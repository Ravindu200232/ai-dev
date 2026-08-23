"""Starting, answering and stopping a build."""
from __future__ import annotations

import logging
import re
import threading

from ..llm import Model
from ..service import Pipeline
from . import events, state

log = logging.getLogger("forge.server.runs")

PLAN_TIMEOUT = 900
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# The run in flight, if any. One build at a time is the whole product.
CURRENT = {"project": "", "gate": None, "cancelled": False}


def slug(text: str, fallback: str = "app") -> str:
    """`Build me a shop!` → `build-me-a-shop`."""
    out = _SLUG_RE.sub("-", str(text or "").lower()).strip("-")
    return out[:40].strip("-") or fallback


def project_dir_for(name: str):
    """A directory that is free, adding -2, -3 … when it is not."""
    root = state.projects_dir()
    base = root / name
    if not base.exists() or not any(base.iterdir()):
        return base
    for n in range(2, 100):
        alt = root / f"{name}-{n}"
        if not alt.exists() or not any(alt.iterdir()):
            return alt
    return base


def cancelled() -> bool:
    return CURRENT["cancelled"]


def cancel() -> bool:
    """Ask the running build to stop at its next checkpoint."""
    if not CURRENT["project"]:
        return False
    CURRENT["cancelled"] = True
    gate = CURRENT["gate"]
    if gate is not None:
        gate.reject()
    events.elog("WARN", "   ⏹ stopping after the current step…")
    return True


def decide(verdict: str, note: str = "") -> bool:
    """Answer the plan a run is waiting on."""
    gate = CURRENT["gate"]
    if gate is None:
        return False
    if verdict == "approve":
        return gate.approve()
    if verdict == "revise":
        return gate.revise(note)
    return gate.reject()


def busy() -> bool:
    return bool(CURRENT["project"])


def start(prompt: str, *, model: str = "", qa_model: str = "",
          project: str = "", kinds=("unit", "e2e")) -> bool:
    """Run a build in the background. False when one is already running."""
    if busy():
        events.eerr("A build is already running — stop it first")
        return False
    threading.Thread(target=_run, daemon=True,
                     args=(prompt, model, qa_model, project, tuple(kinds))).start()
    return True


def _run(prompt, model, qa_model, project, kinds) -> None:
    from .gate import PlanGate

    name = ""
    try:
        model = model or state.default_model()
        target = (state.projects_dir() / project if project
                  else project_dir_for(slug(prompt)))
        target.mkdir(parents=True, exist_ok=True)
        name = target.name

        gate = PlanGate(events.emit, timeout=PLAN_TIMEOUT)
        CURRENT.update(project=name, gate=gate, cancelled=False)
        events.eproject(name)
        events.elog("INFO", "━" * 40)
        events.elog("INFO", f"⚒️  {prompt[:80]}")
        events.elog("INFO", f"   🔨 {model}")
        events.elog("INFO", f"   📁 {target}")
        events.elog("INFO", "━" * 40)

        pipeline = Pipeline(target, Model(model),
                            qa_model=Model(qa_model) if qa_model else None,
                            emit=events.emit, should_stop=cancelled)
        result = pipeline.run(prompt, name=name, title=name.replace("-", " ").title(),
                              approve=gate.gate, kinds=kinds)

        events.emit({"type": "forge_result", "project": name,
                     "result": result.as_dict()})
        events.elog("INFO", f"   ⚒️  {result.summary()}")
        if result.stopped_at == "plan":
            events.elog("WARN", "   🧭 the plan was not approved — nothing was built")
        events.edone(name)
    except Exception as e:                                         # noqa: BLE001
        log.exception("build failed")
        events.eerr(f"Build failed: {type(e).__name__}: {e}")
    finally:
        CURRENT.update(project="", gate=None, cancelled=False)


EDIT_ROLE = """You are changing one existing Next.js 15 App Router app.

Read before you write: the file you are about to change, and anything it
imports that the change depends on. Make the smallest change that does what
was asked, keep every other behaviour exactly as it is, and write whole files.

When the change is done, reply with one short sentence saying what you did."""


class _Host:
    """What `forge.edit` needs from a host, backed by the project directory."""

    EDIT_TIMEOUT = 300

    def __init__(self, project_dir):
        self.project_dir = project_dir
        self.files = {}

    def write_file(self, rel, content):
        from ..tools.paths import resolve
        target = resolve(self.project_dir, rel)
        if target.is_file() and target.read_text(
                encoding="utf-8", errors="replace") == content:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.files[rel] = content
        events.emit({"type": "file", "name": rel, "size": len(content),
                     "content": content})
        return True

    def _budget_chars(self):
        return 80_000


def edit(prompt: str, project: str, *, model: str = "", focus=()) -> bool:
    """Change a project that already exists. False when one is running."""
    if busy():
        events.eerr("A build is already running — stop it first")
        return False
    threading.Thread(target=_edit, daemon=True,
                     args=(prompt, project, model, tuple(focus))).start()
    return True


def _edit(prompt, project, model, focus) -> None:
    from ..edit import EditAgent
    from ..events import relay

    target = state.projects_dir() / project
    try:
        if not target.is_dir():
            events.eerr(f"No project called {project}")
            return
        CURRENT.update(project=project, gate=None, cancelled=False)
        events.estep("build", "run")
        events.elog("INFO", f"✏️  {project} — {prompt[:80]}")

        result = EditAgent(_Host(target), target,
                           Model(model or state.default_model()),
                           on_event=relay(events.emit),
                           should_stop=cancelled).run(EDIT_ROLE, prompt,
                                                      focus=focus)
        events.estep("build", "done" if result.files else "error")
        if result.files:
            events.elog("INFO", f"   ✅ changed {', '.join(result.files)}")
        else:
            events.elog("WARN", "   ⚠ nothing was changed")
        events.emit({"type": "edit_result", "project": project,
                     "files": result.files, "text": result.text})
        events.edone(project)
    except Exception as e:                                         # noqa: BLE001
        log.exception("edit failed")
        events.eerr(f"Edit failed: {type(e).__name__}: {e}")
    finally:
        CURRENT.update(project="", gate=None, cancelled=False)
