"""Every seam between the SRS agent and AgentForge lives here.

The SRS agent runs as a subprocess of the Go backend, so these seams read the
same settings file the backend writes, plus the environment the parent sets.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


LOCODE_ROOT = Path(__file__).resolve().parents[2]


if str(LOCODE_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCODE_ROOT))


def _prod_dir(root):
    """The same rule the backend uses: AGENTFORGE_PROJECTS if set."""
    raw = os.environ.get("AGENTFORGE_PROJECTS", "").strip()
    if not raw:
        return root / "production-ready"
    p = Path(raw).expanduser()
    return p if p.is_absolute() else root / p


PROD_DIR = _prod_dir(LOCODE_ROOT)


SRS_STAGING = PROD_DIR / ".srs"


SRS_PORT = 7826


DEFAULT_SRS_MODEL = "gemma4:31b-cloud"

SETTINGS_PATH = Path.home() / ".agentforge" / "settings.json"
DEFAULT_LOCAL_HOST = "http://localhost:11434"
CLOUD_HOST = "https://ollama.com"
LOCAL_DEFAULT_CTX = 16384
CLOUD_DEFAULT_CTX = 131072


def agentforge_settings() -> dict:
    """Read ~/.agentforge/settings.json — the file the backend owns."""
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
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


def _is_cloud(model: str) -> bool:
    m = (model or "").strip().lower()
    return m.endswith("-cloud") or m.endswith(":cloud")


def _local_host() -> str:
    host = (os.environ.get("OLLAMA_HOST", "").strip()
            or str(agentforge_settings().get("ollama_host", "")).strip()
            or DEFAULT_LOCAL_HOST)
    if not host.startswith("http"):
        host = f"http://{host}"
    return host.rstrip("/")


def route(model: str) -> tuple[str, dict]:
    """(base_url, headers) for this model."""
    headers = {"Content-Type": "application/json"}
    key = (os.environ.get("OLLAMA_API_KEY", "").strip()
           or str(agentforge_settings().get("ollama_api_key", "")).strip())
    if _is_cloud(model) and key:
        headers["Authorization"] = f"Bearer {key}"
        return CLOUD_HOST, headers
    return _local_host(), headers


def num_ctx(model: str) -> int:
    """Context window to request — cloud models get the larger window."""
    if _is_cloud(model):
        return CLOUD_DEFAULT_CTX
    override = (os.environ.get("AGENTFORGE_NUM_CTX", "").strip()
                or str(agentforge_settings().get("local_num_ctx", "")).strip())
    return max(4096, int(override)) if override.isdigit() else LOCAL_DEFAULT_CTX


SRS_DB = "agentforge_srs"


def mongo_uri() -> str:
    """AgentForge's mongod. The parent process passes the port it settled on."""
    override = os.environ.get("AGENTFORGE_MONGO_URI", "").strip()
    if override:
        return override
    port = os.environ.get("AGENTFORGE_MONGO_PORT", "").strip() or "27017"
    return f"mongodb://127.0.0.1:{port}/{SRS_DB}"
