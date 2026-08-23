"""Every seam between the deployment agent and AgentForge lives here.

The deployment agent runs as a subprocess of the Go backend, so these seams read
the same settings file the backend writes, plus the environment the parent sets.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parent
PKG_ROOT = AGENT_ROOT.parent
LOCODE_ROOT = PKG_ROOT.parent

for _root in (AGENT_ROOT, PKG_ROOT, LOCODE_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

def _prod_dir(root):
    """The same rule the backend uses: AGENTFORGE_PROJECTS if set."""
    raw = os.environ.get("AGENTFORGE_PROJECTS", "").strip()
    if not raw:
        return root / "production-ready"
    p = Path(raw).expanduser()
    return p if p.is_absolute() else root / p


PROD_DIR = _prod_dir(LOCODE_ROOT)


DEPLOY_DATA = PROD_DIR / ".deploy"


DEPLOY_PORT = 7834


DEFAULT_DEPLOY_MODEL = "gemma4:31b-cloud"

SETTINGS_PATH = Path.home() / ".agentforge" / "settings.json"
DEFAULT_LOCAL_HOST = "http://localhost:11434"
CLOUD_HOST = "https://ollama.com"


def agentforge_settings() -> dict:
    """Read ~/.agentforge/settings.json — the file the backend owns."""
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def deploy_model() -> str:
    """The model that writes the deployment plan."""
    settings = agentforge_settings()
    for key in ("deploy_model", "agent_model"):
        value = str(settings.get(key, "")).strip()
        if value:
            return value
    return DEFAULT_DEPLOY_MODEL


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


def ollama_client(model: str = ""):
    """A planner client pointed at whichever Ollama can serve the chosen model."""
    from dfagents.planner import OllamaClient

    tag = model or deploy_model()
    base, headers = route(tag)
    return OllamaClient(base_url=base.rstrip("/"), model=tag, headers=headers)


def ollama_probe() -> dict:
    """Is the chosen model reachable?"""
    import requests

    tag = deploy_model()
    base, headers = route(tag)
    try:
        r = requests.get(f"{base.rstrip('/')}/api/tags", headers=headers, timeout=6)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception as e:                                      # noqa: BLE001
        return {"ready": False, "model_ready": False, "model": tag,
                "error": f"{type(e).__name__}: {e}"}

    cloud = tag.endswith(":cloud") or tag.endswith("-cloud")
    return {"ready": True, "model_ready": cloud or tag in names, "model": tag}


def project_dir(project: str) -> Path:
    """The absolute path AgentForge would hand to /api/runs/analyze."""
    return PROD_DIR / project


def free_tier_only() -> bool:
    """Keep the EC2 instance inside AWS's free tier."""
    value = agentforge_settings().get("aws_free_tier", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)
