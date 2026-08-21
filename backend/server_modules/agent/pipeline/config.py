"""Configuration and logging for the legacy folder-watcher pipeline."""
from __future__ import annotations

import logging
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parent.parent
IDEAS_DIR = BASE_DIR / "ideas"
PROD_DIR = BASE_DIR / "production-ready"
LOGS_DIR = BASE_DIR / "logs"
OLLAMA_URL = "http://localhost:11434"
REFINE_MODEL = "llama3.1:8b"
BUILD_MODEL = "qwen2.5-coder:14b"
MAX_FIX = 2
DEV_PORT = 5173

LOGS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "pipeline.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("pipeline")


def ensure_directories() -> None:
    """Create the three folders the watcher owns."""
    for folder in (IDEAS_DIR, PROD_DIR, LOGS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
