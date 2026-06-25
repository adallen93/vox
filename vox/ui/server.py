from pathlib import Path

from aiohttp import web

from vox.ui.hub import UIHub

_STATIC = Path(__file__).parent.parent / "ui_web"


async def make_app(hub: UIHub) -> web.Application:
    app = web.Application()
    app.router.add_get("/", lambda r: web.FileResponse(_STATIC / "index.html"))
    app.router.add_get("/ws", _make_ws_handler(hub))
    app.router.add_static("/static", _STATIC)
    return app


def _make_ws_handler(hub: UIHub):
    async def _ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await hub.handle_ws(ws)
        return ws

    return _ws_handler
