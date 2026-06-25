"""
PtyManager / PtySession — ConPTY-backed terminal panes via pywinpty.

Each PtySession owns one winpty process and a reader thread that forwards
output to the asyncio loop via call_soon_threadsafe → broadcast_fn.
"""
import asyncio
import base64
import logging
import threading
import uuid
from typing import Callable

log = logging.getLogger(__name__)


class PtySession:
    def __init__(
        self,
        pane_id: str,
        cmdline: list[str],
        loop: asyncio.AbstractEventLoop,
        broadcast_fn: Callable[[dict], None],
    ) -> None:
        self.pane_id = pane_id
        self._loop = loop
        self._broadcast = broadcast_fn
        self._running = False

        import winpty
        print(f"[pty] spawning {cmdline} (pane {pane_id})", flush=True)
        self._pty = winpty.PtyProcess.spawn(cmdline, backend=winpty.Backend.ConPTY)
        self._running = True
        print(f"[pty] spawn OK, pid={self._pty.pid if hasattr(self._pty, 'pid') else '?'}", flush=True)

        self._thread = threading.Thread(
            target=self._reader_thread,
            name=f"pty-reader-{pane_id}",
            daemon=True,
        )
        self._thread.start()

    def write(self, data: bytes) -> None:
        if self._running:
            try:
                self._pty.write(data.decode("utf-8", errors="replace"))
            except Exception as exc:
                log.warning("pty write error (pane %s): %s", self.pane_id, exc)

    def resize(self, cols: int, rows: int) -> None:
        if self._running:
            try:
                self._pty.setwinsize(rows, cols)
            except Exception as exc:
                log.warning("pty resize error (pane %s): %s", self.pane_id, exc)

    def close(self) -> None:
        self._running = False
        try:
            self._pty.terminate()
        except Exception:
            pass
        self._thread.join(timeout=2.0)

    def _reader_thread(self) -> None:
        print(f"[pty] reader thread started for pane {self.pane_id}", flush=True)
        chunks = 0
        while self._running:
            try:
                data = self._pty.read(4096)  # blocks; returns str from pywinpty
                chunks += 1
                if chunks <= 5:
                    print(f"[pty] read chunk #{chunks} ({len(data)} chars) from pane {self.pane_id}: {repr(data[:60])}", flush=True)
                raw = data.encode("utf-8", errors="replace")
                msg = {
                    "type": "pty_output",
                    "pane_id": self.pane_id,
                    "data": base64.b64encode(raw).decode(),
                }
                self._loop.call_soon_threadsafe(self._broadcast, msg)
            except (EOFError, OSError) as exc:
                print(f"[pty] reader EOF/OSError on pane {self.pane_id}: {exc}", flush=True)
                break
            except Exception as exc:
                print(f"[pty] reader error on pane {self.pane_id}: {exc}", flush=True)
                break

        print(f"[pty] reader thread exiting for pane {self.pane_id} (chunks={chunks})", flush=True)
        self._running = False
        self._loop.call_soon_threadsafe(
            self._broadcast, {"type": "pty_closed", "pane_id": self.pane_id}
        )


class PtyManager:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        broadcast_fn: Callable[[dict], None],
    ) -> None:
        self._loop = loop
        self._broadcast = broadcast_fn
        self._panes: dict[str, PtySession] = {}

    def spawn(self, profile: dict) -> str:
        import os
        pane_id = uuid.uuid4().hex[:8]
        cmdline_str = os.path.expandvars(profile.get("commandline", "powershell.exe"))
        cmdline = cmdline_str.split()
        print(f"[pty] spawn request: profile={profile!r} cmdline={cmdline}", flush=True)
        try:
            session = PtySession(pane_id, cmdline, self._loop, self._broadcast)
            self._panes[pane_id] = session
        except Exception as exc:
            print(f"[pty] spawn FAILED for {cmdline}: {exc}", flush=True)
            return ""
        return pane_id

    def write(self, pane_id: str, data: bytes) -> None:
        pane = self._panes.get(pane_id)
        if pane:
            pane.write(data)
        else:
            print(f"[pty] write to unknown pane {pane_id!r}", flush=True)

    def resize(self, pane_id: str, cols: int, rows: int) -> None:
        pane = self._panes.get(pane_id)
        if pane:
            pane.resize(cols, rows)

    def close_pane(self, pane_id: str) -> None:
        pane = self._panes.pop(pane_id, None)
        if pane:
            pane.close()

    def close_all(self) -> None:
        for pane in list(self._panes.values()):
            pane.close()
        self._panes.clear()
