"""The SRS and deployment services, started alongside the forge server.

Each lives in its own tree with its own dependencies. Neither is required:
if one cannot be imported the server says so once and carries on, because a
missing sidecar is not a reason to be unable to build anything.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger("forge.server.sidecars")

BASE_DIR = Path(__file__).resolve().parents[2]

SIDECARS = (
    ("SRS", "srs-agent", "srs_agent", "AGENTFORGE_SRS_PORT", 7827),
    ("Deploy", "deployment-agent", "deploy_agent", "AGENTFORGE_DEPLOY_PORT", 7828),
)

STATUS = {}


def _serve(label: str, folder: str, module: str, port: int) -> None:
    """Import the sidecar and block in its own server. Runs in a thread."""
    root = BASE_DIR / folder
    if not root.is_dir():
        STATUS[label] = {"state": "absent", "port": port}
        return
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        mount = __import__(f"{module}.mount", fromlist=["mount"])
    except Exception as e:                                         # noqa: BLE001
        STATUS[label] = {"state": "unavailable", "port": port,
                         "error": f"{type(e).__name__}: {e}"}
        log.warning(f"{label} sidecar unavailable — {STATUS[label]['error']}")
        return
    STATUS[label] = {"state": "starting", "port": port}
    try:
        mount.serve(port=port)
        STATUS[label] = {"state": "stopped", "port": port}
    except Exception as e:                                         # noqa: BLE001
        STATUS[label] = {"state": "crashed", "port": port,
                         "error": f"{type(e).__name__}: {e}"}
        log.warning(f"{label} sidecar stopped — {STATUS[label]['error']}")


def start_all() -> None:
    """Start every sidecar that is present, each in a daemon thread."""
    for label, folder, module, env, default in SIDECARS:
        port = int(os.environ.get(env, default))
        threading.Thread(target=_serve, daemon=True,
                         args=(label, folder, module, port)).start()


def status() -> dict:
    """What each sidecar is doing, for the studio to show."""
    return {label: STATUS.get(label, {"state": "off"})
            for label, *_ in SIDECARS}
