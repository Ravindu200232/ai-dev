"""Every seam between the SRS agent and AgentForge lives here."""
from __future__ import annotations

import json
import sys
from pathlib import Path


LOCODE_ROOT = Path(__file__).resolve().parents[2]


if str(LOCODE_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCODE_ROOT))

def _prod_dir(root):
    """The same rule server.py uses: AGENTFORGE_PROJECTS if set."""
    import os
    raw = os.environ.get("AGENTFORGE_PROJECTS", "").strip()
    if not raw:
        return root / "production-ready"
    p = Path(raw).expanduser()
    return p if p.is_absolute() else root / p


PROD_DIR = _prod_dir(LOCODE_ROOT)


SRS_STAGING = PROD_DIR / ".srs"


SRS_PORT = 7826


DEFAULT_SRS_MODEL = "gemma4:31b-cloud"


def agentforge_settings() -> dict:
    """Read ~/.agentforge/settings.json."""
    try:
        from agents.ollama_client import load_settings
        return load_settings()
    except Exception:
        try:
            path = Path.home() / ".agentforge" / "settings.json"
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}


def srs_model() -> str:
    """The model the SRS should use when the caller did not name one."""
    settings = agentforge_settings()
    for key in ("srs_model", "agent_model"):
        value = str(settings.get(key, "")).strip()
        if value:
            return value
    return DEFAULT_SRS_MODEL


def route(model: str) -> tuple[str, dict]:
    """(base_url, headers) for this model, using AgentForge's routing."""
    try:
        from agents.ollama_client import _default_client
        return _default_client().route(model)
    except Exception:
        return "http://localhost:11434", {"Content-Type": "application/json"}


def num_ctx(model: str) -> int:
    """Context window to request — measured per model, not a fixed 8192."""
    try:
        from agents.ollama_client import max_context
        return int(max_context(model))
    except Exception:
        return 8192


SRS_DB = "agentforge_srs"


def mongo_uri() -> str:
    """AgentForge's mongod, on whatever port it settled on."""
    try:
        from agents.mongo import MONGO, get_uri_override
        override = get_uri_override()
        if override:
            return override
        return f"mongodb://127.0.0.1:{MONGO.port}/{SRS_DB}"
    except Exception:
        return f"mongodb://127.0.0.1:27017/{SRS_DB}"
