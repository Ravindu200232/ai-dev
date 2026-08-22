"""Every seam between the deployment agent and AgentForge lives here."""
from __future__ import annotations

import json
import sys
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parent
PKG_ROOT = AGENT_ROOT.parent
LOCODE_ROOT = PKG_ROOT.parent

for _root in (AGENT_ROOT, PKG_ROOT, LOCODE_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

def _prod_dir(root):
    """The same rule server.py uses: AGENTFORGE_PROJECTS if set."""
    import os
    raw = os.environ.get("AGENTFORGE_PROJECTS", "").strip()
    if not raw:
        return root / "production-ready"
    p = Path(raw).expanduser()
    return p if p.is_absolute() else root / p


PROD_DIR = _prod_dir(LOCODE_ROOT)


DEPLOY_DATA = PROD_DIR / ".deploy"


DEPLOY_PORT = 7834


DEFAULT_DEPLOY_MODEL = "gemma4:31b-cloud"


def agentforge_settings() -> dict:
    """Read ~/.agentforge/settings.json."""
    try:
        from agents.core.ollama_client import load_settings
        return load_settings()
    except Exception:
        try:
            path = Path.home() / ".agentforge" / "settings.json"
            return json.loads(path.read_text(encoding="utf-8"))
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


def route(model: str) -> tuple[str, dict]:
    """(base_url, headers) for this model, using AgentForge's routing."""
    try:
        from agents.core.ollama_client import _default_client
        return _default_client().route(model)
    except Exception:
        return "http://localhost:11434", {"Content-Type": "application/json"}


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
