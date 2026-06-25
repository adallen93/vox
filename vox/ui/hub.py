"""
UIHub — asyncio-native event router between the vox bg loop and WebSocket clients.

Each connected browser gets its own asyncio.Queue (maxsize=100). broadcast() puts
into all per-connection queues and must be called on the event loop thread (or via
call_soon_threadsafe from a foreign thread). A _ws_sender drain-task per connection
calls await ws.send_str() so the hot loop never blocks.

Overflow policy: delta frames are silently dropped when a queue is full. All other
frame types evict the oldest delta to make room.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from aiohttp.web_ws import WebSocketResponse
    from vox.ui.pty import PtyManager

log = logging.getLogger(__name__)

_NOOP: Callable = lambda *_: None


@dataclass
class UICallbacks:
    on_state_change: Callable[[str], None] = field(default_factory=lambda: _NOOP)
    on_user_text: Callable[[str], None] = field(default_factory=lambda: _NOOP)
    on_assistant_delta: Callable[[str], None] = field(default_factory=lambda: _NOOP)
    on_turn_complete: Callable[[dict], None] = field(default_factory=lambda: _NOOP)
    on_tool: Callable[[str], None] = field(default_factory=lambda: _NOOP)


class UIHub:
    _DEBOUNCE_S = 0.25
    _QUEUE_MAX = 100

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._hotkey_event: asyncio.Event = asyncio.Event()
        self._conn_queues: dict["WebSocketResponse", asyncio.Queue] = {}
        self._ssh_task: asyncio.Task | None = None
        self._pty_mgr: "PtyManager | None" = None
        self._current_state: str = "idle"
        self._last_trigger: float = 0.0

    def set_pty_manager(self, pty_mgr: "PtyManager") -> None:
        self._pty_mgr = pty_mgr

    # ------------------------------------------------------------------
    # Trigger (debounced; called from loop thread or via call_soon_threadsafe)
    # ------------------------------------------------------------------

    def trigger(self) -> None:
        now = time.perf_counter()
        if now - self._last_trigger > self._DEBOUNCE_S:
            self._last_trigger = now
            self._hotkey_event.set()

    # ------------------------------------------------------------------
    # broadcast — must be called on the loop thread
    # ------------------------------------------------------------------

    def broadcast(self, msg: dict) -> None:
        payload = json.dumps(msg)
        is_delta = msg.get("type") == "delta"
        for q in list(self._conn_queues.values()):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                if is_delta:
                    pass  # droppable
                else:
                    # Evict oldest item (likely a delta) and insert
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        log.warning("broadcast: queue still full after eviction, dropping %s", msg.get("type"))

    # ------------------------------------------------------------------
    # WebSocket handler (called from aiohttp route as a coroutine)
    # ------------------------------------------------------------------

    async def handle_ws(self, ws: "WebSocketResponse") -> None:
        from aiohttp import WSMsgType

        q: asyncio.Queue = asyncio.Queue(maxsize=self._QUEUE_MAX)
        self._conn_queues[ws] = q

        sender = asyncio.create_task(self._ws_sender(ws, q))

        # Send hello snapshot
        self.broadcast({"type": "state", "state": self._current_state})

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        log.warning("handle_ws: invalid JSON from client")
                        continue
                    await self._handle_inbound(data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        except Exception:
            log.exception("handle_ws: error reading from client")
        finally:
            # Signal sender to stop
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
            await sender
            self._conn_queues.pop(ws, None)

            # If no connections remain and _ssh_task is running, cancel it
            # (browser closed/refreshed without sending vox_deactivate)
            if not self._conn_queues and self._ssh_task is not None:
                log.info("handle_ws: last connection closed — cancelling ssh task")
                self._ssh_task.cancel()
                self._ssh_task = None

    async def _ws_sender(self, ws: "WebSocketResponse", q: asyncio.Queue) -> None:
        try:
            while True:
                payload = await q.get()
                if payload is None:
                    break
                try:
                    await ws.send_str(payload)
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Inbound message dispatch
    # ------------------------------------------------------------------

    async def _handle_inbound(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "hello":
            self.broadcast({"type": "state", "state": self._current_state})
            self._send_profiles()
        elif t == "trigger":
            self.trigger()
        elif t == "vox_activate":
            await self._activate_vox()
        elif t == "vox_deactivate":
            if self._ssh_task is not None:
                self._ssh_task.cancel()
                self._ssh_task = None
        elif t == "pty_spawn":
            if self._pty_mgr is not None:
                pane_id = self._pty_mgr.spawn(msg.get("profile", {}))
                self.broadcast({"type": "pane_opened", "pane_id": pane_id})
        elif t == "pty_input":
            if self._pty_mgr is not None:
                from base64 import b64decode
                self._pty_mgr.write(msg["pane_id"], b64decode(msg["data"]))
        elif t == "pty_resize":
            if self._pty_mgr is not None:
                self._pty_mgr.resize(msg["pane_id"], msg["cols"], msg["rows"])
        elif t == "pty_close":
            if self._pty_mgr is not None:
                self._pty_mgr.close_pane(msg["pane_id"])

    # ------------------------------------------------------------------
    # Vox session lifecycle
    # ------------------------------------------------------------------

    def _send_profiles(self) -> None:
        from vox.config import DEFAULT_CONFIG
        from vox.ui.profiles import load_profiles
        profiles = load_profiles(DEFAULT_CONFIG.wt_settings_path)
        self.broadcast({"type": "profiles", "profiles": profiles})

    async def _activate_vox(self) -> None:
        if self._ssh_task is not None:
            self._ssh_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._ssh_task), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            self._ssh_task = None

        from vox.__main__ import _ssh_test
        self._ssh_task = asyncio.create_task(
            _ssh_test(
                use_mic=True,
                use_hotkey=True,
                ui_mode=True,
                callbacks=self.make_callbacks(),
                trigger_fn=self.trigger,
                _hotkey_event=self._hotkey_event,
            )
        )
        self._ssh_task.add_done_callback(self._on_ssh_task_done)

    def _on_ssh_task_done(self, task: "asyncio.Task") -> None:
        self._ssh_task = None
        exc = task.exception() if not task.cancelled() else None
        if exc is not None:
            log.exception("vox ssh task died with exception", exc_info=exc)
            print(f"[vox] ssh task died: {exc}", flush=True)
        self._current_state = "idle"
        self.broadcast({"type": "state", "state": "idle"})
        self.broadcast({"type": "vox_ended"})

    # ------------------------------------------------------------------
    # Callbacks for _ssh_test
    # ------------------------------------------------------------------

    def make_callbacks(self) -> UICallbacks:
        def _on_state(s: str) -> None:
            self._current_state = s
            self.broadcast({"type": "state", "state": s})

        return UICallbacks(
            on_state_change=_on_state,
            on_user_text=lambda t: self.broadcast({"type": "user_text", "text": t}),
            on_assistant_delta=lambda t: self.broadcast({"type": "delta", "text": t}),
            on_turn_complete=lambda d: self.broadcast({"type": "turn_complete", **d}),
            on_tool=lambda n: self.broadcast({"type": "tool", "name": n}),
        )
