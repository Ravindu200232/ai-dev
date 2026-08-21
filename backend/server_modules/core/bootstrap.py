# Shared startup state and process setup.
"""AgentForge Server — HTTP:7824 | WebSocket:7825."""
import atexit
import base64
import signal
import sys, json, asyncio, logging, threading, time, re, socket, subprocess, os, textwrap, urllib3, uuid, io
urllib3.disable_warnings()


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit

import requests

try:
    import websockets
except ImportError:
    subprocess.run([sys.executable,"-m","pip","install","websockets",
                    "--break-system-packages","-q"])
    import websockets

sys.path.insert(0, str(Path(__file__).parent))


sys.path.insert(0, str(Path(__file__).parent / "srs-agent"))
from agents.refiner  import RefinerAgent
from agents.builder  import BuilderAgent, set_stream_callback
from agents.tester   import TesterAgent, set_emit as set_tester_emit
from agents.analyzer import (AnalyzerAgent, AnalyzerReport, Finding,
                             REPAIRABLE_MAJOR)
from agents import nextdocs, themes as themekit, photos
from agents.architect import ArchitectAgent, FileStreamParser
from agents.exports import check_named_imports, check_syntax, syntax_messages
from agents.features import FeaturesAgent, FeatureSpec
from agents.capture import PENCIL_SYSTEM, capture_region
from agents.images import ImageAgent
from agents.seed_keys import family_key, template_keys
from agents.source_guidance import feature_image_requested
from agents.picker import (ELEMENT_EDIT_SYSTEM, ElementResolver, describe,
                           guard_scope, looks_like_addition, looks_like_global,
                           looks_like_page_only, looks_like_removal,
                           looks_like_retext, routes_rendering)
from agents.mongo import MONGO, db_name_for
from agents import cancel
from agents.bugfixer import BugFixerAgent
from agents.commands import CommandRunner
from agents.workspace import WorkspaceTools, TOOL_HELP
from agents.agent_memory import AgentMemory, memory_for
from agents.build_cache import already_green, clear as clear_build_state, mark_green
from qa_agent import (E2EAgent, AgenticE2EDebugger, DebugNotebook,
                      FileSnapshot, QASession, TestHarness, TestFailure,
                      UnitTestAuthor, VitestRunner, select_targets)
from qa_agent.e2e import KIND_SELECTOR
from qa_agent.e2e_progress import (
    failure_signature as _e2e_failure_signature,
    failure_severity as _e2e_failure_severity,
    measure_progress as _e2e_progress,
    normalize_message as _e2e_norm_message,
    extend_round_budget as _e2e_extend_budget,
    stop_after_no_progress as _e2e_stop_no_progress,
    MIN_REPAIR_ROUNDS as E2E_MIN_FIX,
)
from agents.ollama_client import is_transient, with_retry
from agents.ollama_client import (OllamaClient, is_cloud_model, max_context,
                                  get_local_host, load_settings, save_settings,
                                  set_default_client)
import shutil
import copy

def _maybe_set_playwright_env():

    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and os.environ.get("PLAYWRIGHT_NODEJS_PATH"):
        return

    exe = Path(sys.argv[0]).resolve()

    resources = exe.parent.parent

    pw = resources / "ms-playwright"
    node = resources / "node" / "bin" / "node"

    if pw.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(pw))
        os.environ.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")

    if node.exists():
        os.environ.setdefault("PLAYWRIGHT_NODEJS_PATH", str(node))

_maybe_set_playwright_env()
def resolve_node_binaries():

    if getattr(sys, "frozen", False):
        exe_path = Path(sys.argv[0]).resolve()
        resources_dir = exe_path.parent.parent

        node_bin_dir = resources_dir / "node" / "bin"

        npm_path = node_bin_dir / "npm"
        node_path = node_bin_dir / "node"

        if npm_path.exists() and node_path.exists():
            return str(npm_path), str(node_path)

    return shutil.which("npm") or "npm", shutil.which("node") or "node"


NPM_BIN, NODE_BIN = resolve_node_binaries()

