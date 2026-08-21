"""Add a feature to a finished app."""
from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass, field

from .architect import FileStreamParser
from .workspace import WorkspaceTools, TOOL_HELP


LOCAL_IMPORT_RE = re.compile(
    r"""from\s+['"]@/(components/[\w./-]+)['"]""")

log = logging.getLogger("features")

# Legacy public names kept for compatibility.
MAX_FILES = None
MAX_PACKAGES = None
MAX_READS = None

# These are path-safety boundaries, not complexity limits.
CHANGE_DIRS = ("app/", "components/", "lib/", "styles/", "src/")
CHANGE_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css")
CHANGE_ROOT_FILES = {
    "middleware.js", "middleware.jsx", "middleware.ts", "middleware.tsx",
    "instrumentation.js", "instrumentation.ts",
    "next.config.js", "next.config.mjs", "next.config.ts",
    "tailwind.config.js", "tailwind.config.mjs", "postcss.config.js",
}

def normalise_change_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return ""
    parts = raw.split("/")
    if ".." in parts:
        return ""
    while raw.startswith("./"):
        raw = raw[2:]
    return raw


def safe_change_path(path: str) -> bool:
    rel = normalise_change_path(path)
    if not rel:
        return False
    if rel.startswith(("node_modules/", ".git/", ".next/", ".agentforge/")):
        return False
    if rel in CHANGE_ROOT_FILES:
        return True
    return rel.startswith(CHANGE_DIRS) and rel.endswith(CHANGE_EXTS)

PLAN_LINE_RE = re.compile(
    r"^\s*(CURRENT|GAP|CAUSE|EVIDENCE|SUMMARY|PACKAGE|FILE|ROUTE|VERIFY|CONFIDENCE|DONE)\b\s*(?:::)?\s*(.*)$", re.M)


@dataclass
class FeatureSpec:
    summary: str = ""
    files: list = field(default_factory=list)
    packages: list = field(default_factory=list)
    routes: list = field(default_factory=list)
    written: list = field(default_factory=list)
    rejected: list = field(default_factory=list)

    context: dict = field(default_factory=dict)

    def paths(self) -> set:
        return {f["path"] for f in self.files}

    def is_empty(self) -> bool:
        return not self.files


PLAN_SYSTEM = """\
You are deciding how to implement ONE requested change in an existing Next.js 16 App Router +
MongoDB project. The change may be a small edit, a section redesign, a repair, or a large feature. You are NOT writing code yet.

You are given the project's plan, routes, a complete file inventory and the
most request-relevant source files. Nothing is hidden: when a dependency is not
shown, inspect it with the workspace tools instead of guessing. Prefer
`search_code`, `route_source`, `importers` and `read_file` over asking for a
whole-project dump.

A CHANGE IS NOT AUTOMATICALLY ONE FILE. A large feature or repair may change things that already
exist, and those dependent edits are the half that gets forgotten. Before you answer,
walk the project and ask what BREAKS or goes STALE because of this change:

  • Does a new field belong on seeded documents? `lib/seed.js` then changes —
    and say so in the `why`, because data already in the database will not have
    the field until it is re-seeded.
  • Does something new need linking to? The page or nav that should link to it
    is an edit, or the feature ships unreachable.
  • Does a shared component need a new prop? Then it changes, and so does
    every caller — count them before you decide it is worth it.
  • Does an API route need a new field in its response? That is an edit, and
    the client that reads it is another.
  • Does an existing page now show something it did not? That page is an edit.

ROUTE PRESERVATION IS A HARD DESIGN PRINCIPLE:
  • If the request changes behavior on an existing workflow/page, edit that
    workflow. Do NOT create a second route with a synonym such as `/basket`
    when `/bag` already owns the workflow. A new page route is justified
    only when the user explicitly asked for a new page/route or the current
    route table proves no existing owner exists.
  • Before adding a new API endpoint, inspect the current caller and sibling
    handlers. Reuse the app's existing auth/database/data-shape conventions;
    never create a parallel API merely because it is easy to imagine.

List those edits alongside the new files. A plan that is all `new` for a
feature touching an existing app is usually a plan that missed something.

Before naming files, establish what the app does NOW and what must be
different. Do not infer ownership from filenames. Ground every edit in source
you were given or inspected with the workspace tools.

Then output ONLY these lines, nothing else, no prose, no code fences:

CURRENT :: one sentence describing the observed current behavior/structure
GAP :: one sentence naming the exact missing/wrong behavior relative to the request
CAUSE :: the source-level reason for the gap; for a purely additive feature say capability absent, not a fake bug
EVIDENCE :: <existing-path> :: concrete fact observed in that file/route/import graph
EVIDENCE :: <existing-path> :: another fact when needed
SUMMARY :: one sentence describing the complete change
PACKAGE :: <npm-package>            (only if genuinely needed; omit otherwise)
FILE :: new|edit :: <path> :: server|client :: why this file changes
ROUTE :: /new-route                 (only for pages or handlers you add)
VERIFY :: one concrete proof that would show the requested behavior now works
CONFIDENCE :: high|medium|low
DONE

Rules:
  • Name EVERY file and package the requested change genuinely needs. Small
    changes should stay small; large changes may legitimately span many files.
    Never omit a necessary file merely to keep the plan short.
  • Every path is project-relative source. Normal locations are app/…,
    components/…, lib/…, styles/… or src/…. Root middleware/Next/Tailwind/
    PostCSS config may be named only when the requested change truly needs it.
  • `edit` means the file already exists; `new` means it does not. Get this
    right — it decides whether you are shown its current contents.
  • Do not list a file you only read for context. List what must CHANGE.
  • Do not plan authentication or a database connection unless the feature
    genuinely needs one; this app already has them. `lib/seed.js` IS fair game
    when the feature gives seeded records a new field.
"""


