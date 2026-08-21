"""The shell that lets tests be written while the app is still being generated."""
import json
import logging
import queue
import re
import threading
import time
from pathlib import Path

from agents.commands import CommandRunner
from agents.ollama_client import is_cloud_model

from .spec import MAX_PER_PHASE, QAReport, select_targets


QA_AUTHOR_WORKERS = 4

log = logging.getLogger("qa.session")


QA_MAX_COMMANDS = 200


TEST_PATH_RE = re.compile(r"^tests/unit/[A-Za-z0-9][A-Za-z0-9._/-]*\.test\.jsx?$")

QA_DIR = ".agentforge/qa"
MANIFEST = f"{QA_DIR}/manifest.json"


HELPER_HOMES = {

    "oid": "request.js", "postJson": "request.js", "getJson": "request.js",
    "postForm": "request.js", "patchJson": "request.js",
    "putJson": "request.js", "deleteJson": "request.js",

    "__seed": "mongoMock.js", "__reset": "mongoMock.js", "__all": "mongoMock.js",
    "serialize": "mongoMock.js",

    "__setUser": "authMock.js",

    "__setPath": "navMock.js", "__resetNav": "navMock.js",
}


HELPER_SPIES = {
    "push": "navMock.js", "replace": "navMock.js", "back": "navMock.js",
    "forward": "navMock.js", "refresh": "navMock.js", "prefetch": "navMock.js",
    "redirect": "navMock.js", "notFound": "navMock.js",
}
_IMPORT_LINE_RE = re.compile(r"^\s*import\b.*$", re.M)


STUBBED_MODULES = ("next/link", "lucide-react")
_VI_MOCK_RE = re.compile(
    r"vi\s*\.\s*mock\s*\(\s*['\"](next/link|lucide-react)['\"]")


_NEEDS = (

    (re.compile(r"\buse(?:Router|Pathname|SearchParams|Params"
                r"|SelectedLayoutSegment)\s*\(|from\s*['\"]next/navigation['\"]"),
     "next/navigation", "navMock.js"),
    (re.compile(r"from\s*['\"]@/lib/mongodb['\"]|require\(['\"]@/lib/mongodb['\"]"),
     "@/lib/mongodb", "mongoMock.js"),
    (re.compile(r"from\s*['\"]@/lib/auth['\"]|require\(['\"]@/lib/auth['\"]"),
     "@/lib/auth", "authMock.js"),
)


def required_mocks(target_src: str) -> list[tuple[str, str]]:
    """`[(module, helper)]` this application file's test cannot run without."""
    return [(mod, helper) for rx, mod, helper in _NEEDS
            if rx.search(target_src or "")]


def mock_line(module: str, helper: str) -> str:
    return f"vi.mock('{module}', () => import('../../helpers/{helper}'))"


def ensure_mocks(body: str, target_src: str) -> str:
    """Add any `vi.mock` the file under test requires and the test left out."""
    if not body or not target_src:
        return body
    missing = [(mod, helper) for mod, helper in required_mocks(target_src)
               if not re.search(rf"vi\s*\.\s*mock\s*\(\s*['\"]{re.escape(mod)}['\"]",
                                body)]
    if not missing:
        return body

    lines = [mock_line(mod, helper) for mod, helper in missing]
    if not re.search(r"\bimport\s*\{[^}]*\bvi\b[^}]*\}\s*from\s*['\"]vitest['\"]", body):
        lines.insert(0, "import { vi } from 'vitest'")
    return "\n".join(lines) + "\n" + body