print("DEBUG: sys.executable =", sys.executable)
print("DEBUG: Using NPM_BIN  =", NPM_BIN)
print("DEBUG: Using NODE_BIN =", NODE_BIN)


node_dir = str(Path(NODE_BIN).parent)
if node_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{node_dir}:{os.environ.get('PATH','')}"

if hasattr(sys, "_MEIPASS"):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent

print(f"DEBUG: BASE_DIR = {BASE_DIR}")
print(f"DEBUG: UI_DIR = {BASE_DIR / 'ui'}")

def _projects_dir() -> Path:
    """Where the generated apps live."""
    raw = os.environ.get("AGENTFORGE_PROJECTS", "").strip()
    if not raw:
        return BASE_DIR / "production-ready"
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = BASE_DIR / p
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"⚠️  AGENTFORGE_PROJECTS={raw!r} cannot be used ({e}) — "
              f"falling back to production-ready/ beside the repo")
        return BASE_DIR / "production-ready"
    return p


PROD_DIR = _projects_dir()
if PROD_DIR != BASE_DIR / "production-ready":
    print(f"📁 projects live at {PROD_DIR} (AGENTFORGE_PROJECTS)")
elif "OneDrive" in str(PROD_DIR):
    print("⚠️  production-ready/ is inside OneDrive — every build's node_modules "
          "gets synced and npm install runs several times slower. Set "
          "AGENTFORGE_PROJECTS to a folder outside OneDrive "
          "(e.g. C:\\AgentForge\\projects) and restart.")
LOGS_DIR = BASE_DIR / "logs"
OLLAMA_URL = get_local_host()
DEFAULT_REFINE = "llama3.1:8b"
DEFAULT_BUILD  = "qwen2.5-coder:14b"
MAX_FIX    = 6


RUNTIME_DEADLINE = 900
DEV_PORT   = 5173
UI_PORT    = 7824
WS_PORT    = 7825


SRS_PORT   = 7826


DEPLOY_PORT = 7834


NEXT_READY_TIMEOUT = 180
NEXT_BUILD_TIMEOUT = 300

for d in [PROD_DIR, LOGS_DIR]: d.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("server")

clients    = set()
MAIN_LOOP  = None
active_vite = {"proc": None, "stderr_lines": []}


ollama = OllamaClient(OLLAMA_URL)
set_default_client(ollama)


def default_agent_model() -> str:
    """Pick a sane agent model when the caller didn't name one: the saved choice."""
    saved = str(load_settings().get("agent_model", "")).strip()
    if saved:
        return saved
    try:
        cloud = ollama.discover().get("cloud") or []
        if cloud:
            return cloud[0]["id"]
    except Exception:
        pass
    return DEFAULT_BUILD


def emit(msg: dict):
    if MAIN_LOOP is None: return
    data = json.dumps(msg, ensure_ascii=False)
    async def _s():
        dead = set()
        for ws in list(clients):
            try: await ws.send(data)
            except: dead.add(ws)
        clients.difference_update(dead)
    asyncio.run_coroutine_threadsafe(_s(), MAIN_LOOP)

def elog(lvl, txt):

    log.info(f"[{lvl}] {txt}")
    emit({"type": "log", "level": lvl, "text": txt})
def estep(s, st):
    if st == "error":
        log.error(f"step {s} failed")
    emit({"type": "step", "step": s, "status": st})
    cancel.check()
def efile(n, sz, c=""):   emit({"type":"file",         "name":n,     "size":sz,   "content":c})
def edetect(t, s):        emit({"type":"detected",     "site_type":t,"strategy":s})
def eprog(lbl, pct):
    emit({"type": "progress", "step": lbl, "pct": pct})
    cancel.check()
def edone(url, proj, preview="/"):

    emit({"type": "done", "url": url, "project": proj, "preview": preview})
def ecancel(detail: dict):

    emit({"type": "cancelled", **(detail or {})})
def eproject(name: str):
    """A project now exists on disk under this name."""
    emit({"type": "project", "project": str(name)})


cancel.log = elog
