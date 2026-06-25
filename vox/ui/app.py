"""
vox UI entry point — pywebview window + aiohttp WebSocket server.

Thread model
------------
MAIN THREAD   pywebview window (webview.start(), blocking). No asyncio calls.
BG "vox-loop" asyncio SelectorEventLoop: aiohttp HTTP/WS server, UIHub,
              _heartbeat, optional _ssh_task spawned on vox_activate.
"""
import asyncio
import logging
import threading

import webview
from aiohttp import web

from vox.config import Config

log = logging.getLogger(__name__)


def run_ui(cfg: Config) -> None:
    ready: threading.Event = threading.Event()
    err_ref: list = []
    loop_ref: list = []

    def _bg_thread() -> None:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_ref.append(loop)

        from vox.ui.hub import UIHub
        from vox.ui.server import make_app
        from vox.ui.pty import PtyManager

        hub = UIHub(loop)
        pty_mgr = PtyManager(loop, hub.broadcast)
        hub.set_pty_manager(pty_mgr)

        runner: web.AppRunner | None = None

        async def _setup() -> None:
            nonlocal runner
            app = await make_app(hub)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, cfg.ui_host, cfg.ui_port)
            await site.start()
            log.info("aiohttp listening on http://%s:%d/", cfg.ui_host, cfg.ui_port)

        async def _heartbeat() -> None:
            import time
            tick = 0
            while True:
                await asyncio.sleep(0.05)
                tick += 1
                if tick % 200 == 0:
                    log.debug("heartbeat #%d @ %.3f", tick, time.perf_counter())

        try:
            loop.run_until_complete(_setup())
            ready.set()  # HTTP is listening — unblock main thread
            loop.create_task(_heartbeat())
            loop.run_forever()  # returns when loop.stop() is called
        except Exception as exc:
            err_ref.append(exc)
            log.exception("vox-loop startup error")
        finally:
            if not ready.is_set():
                ready.set()
            if runner is not None:
                try:
                    loop.run_until_complete(runner.cleanup())
                except Exception:
                    log.exception("runner cleanup error")
            pty_mgr.close_all()
            loop.close()

    t = threading.Thread(target=_bg_thread, name="vox-loop", daemon=True)
    t.start()

    if not ready.wait(timeout=60.0):
        raise SystemExit("vox bg loop failed to start within 60 s")

    if err_ref:
        raise SystemExit(f"vox bg loop error: {err_ref[0]}") from err_ref[0]

    webview.create_window(
        "vox",
        url=f"http://{cfg.ui_host}:{cfg.ui_port}/",
    )
    webview.start()  # blocks until window closed

    # Window closed — stop the bg loop cleanly
    if loop_ref:
        loop_ref[0].call_soon_threadsafe(loop_ref[0].stop)
    t.join(timeout=10.0)
