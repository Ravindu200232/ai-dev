"""Where things live, and what the studio needs to know about them."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..ollama import OllamaClient, load_settings, save_settings

BASE_DIR = Path(__file__).resolve().parents[2]
UI_PORT = int(os.environ.get("AGENTFORGE_UI_PORT", "7824"))
WS_PORT = int(os.environ.get("AGENTFORGE_WS_PORT", "7825"))
STUDIO_PORT = int(os.environ.get("AGENTFORGE_STUDIO_PORT", "3000"))

SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".md"}
MAX_LISTED_FILES = 400
MAX_FILE_BYTES = 200_000

_CLIENT = OllamaClient()


def projects_dir() -> Path:
    """Where built apps are written."""
    override = os.environ.get("AGENTFORGE_PROJECTS", "").strip()
    root = Path(override) if override else BASE_DIR / "production-ready"
    root.mkdir(parents=True, exist_ok=True)
    return root


def bind_host() -> str:
    return os.environ.get("AGENTFORGE_HOST", "127.0.0.1")


def default_model() -> str:
    """The model a build uses when the request does not name one."""
    return (os.environ.get("AGENTFORGE_MODEL", "").strip()
            or str(load_settings().get("model", "")).strip()
            or "qwen3-coder:480b-cloud")


def projects() -> list:
    """Every project on disk, newest first."""
    out = []
    for path in projects_dir().iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({"name": path.name, "modified": int(stat.st_mtime),
                    "has_app": (path / "app").is_dir()})
    return sorted(out, key=lambda p: -p["modified"])


def project_files(name: str) -> dict:
    """`{relative path: contents}` for one project's source."""
    from ..tools.paths import ignored

    root = projects_dir() / name
    if not root.is_dir():
        return {}
    found = {}
    for path in sorted(root.rglob("*")):
        if len(found) >= MAX_LISTED_FILES:
            break
        if (not path.is_file() or path.suffix not in SOURCE_SUFFIXES
                or ignored(path, root)):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            found[path.relative_to(root).as_posix()] = path.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
    return found


def models(refresh: bool = True) -> dict:
    """Everything the studio's model pickers render."""
    try:
        return _CLIENT.catalog(refresh=refresh)
    except Exception as e:                                         # noqa: BLE001
        return {"cloud": [], "local_models": [], "local": [],
                "cloud_enabled": False, "error": str(e)}


def settings() -> dict:
    """Settings, with the API key reported as present rather than echoed."""
    saved = dict(load_settings())
    key = str(saved.pop("ollama_api_key", "") or "")
    saved["has_api_key"] = bool(key)
    return saved


def update_settings(data: dict) -> dict:
    save_settings({k: v for k, v in (data or {}).items() if k != "has_api_key"})
    return settings()


def write_source(name: str, rel: str, content: str) -> str:
    """Save one file the user edited in the studio."""
    from ..tools.paths import resolve

    target = resolve(projects_dir() / name, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(content or ""), encoding="utf-8")
    return target.relative_to(projects_dir() / name).as_posix()


def delete_project(name: str) -> bool:
    """Remove a project and everything in it."""
    import shutil

    from ..tools.paths import resolve

    target = resolve(projects_dir(), name)
    if target == projects_dir() or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def dump(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")
