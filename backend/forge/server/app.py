"""Starting the two servers the studio talks to."""
from __future__ import annotations

import asyncio
import logging
import threading
from http.server import ThreadingHTTPServer

from . import events, state, ws
from .http import Handler

log = logging.getLogger("forge.server")

BANNER = """
{line}
  ⚒️  AgentForge — forge pipeline
  🌐 Studio      →  http://{host}:{ui}
  🔌 WebSocket   →  ws://{host}:{socket}
  📁 Projects    →  {projects}
{line}
"""


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve_http() -> None:
    """Blocking. Run me in a daemon thread."""
    try:
        Server((state.bind_host(), state.UI_PORT), Handler).serve_forever()
    except Exception as e:                                         # noqa: BLE001
        log.error(f"HTTP server stopped: {e}")


async def main() -> None:
    """Run until interrupted."""
    import websockets

    events.use_loop(asyncio.get_running_loop())
    threading.Thread(target=serve_http, daemon=True).start()
    print(BANNER.format(line="━" * 46, host=state.bind_host(),
                        ui=state.UI_PORT, socket=state.WS_PORT,
                        projects=state.projects_dir()))
    async with websockets.serve(ws.serve, state.bind_host(), state.WS_PORT):
        await asyncio.Future()


def run() -> None:
    """The entry point `server.py` calls."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⛔ Stopped.")
