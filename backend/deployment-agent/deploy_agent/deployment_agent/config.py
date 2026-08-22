from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "DeploymentAgent"
ARTIFACT_SCHEMA_VERSION = 3
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 7834
WS_PORT = 7835
OLLAMA_URL = os.environ.get("DEPLOYMENT_AGENT_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("DEPLOYMENT_AGENT_MODEL", "gemma4:31b-cloud")
# Thinking is a Builder-only Studio option. Deployment is always off.
OLLAMA_THINK = False


OLLAMA_NUM_PREDICT = int(os.environ.get("DEPLOYMENT_AGENT_OLLAMA_NUM_PREDICT", "4096"))
OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("DEPLOYMENT_AGENT_OLLAMA_TIMEOUT_SECONDS", "180"))


GIT_TIMEOUT_SECONDS = int(os.environ.get("DEPLOYMENT_AGENT_GIT_TIMEOUT_SECONDS", "600"))


def app_data_dir() -> Path:
    """Where state.db and the staged worktrees live."""
    explicit = os.environ.get("DEPLOYMENT_AGENT_DATA_DIR", "").strip()
    if explicit:
        root = Path(explicit)
    else:
        try:
            from deploy_agent.bridge import DEPLOY_DATA
            root = DEPLOY_DATA
        except Exception:                                       # noqa: BLE001
            base = os.environ.get("LOCALAPPDATA")
            if base:
                root = Path(base) / APP_NAME
            else:
                root = Path.home() / ".deployment-agent"
    root.mkdir(parents=True, exist_ok=True)
    return root


DATA_DIR = app_data_dir()
RUNS_DIR = DATA_DIR / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "state.db"

SKIP_DIRS = {
    ".git",
    ".next",
    ".cache",
    ".turbo",
    ".vercel",
    "node_modules",
    "dist",
    "build",
    "out",
    "coverage",
}

SOURCE_EXTENSIONS = {
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".json",
    ".css",
    ".md",
    ".yml",
    ".yaml",
}