REPAIR_SYSTEM = """\
You are deciding WHICH FILES must change to fix a bug in a running Next.js 16
App Router + MongoDB project. You are NOT writing code yet.

You are given the project's plan, an inventory of every source file (one line
each), the routes it serves, and the errors the app actually produced when it
was exercised in a browser. To read a file, emit exactly:
<read_file path="app/items/page.jsx"/>
One per line; you will be given the contents and may then read more. Read the
files the errors point at, and the files those import — a stack frame names the
place the app died, which is often not the place that is wrong.

Do not jump from an error message to a rewrite. Establish a source-level root
cause first. A stack frame, failed request, rendered DOM mismatch, importer, or
route contract is evidence; a paraphrase of the symptom is not. When the
controller gives an exact project `file:line` runtime frame AND the supplied
source at that line demonstrates the defect, your FIRST EVIDENCE line must cite
that exact file and concrete source fact. Do not ask to rediscover evidence the
controller already provided, and do not downgrade a production exception into
a test/selector issue.

Then output ONLY these lines, nothing else, no prose, no code fences:

CURRENT :: one sentence describing what actually happened
GAP :: one sentence describing the required behavior that failed
CAUSE :: the source-level root cause, not the symptom
EVIDENCE :: <existing-path> :: concrete observed fact supporting the cause
EVIDENCE :: <existing-path> :: another independent fact when available
SUMMARY :: one sentence naming the repair
PACKAGE :: <npm-package>            (only if an import genuinely resolves to nothing)
FILE :: new|edit :: <path> :: server|client :: what is wrong in this file
VERIFY :: one exact runtime/build/user-flow proof that would disprove the bug
CONFIDENCE :: high|medium|low
DONE

Rules:
  • List the SMALLEST set of files that actually fixes the errors. A fix that
    rewrites six files to repair one 500 is a worse outcome than the 500.
  • "Only plain objects can be passed to Client Components" reported five
    times on one page is ONE bug, and the file to change is usually the client
    component receiving the prop — not the five call sites. A page passing
    `icon={DollarSign}` to a `'use client'` card is fixed by making the card
    take an icon NAME and look it up, which is two files at most.
  • Every path is project-relative safe source. Follow the dependency chain;
    do not omit a necessary source file just to keep the plan short.
  • `edit` means the file already exists; `new` means it does not. Get this
    right — it decides whether you are shown its current contents.
  • Do not list a file you only read for context. List what must CHANGE.
  • A route that redirects an unauthenticated visitor to /login is CORRECT
    behaviour, not a bug. Do not plan to remove a login requirement.
  • Do not assume scaffold/auth/database files are correct or wrong. Follow the
    runtime evidence. If the evidence and dependency chain actually lead there,
    include the exact source file that must change; otherwise leave it alone.
  • If the errors do not name a real defect in the application code, an empty
    FILE set is valid, but the conclusion must still be evidenced: output
    CURRENT, GAP, CAUSE, at least one EVIDENCE line, SUMMARY, VERIFY,
    CONFIDENCE and DONE, simply omitting FILE lines.
"""