def drop_redundant_mocks(body: str) -> str:
    """Remove a `vi.mock` for a module that already works — next/link, lucide-react."""
    while True:
        m = _VI_MOCK_RE.search(body)
        if not m:
            return body

        i, depth = body.index("(", m.start()), 0
        for j in range(i, len(body)):
            c = body[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    if body[end:end + 1] == ";":
                        end += 1
                    while body[end:end + 1] == "\n":
                        end += 1
                    body = body[:m.start()] + body[end:]
                    break
        else:
            return body


def add_helper_imports(body: str) -> str:
    """Add the import for any AgentForge helper the test calls but did not import."""
    if not body:
        return body

    def already_imported(name: str) -> bool:
        return bool(re.search(rf"import\s*\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}\s*"
                              rf"from\s*['\"][^'\"]*helpers/", body))

    missing = {}
    for name, home in HELPER_HOMES.items():
        if not re.search(rf"(?<![.\w]){re.escape(name)}\s*\(", body):
            continue
        if already_imported(name):
            continue
        missing.setdefault(home, []).append(name)

    for name, home in HELPER_SPIES.items():
        if not re.search(rf"expect\s*\(\s*{re.escape(name)}\s*[,)]", body):
            continue
        if already_imported(name):
            continue
        missing.setdefault(home, []).append(name)

    if not missing:
        return body

    lines = []
    for home, names in sorted(missing.items()):
        lines.append(f"import {{ {', '.join(sorted(names))} }} "
                     f"from '../../helpers/{home}'")
    block = "\n".join(lines)

    last = None
    for m in _IMPORT_LINE_RE.finditer(body):
        last = m
    if last:
        return body[:last.end()] + "\n" + block + body[last.end():]
    return block + "\n" + body


class QASessionBase:
    """Owns the timing, the queue and the manifest. Holds no model logic."""

    @staticmethod
    def model_for(session, arch) -> str:
        """Which model a QA-side call runs on."""
        return (getattr(session, "model", "") or ""
                or getattr(arch, "model", "") or "")

    def __init__(self, project_dir, *, callbacks: dict = None,
                 model: str = "", enabled: bool = True):
        self.project_dir = Path(project_dir)
        self.cb = callbacks or {}
        self.model = model
        self.enabled = enabled
        self.arch = None
        self.author = None

        self.cmd = CommandRunner(
            self.project_dir,
            npm_bin=self.cb.get("npm_bin", "npm"),
            node_bin=self.cb.get("node_bin", "node"),
            on_log=lambda lvl, txt: self._fire("on_log", lvl, txt),
            on_event=lambda ev: self._fire("on_command", ev),
            max_calls=QA_MAX_COMMANDS)

        self._q = queue.Queue()
        self._workers = []
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._pending = {}

        self._buffer = []
        self._queued = set()
        self._runner_lock = threading.Lock()
        self.manifest = {}
        self.report = QAReport()
        self.concurrent = False
        # Author tests while generation is happening.
        self.defer_execution = True
        self.jobs_done = 0
        self.tokens = 0

    def _fire(self, name, *a):
        fn = self.cb.get(name)
        if fn and callable(fn):
            try:
                fn(*a)
            except Exception as e:
                log.warning(f"callback {name} failed: {e}")

    def _log(self, lvl, txt):

        if self.cb and self.cb.get("on_log"):
            self._fire("on_log", lvl, txt)
            return
        log.info(txt)

    def bind(self, arch, analyzer=None):
        """Attach the architect once it exists, and decide the timing model."""
        from .author import UnitTestAuthor
        self.arch = arch

        if not self.manifest:
            self.load_manifest()
        adopted = self.adopt_orphans()
        if adopted:
            log.info(f"adopted {adopted} test file(s) already on disk")

        self.concurrent = is_cloud_model(self.model or getattr(arch, "model", ""))
        self.author = UnitTestAuthor(arch, self.project_dir,
                                     callbacks=self.cb, analyzer=analyzer,
                                     session=self)

        if self.concurrent:
            threading.Thread(target=self._warm_runner, daemon=True,
                             name="agentforge-qa-install").start()
        return self

    def _warm_runner(self):
        """Install the test runner as soon as the scaffold actually exists."""
        try:
            deadline = time.time() + 180
            package = self.project_dir / "package.json"
            while (not package.is_file() and not self._cancel.is_set()
                   and time.time() < deadline):
                time.sleep(0.25)
            if not package.is_file() or self._cancel.is_set():
                log.debug("qa: scaffold never became ready for runner warm-up")
                return
            self.ensure_runner()
        except Exception as e:
            log.warning(f"qa: warming the runner: {e}")

    def on_phase(self, inner):
        """Wrap the pipeline's `on_phase` callback."""
        def wrapped(payload):
            try:
                inner(payload)
            finally:
                if not self.enabled or not self.arch:
                    return
                try:
                    self._observe(payload)
                except Exception as e:
                    log.warning(f"qa on_phase: {e}")
        return wrapped

    def _observe(self, p):
        idx = p.get("phase", 0)
        if not isinstance(idx, int) or idx <= 0:
            return
        status = p.get("status")
        if status == "active":
            self._pending[idx] = list(p.get("files") or [])
        elif status == "done":

            self._enqueue(idx, self._pending.pop(idx, []))

    def on_file_written(self, inner):
        """Wrap the pipeline's `on_file_written`."""
        def wrapped(path, size, content):
            try:
                inner(path, size, content)
            finally:
                if not self.enabled or not self.arch or not self.concurrent:
                    return
                try:
                    self._note_file(path)
                except Exception as e:
                    log.warning(f"qa on_file_written: {e}")
        return wrapped


__all__ = [name for name in globals() if not name.startswith("__")]
