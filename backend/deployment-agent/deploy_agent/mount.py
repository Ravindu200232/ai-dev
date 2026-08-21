"""Run the deployment agent inside AgentForge's process."""
from __future__ import annotations

import logging
import os

from .bridge import DEPLOY_PORT

log = logging.getLogger("deploy")


def serve(port: int = DEPLOY_PORT, host: str = "127.0.0.1") -> None:
    """Block on the HTTP server."""

    from .bridge import DEPLOY_DATA
    DEPLOY_DATA.mkdir(parents=True, exist_ok=True)
    os.environ["DEPLOYMENT_AGENT_DATA_DIR"] = str(DEPLOY_DATA)

    os.environ["DEPLOYMENT_AGENT_NO_BROWSER"] = "1"

    import dfserver

    try:
        dfserver.start_reconciliation()
    except Exception as e:                                      # noqa: BLE001
        log.warning(f"reconciliation skipped: {type(e).__name__}: {e}")

    dfserver.start_http(port=port, host=host)