class FeaturesAgentBase:
    """Composition over ArchitectAgent, exactly like AnalyzerAgent."""

    COVER_SYSTEM = """\
You are checking one plan against one request, and nothing else.

You are given what somebody asked for and the plan a model made for it: a
summary and the list of files it will change. Your job is to name the parts of
the REQUEST that no file in that plan will deliver.

A request is usually several things in one sentence — "the member sees X, the
staff see Y, the owner's dashboard shows Z". A plan that covers two of the
three is not a small miss: the third is a feature the person asked for, was
told was built, and does not have. It is also invisible, because everything
that WAS built works.

Go through the request one clause at a time. For each, find the file in the
plan that delivers it. Judge by what the file IS — a page's own file is what
shows something on that page — not by what would be convenient.

Reply with one line per uncovered part, and nothing else:

    MISSING :: <the part of the request, in its own words> :: <the file that should deliver it>

If the plan covers every part, reply with exactly:

    COVERED

No preamble, no explanation, no markdown.\
"""

    MEMORY_TURNS = 8

    MEMORY_CHARS = 14_000

    def __init__(self, arch, project_dir=None, *, callbacks=None, analyzer=None,
                 model=None):
        self.arch = arch
        self.project_dir = project_dir or arch.project_dir
        self.cb = callbacks or {}

        self.model = model or None
        if analyzer is None:
            from .analyzer import AnalyzerAgent
            analyzer = AnalyzerAgent(arch, self.project_dir, callbacks=self.cb)
        self.az = analyzer

    def _fire(self, name, *a):
        fn = self.cb.get(name)
        if fn and callable(fn):
            try:
                fn(*a)
            except Exception as e:
                log.warning(f"callback {name} failed: {e}")

    def _log(self, lvl, txt):
        self._fire("on_log", lvl, txt)
        log.info(txt)

    def _budget_chars(self) -> int:
        return self.az._budget_chars()

    def full_source(self, budget: int = 0) -> str:
        """Every source file in the project, complete."""
        files = self.az.source_files()
        budget = budget or int(self._budget_chars() * 0.55)

        def rank(rel):
            if rel.startswith("lib/"):
                return 0
            if rel.endswith(("layout.jsx", "layout.js")):
                return 1
            if "/api/" in rel:
                return 2
            if rel.startswith("app/"):
                return 3
            return 4

        out, used = [], 0
        for rel in sorted(files, key=lambda r: (rank(r), r)):
            body = files[rel]
            block = f"--- {rel} ---\n{body}\n"
            if used + len(block) > budget:
                out.append(f"--- {rel} ---\n(omitted — the context budget "
                           f"ran out here)\n")
                continue
            out.append(block)
            used += len(block)
        return "\n".join(out)

    def feature_focus_paths(self, request: str, limit: int | None = None) -> list[str]:
        """Cheap lexical retrieval before the LLM starts using workspace tools."""
        files = self.az.source_files()
        words = {w for w in re.findall(r"[a-z0-9_]+", str(request or "").lower())
                 if len(w) >= 4 and w not in {"this","that","with","from","page","make","add","update","change","feature"}}
        scored = []
        for rel, body in files.items():
            hay_path = rel.lower()
            hay = str(body or "").lower()[:16000]
            score = sum((8 if w in hay_path else 0) + min(hay.count(w), 4) for w in words)
            if score:
                scored.append((-score, rel))
        ordered = [rel for _, rel in sorted(scored)]
        chosen = ordered if not limit else ordered[:limit]
        for shared in ("app/layout.jsx", "app/layout.js", "components/Navbar.jsx",
                       "components/Navbar.js", "lib/seed.js"):
            if shared in files and shared not in chosen:
                chosen.append(shared)
        return chosen if not limit else chosen[:limit]

    def _focused_source(self, paths: list[str], budget: int) -> str:
        chunks, used = [], 0
        files = self.az.source_files()
        live = getattr(self.arch, "files", {}) or {}
        for rel in paths:
            body = str(live.get(rel, files.get(rel, "")) or "")
            if not body:
                continue
            block = f"\n### {rel}\n```\n{body}\n```\n"
            if used + len(block) > budget and chunks:
                break
            chunks.append(block)
            used += len(block)
        return "".join(chunks)

    def _memory(self) -> list:
        """The tail of the conversation the app was generated in."""
        convo = getattr(self.arch, "convo", None) or []
        if len(convo) < 3:
            return []
        body = [m for m in convo if m.get("role") in ("user", "assistant")]
        if not body:
            return []

        head, tail = body[:1], body[1:][-self.MEMORY_TURNS:]
        kept, total = [], 0
        for m in reversed(head + tail):
            c = (m.get("content") or "")[:2200]
            if total + len(c) > self.MEMORY_CHARS:
                break
            kept.append({"role": m["role"], "content": c})
            total += len(c)
        kept.reverse()
        if not kept:
            return []
        return ([{"role": "user", "content":
                  "Compact historical receipts from earlier work follow. "
                  "They are context only: CURRENT SOURCE, routes and package.json "
                  "are authoritative when history disagrees."}]
                + kept
                + [{"role": "assistant", "content":
                    "Understood. I remember this project and the choices we "
                    "made in it."}])


__all__ = [name for name in globals() if not name.startswith("__")]
