"""The JSON API the studio calls. One function per route."""
from __future__ import annotations

import logging
import subprocess
import sys

from . import runs, state

log = logging.getLogger("forge.server.api")

PREFIX = "/__agentforge/api"


def get(path: str) -> tuple:
    """`(status, payload)` for a GET. 404 when nothing serves this path."""
    if path == "/projects":
        return 200, {"projects": state.projects()}
    if path == "/models":
        return 200, state.models()
    if path == "/settings":
        return 200, state.settings()
    if path == "/build/status":
        return 200, {"running": runs.busy(), "project": runs.CURRENT["project"]}
    if path.startswith("/files/"):
        name = path[len("/files/"):]
        files = state.project_files(name)
        if not files:
            return 404, {"error": f"{name} has no readable source"}
        return 200, {"project": name, "files": files}
    return 404, {"error": f"no route for GET {path}"}


def post(path: str, body: dict) -> tuple:
    """`(status, payload)` for a POST."""
    if path == "/settings":
        return 200, state.update_settings(body)

    if path == "/save-file":
        project = str(body.get("project") or "")
        try:
            written = state.write_source(project, str(body.get("path") or ""),
                                         body.get("content") or "")
        except Exception as e:                                     # noqa: BLE001
            return 400, {"error": str(e)}
        return 200, {"saved": written}

    if path == "/delete-project":
        name = str(body.get("project") or "")
        if runs.CURRENT["project"] == name:
            return 409, {"error": "that project is being built right now"}
        return (200, {"deleted": name}) if state.delete_project(name) else \
               (404, {"error": f"no project called {name}"})

    if path == "/build/cancel":
        return 200, {"cancelled": runs.cancel()}

    if path.startswith("/open/"):
        return open_project(path[len("/open/"):])

    return 404, {"error": f"no route for POST {path}"}


def open_project(name: str) -> tuple:
    """Reveal a project in the desktop file manager, where there is one."""
    target = state.projects_dir() / name
    if not target.is_dir():
        return 404, {"error": f"no project called {name}"}
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
    try:
        subprocess.Popen([opener, str(target)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return 200, {"path": str(target), "opened": False, "error": str(e)}
    return 200, {"path": str(target), "opened": True}
