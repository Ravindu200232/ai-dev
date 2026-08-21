"""Run the SRS API inside AgentForge's process."""
from __future__ import annotations

import logging
import time

from .bridge import SRS_PORT

log = logging.getLogger("srs")


STORE_CHECK_EVERY_S = 5.0


def serve(port: int = SRS_PORT, host: str = "127.0.0.1") -> None:
    """Block on uvicorn. Intended as a daemon thread's target."""
    import uvicorn

    from . import jobs
    from .app.db import ensure_store
    from .app.main import app

    jobs.attach(app)

    last_checked = [0.0]

    @app.middleware("http")
    async def keep_the_store_alive(request, call_next):
        """Give the store a chance to heal before the request needs it."""
        now = time.monotonic()
        if now - last_checked[0] > STORE_CHECK_EVERY_S:
            last_checked[0] = now
            try:
                await ensure_store()
            except Exception as e:                          # noqa: BLE001
                log.warning(f"store check failed: {type(e).__name__}: {e}")
        return await call_next(request)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,

        timeout_keep_alive=300,
    )
    uvicorn.Server(config).run()
