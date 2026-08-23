"""Core read-only workspace tools shared by LLM agents."""
from __future__ import annotations

import json
import re
import stat
from pathlib import Path

TOOL_HELP = r"""
AGENTIC WORKSPACE TOOLS — use them only when current context is insufficient.
Prefer the function tools offered by the API. If this model cannot call tools,
the tags below are a compatibility fallback. Ask for at most four tools in one
turn. AgentForge returns the observations and you continue the SAME task. Do
not repeat an identical request.

<read_file path="app/items/page.jsx"/>
<search_code query="stock_quantity"/>
<list_files prefix="components/"/>
<route_source path="/items/123"/>
<importers path="components/ProductCard.jsx"/>
<dependency_closure path="app/items/new/page.jsx"/>
<tests_for path="components/ProductCard.jsx"/>
<route_map prefix="/"/>
<plan_query query="sign in"/>

RUN SOMETHING when reading is not enough — the build, the linter, a one-line
node script.  Only a short allow-list is accepted and every run is capped:

<run_command cmd="npm run build"/>
<run_command cmd="node -e \"console.log(require('./package.json').name)\""/>

REMEMBER WHAT YOU LEARN so the next turn does not repeat it.  One note per
line, kind is one of goal | tried | learned | decided | avoid:

<remember kind="tried">rewrote the guard in app/admin/page.jsx — still 200</remember>
<remember kind="avoid">do not touch lib/auth.js, the redirect is not from there</remember>
<recall query="guard"/>

After the observations, make the smallest complete change.  Never ask the user
to copy a file that these tools can inspect.
"""

_TAGS = {
    "read_file": re.compile(r"<read_file\s+path=[\"']([^\"']+)[\"']\s*/?>", re.I),
    "search_code": re.compile(r"<search_code\s+query=[\"']([^\"']+)[\"']\s*/?>", re.I),
    "list_files": re.compile(r"<list_files\s+prefix=[\"']([^\"']*)[\"']\s*/?>", re.I),
    "route_source": re.compile(r"<route_source\s+path=[\"']([^\"']+)[\"']\s*/?>", re.I),
    "importers": re.compile(r"<importers\s+path=[\"']([^\"']+)[\"']\s*/?>", re.I),
    "dependency_closure": re.compile(r"<dependency_closure\s+path=[\"']([^\"']+)[\"']\s*/?>", re.I),
    "tests_for": re.compile(r"<tests_for\s+path=[\"']([^\"']+)[\"']\s*/?>", re.I),
    "route_map": re.compile(r"<route_map\s+prefix=[\"']([^\"']*)[\"']\s*/?>", re.I),
    "plan_query": re.compile(r"<plan_query\s+query=[\"']([^\"']+)[\"']\s*/?>", re.I),
    "run_command": re.compile(r"<run_command\s+cmd=[\"']([^\"']{1,300}?)[\"']\s*/?>", re.I),
    "recall": re.compile(r"<recall\s+query=[\"']([^\"']*)[\"']\s*/?>", re.I),
}

READ_TOOL_NAMES = (
    "list_files", "read_file", "search_code", "route_source", "importers",
    "dependency_closure", "tests_for", "route_map", "plan_query", "recall",
)
WORKSPACE_TOOL_NAMES = READ_TOOL_NAMES + ("run_command", "remember")


def _schema(name: str, description: str, properties: dict,
            required=()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object", "properties": properties,
                "required": list(required),
            },
        },
    }


WORKSPACE_SCHEMAS = {
    "list_files": _schema(
        "list_files", "List project files/directories with byte sizes and permissions.",
        {"path": {"type": "string", "description": "Project-relative directory; empty means project root."},
         "depth": {"type": "integer", "description": "Levels to include, from 1 to 8."}}),
    "read_file": _schema(
        "read_file", "Read a project file with line numbers and a bounded window.",
        {"path": {"type": "string", "description": "Project-relative file path."},
         "start": {"type": "integer", "description": "First 1-based line."},
         "limit": {"type": "integer", "description": "Maximum lines, up to 500."}},
        ("path",)),
    "search_code": _schema(
        "search_code", "Search current project source and return matching path:line evidence.",
        {"query": {"type": "string", "description": "Text or regular expression."},
         "path": {"type": "string", "description": "Optional project-relative path prefix."},
         "glob": {"type": "string", "description": "Optional filename glob such as *.jsx."}},
        ("query",)),
    "route_source": _schema(
        "route_source", "Map a browser or API route to its current source file.",
        {"path": {"type": "string", "description": "Route such as /items/123 or /api/items."}},
        ("path",)),
    "importers": _schema(
        "importers", "List current source files that import a target file.",
        {"path": {"type": "string", "description": "Project-relative source file."}},
        ("path",)),
    "dependency_closure": _schema(
        "dependency_closure", "Show a source file and its local imports to depth two.",
        {"path": {"type": "string", "description": "Project-relative source file."}},
        ("path",)),
    "tests_for": _schema(
        "tests_for", "Find generated tests associated with a source file.",
        {"path": {"type": "string", "description": "Project-relative source file."}},
        ("path",)),
    "route_map": _schema(
        "route_map", "List application routes and their owning files.",
        {"prefix": {"type": "string", "description": "Route prefix; / means all routes."}}),
    "plan_query": _schema(
        "plan_query", "Search the approved plan, capabilities, workflows and contracts.",
        {"query": {"type": "string", "description": "Term to find, or current for the compact plan."}}),
    "recall": _schema(
        "recall", "Recall durable notes from earlier work on this project.",
        {"query": {"type": "string", "description": "What to recall."}},
        ("query",)),
    "run_command": _schema(
        "run_command", "Run one allow-listed npm/npx/node/yarn/pnpm project command.",
        {"command": {"type": "string", "description": "One command, without shell operators."}},
        ("command",)),
    "remember": _schema(
        "remember", "Save one durable project note for later agent turns.",
        {"kind": {"type": "string", "description": "goal, tried, learned, decided, or avoid."},
         "text": {"type": "string", "description": "A concise factual note."}},
        ("kind", "text")),
}

# `remember` carries its text in the body, so it is matched on its own.
_REMEMBER_RE = re.compile(
    r"<remember(?:\s+kind=[\"']([a-z]{3,10})[\"'])?\s*>(.{4,400}?)</remember>",
    re.I | re.S)

COMMAND_TIMEOUT = 180
COMMAND_OUTPUT_CHARS = 4000

_IMPORT_RE = re.compile(r"(?:from\s+|import\s*\(\s*)['\"]([^'\"]+)['\"]")


def _clean(value: str) -> str:
    value = str(value or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _unsafe(value: str) -> bool:
    raw = str(value or "").strip().replace("\\", "/")
    return (raw.startswith(("/", "~")) or
            bool(re.match(r"^[a-z]:/", raw, re.I)) or
            ".." in Path(raw or ".").parts)


STRUCTURE_CHARS = 3_500

# Directories whose contents tell a reader nothing about the app's own shape.
_STRUCTURE_SKIP = ("node_modules/", ".next/", ".git/", ".agentforge/",
                   "coverage/", "dist/", "out/", ".turbo/", ".vite/")


def project_structure(files, *, max_chars: int = STRUCTURE_CHARS,
                      prefix: str = "") -> str:
    """The project's folder/file layout, compact enough to sit in a prompt.

    Agents get a tool that reads any file, but a tool is only usable once the
    caller knows a path to ask for. Handing over the real layout up front is
    what turns "read the file you need" into something the model can act on
    without guessing a filename.
    """
    if isinstance(files, dict):
        names = list(files)
    else:
        names = list(files or [])
    base = _clean(prefix).rstrip("/")

    grouped = {}
    total = 0
    for rel in sorted(str(name or "").replace("\\", "/") for name in names):
        if not rel or rel.startswith(_STRUCTURE_SKIP):
            continue
        if base and not (rel == base or rel.startswith(base + "/")):
            continue
        head, _, leaf = rel.rpartition("/")
        grouped.setdefault(head + "/" if head else "./", []).append(leaf)
        total += 1
    if not total:
        return ""

    width = min(34, max((len(d) for d in grouped), default=0) + 2)
    rows, shown = [], 0
    for directory in sorted(grouped):
        leaves = grouped[directory]
        line = f"{directory:<{width}}{', '.join(leaves)}"
        if sum(len(r) + 1 for r in rows) + len(line) > max(400, max_chars):
            rows.append(f"… {len(grouped) - shown} more director"
                        f"{'y' if len(grouped) - shown == 1 else 'ies'}")
            break
        rows.append(line)
        shown += 1
    head = f"{total} file(s) in {len(grouped)} director" \
           f"{'y' if len(grouped) == 1 else 'ies'}"
    return head + "\n" + "\n".join(rows)


STRUCTURE_TITLE = "The files this project is made of"

STRUCTURE_NOTE = (
    "Every path above is real. Read any of them with the read_file / "
    "dependency_closure tools before you touch something that depends on "
    "them, and never name a file that is not on this list.")


def structure_block(source, *, title: str = STRUCTURE_TITLE,
                    max_chars: int = STRUCTURE_CHARS,
                    note: str = STRUCTURE_NOTE) -> str:
    """A ready-to-paste prompt section, or `""` when there is nothing to show.

    `source` is an agent (anything with `.files`) or the file map itself.
    """
    files = getattr(source, "files", source)
    tree = project_structure(files or {}, max_chars=max_chars)
    if not tree:
        return ""
    return f"## {title}\n{tree}\n{note}\n"


class WorkspaceTools:
    def __init__(self, arch):
        self.arch = arch
        self.project_dir = Path(getattr(arch, "project_dir", "."))
        self.cache = getattr(arch, "_workspace_tool_cache", None)
        if self.cache is None:
            self.cache = {}
            setattr(arch, "_workspace_tool_cache", self.cache)

    @property
    def files(self) -> dict:
        return getattr(self.arch, "files", None) or {}

    @staticmethod
    def schemas(names=None, extra=()) -> list:
        """OpenAI/Ollama function schemas, optionally narrowed by agent role."""
        selected = tuple(names or WORKSPACE_TOOL_NAMES)
        return [WORKSPACE_SCHEMAS[name] for name in selected
                if name in WORKSPACE_SCHEMAS] + list(extra or ())

    @staticmethod
    def assistant_message(content: str, calls=None) -> dict:
        message = {"role": "assistant", "content": str(content or "")}
        if calls:
            message["tool_calls"] = list(calls)
        return message

    @staticmethod
    def _arguments(raw) -> dict:
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(str(raw or "{}"))
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def tool_message(call: dict, name: str, content: str) -> dict:
        message = {"role": "tool", "name": name,
                   "content": str(content or "")[:18000]}
        call_id = str((call or {}).get("id") or "").strip()
        if call_id:
            message["tool_call_id"] = call_id
        return message

    def serve_calls(self, calls, *, names=None, max_calls: int = 4) -> tuple[list, int]:
        """Dispatch standard function calls and return `role: tool` messages."""
        allowed = set(names or WORKSPACE_TOOL_NAMES)
        messages, used = [], 0
        limit = max(1, int(max_calls or 4))
        for index, call in enumerate(list(calls or [])):
            fn = (call or {}).get("function") or {}
            name = str(fn.get("name") or "").strip()
            args = self._arguments(fn.get("arguments"))
            if index >= limit:
                messages.append(self.tool_message(
                    call, name or "unknown",
                    f"refused: at most {limit} workspace tools may run in one turn"))
                continue
            if name not in allowed or name not in WORKSPACE_SCHEMAS:
                messages.append(self.tool_message(
                    call, name or "unknown", f"refused: tool {name!r} is not available in this agent mode"))
                continue
            key = f"{name}::{json.dumps(args, sort_keys=True, default=str)}".lower()
            if key in self.cache:
                body = "refused: this exact tool call was already served; use its earlier result"
            else:
                self.cache[key] = True
                try:
                    body = self.dispatch(name, args)
                except Exception as exc:                       # noqa: BLE001
                    body = f"{name} failed: {type(exc).__name__}: {exc}"
                self.cache[key] = body
                used += 1
            messages.append(self.tool_message(call, name, body))
        return messages, used

    def dispatch(self, name: str, args: dict) -> str:
        """Map structured arguments to the existing safe tool implementations."""
        if name == "list_files":
            return self.list_files(path=args.get("path", ""), depth=args.get("depth", 4))
        if name == "read_file":
            return self.read_file(args.get("path", ""), args.get("start", 1),
                                  args.get("limit", 400))
        if name == "search_code":
            return self.search_code(args.get("query", ""), args.get("path", ""),
                                    args.get("glob", ""))
        if name == "route_source":
            return self.route_source(args.get("path", ""))
        if name in {"importers", "dependency_closure", "tests_for"}:
            return getattr(self, name)(args.get("path", ""))
        if name == "route_map":
            return self.route_map(args.get("prefix", "/"))
        if name == "plan_query":
            return self.plan_query(args.get("query", "current"))
        if name == "recall":
            return self.recall(args.get("query", ""))
        if name == "run_command":
            return self.run_command(args.get("command", ""))
        if name == "remember":
            kept = self.memory.remember(args.get("kind", "learned"),
                                        args.get("text", ""))
            return "note saved" if kept else "note already existed or was empty"
        return f"unknown workspace tool: {name}"

    def requests(self, reply: str) -> list[tuple[str, str]]:
        hits = []
        text = str(reply or "")
        for name, rx in _TAGS.items():
            for m in rx.finditer(text):
                hits.append((m.start(), name, m.group(1)))
        hits.sort(key=lambda x: x[0])
        return [(name, arg) for _, name, arg in hits[:4]]

    def serve(self, reply: str, *, max_calls: int = 4) -> tuple[str, int]:
        out, used = [], 0
        kept = self.remember_from(reply)
        if kept:
            out.append(f"### remember\n{kept} note(s) written to this "
                       f"project's notebook")
        for name, arg in self.requests(reply)[:max_calls]:
            key = f"{name}::{arg}".lower()
            if key in self.cache:
                out.append(f"### {name} {arg}\n(refused: exact tool request already served; use the observation already in context)")
                continue
            body = self.run(name, arg)
            self.cache[key] = body
            used += 1
            out.append(f"### {name} {arg}\n{body}")
        return ("\n\n".join(out), used)

    def run(self, name: str, arg: str) -> str:
        name = name.lower().strip()
        if name == "read_file":
            return self.read_file(arg)
        if name == "search_code":
            return self.search_code(arg)
        if name == "list_files":
            return self.list_files(arg)
        if name == "route_source":
            return self.route_source(arg)
        if name == "importers":
            return self.importers(arg)
        if name == "dependency_closure":
            return self.dependency_closure(arg)
        if name == "tests_for":
            return self.tests_for(arg)
        if name == "route_map":
            return self.route_map(arg)
        if name == "plan_query":
            return self.plan_query(arg)
        if name == "run_command":
            return self.run_command(arg)
        if name == "recall":
            return self.recall(arg)
        return f"unknown workspace tool: {name}"

    # ------------------------------------------------------------- doing
    @property
    def memory(self):
        got = getattr(self.arch, "_agent_memory", None)
        if got is None:
            from .agent_memory import memory_for
            got = memory_for(self.arch, getattr(self.arch, "agent_name", "agent"))
            setattr(self.arch, "_agent_memory", got)
        return got

    def run_command(self, command: str) -> str:
        """Run one allow-listed command in the project and return what it said."""
        from .commands import CommandRunner, validate
        command = str(command or "").strip()
        ok, why = validate(command)
        if not ok:
            return f"refused: {why}"
        try:
            runner = getattr(self.arch, "cmd", None) or CommandRunner(self.project_dir)
            result = runner.run(command, timeout=COMMAND_TIMEOUT)
        except Exception as e:                                  # noqa: BLE001
            return f"could not run it: {type(e).__name__}: {e}"[:400]
        body = str(getattr(result, "output", "") or "").strip()
        if len(body) > COMMAND_OUTPUT_CHARS:
            body = body[:COMMAND_OUTPUT_CHARS // 2] + "\n…\n" + body[-COMMAND_OUTPUT_CHARS // 2:]
        verdict = "ok" if getattr(result, "ok", False) else "FAILED"
        self.memory.remember(
            "tried", f"ran `{command}` — {verdict}", agent="workspace")
        return f"exit {getattr(result, 'code', '?')} ({verdict})\n{body or '(no output)'}"

    def recall(self, query: str) -> str:
        rows = self.memory.recall(query, limit=8)
        if not rows:
            return "nothing remembered about that yet"
        return "\n".join(f"[{r.get('kind')}] {r.get('text')}" for r in rows)

    def remember_from(self, reply: str) -> int:
        """Write every `<remember>` the model just emitted. Returns how many."""
        kept = 0
        for kind, text in _REMEMBER_RE.findall(str(reply or "")):
            if self.memory.remember(kind or "learned", text):
                kept += 1
        return kept

    def read_file(self, rel: str, start: int = 1, limit: int = 400) -> str:
        rel = _clean(rel)
        if not rel or _unsafe(rel):
            return "refused unsafe path"
        body = self.files.get(rel)
        if body is None:
            return f"not found: {rel}"
        lines = str(body).splitlines()
        first = max(1, int(start or 1))
        count = max(1, min(500, int(limit or 400)))
        window = lines[first - 1:first - 1 + count]
        numbered = "\n".join(f"{first + index:>5} {line}"
                             for index, line in enumerate(window))
        remaining = len(lines) - (first - 1 + len(window))
        tail = f"\n… {remaining} more lines" if remaining > 0 else ""
        return f"--- {rel} ({len(lines)} lines) ---\n{numbered}{tail}"[:18000]

    def search_code(self, query: str, path: str = "", glob: str = "") -> str:
        q = str(query or "").strip()
        if not q:
            return "empty search"
        if _unsafe(path):
            return "refused unsafe path"
        prefix = _clean(path)
        try:
            rx = re.compile(q, re.I)
        except re.error:
            rx = re.compile(re.escape(q), re.I)
        rows = []
        for rel, body in sorted(self.files.items()):
            if not rel.startswith(("app/", "components/", "lib/", "tests/")):
                continue
            if prefix and not (rel == prefix or rel.startswith(prefix.rstrip("/") + "/")):
                continue
            if glob and not Path(rel).match(glob):
                continue
            for n, line in enumerate(str(body or "").splitlines(), 1):
                if rx.search(line):
                    rows.append(f"{rel}:{n}: {line.strip()[:260]}")
                    if len(rows) >= 80:
                        return "\n".join(rows)
        return "\n".join(rows) or "no matches"

    def structure(self, *, max_chars: int = STRUCTURE_CHARS,
                  prefix: str = "") -> str:
        """This project's folder/file layout, for a prompt rather than a tool."""
        return project_structure(self.files, max_chars=max_chars, prefix=prefix)

    def list_files(self, prefix: str = "", *, path: str = "", depth: int = 4) -> str:
        raw = path if path not in (None, "") else prefix
        if _unsafe(raw):
            return "refused unsafe prefix"
        base = _clean(raw).rstrip("/")
        levels = max(1, min(8, int(depth or 4)))
        matches = []
        for rel in sorted(self.files):
            if base and not (rel == base or rel.startswith(base + "/")):
                continue
            remainder = rel[len(base):].lstrip("/") if base else rel
            if remainder.count("/") >= levels:
                continue
            matches.append(rel)
        if not matches:
            return f"{base or '.'}: no files"

        directories = set()
        for rel in matches:
            parts = rel.split("/")[:-1]
            for index in range(1, len(parts) + 1):
                directory = "/".join(parts[:index])
                if not base or directory == base or directory.startswith(base + "/"):
                    directories.add(directory)
        rows = [f"{base or '.'} ({len(matches)} files, depth {levels})"]
        for directory in sorted(directories):
            rows.append(f"dir  {'':>9} drwxr-xr-x {directory}/")
        for rel in matches:
            content = str(self.files.get(rel) or "")
            size = len(content.encode("utf-8", errors="replace"))
            target = self.project_dir / rel
            try:
                mode = stat.filemode(target.stat().st_mode) if target.is_file() else "-rw-------"
            except OSError:
                mode = "-rw-------"
            rows.append(f"file {size:>9,} {mode} {rel}")
        if len(rows) > 251:
            rows = rows[:251] + [f"… {len(rows) - 251} more entries"]
        return "\n".join(rows)

    def route_source(self, route: str) -> str:
        route = str(route or "").strip().split("?", 1)[0]
        if not route.startswith("/"):
            return "route must start with /"
        clean = route.rstrip("/") or "/"
        api = clean.startswith("/api/")
        segs = [s for s in (clean[5:] if api else clean.strip("/")).split("/") if s]
        prefix, leaf = ("app/api", "route.js") if api else ("app", "page.jsx")
        candidates = []
        if not segs and not api:
            candidates.extend(["app/page.jsx", "app/page.js"])
        else:
            stem = prefix + "/" + "/".join(segs)
            candidates.extend([stem + "/" + leaf])
            if leaf.endswith("jsx"):
                candidates.append(stem + "/page.js")
        for rel in candidates:
            if rel in self.files:
                return f"{clean} -> {rel}\n{str(self.files[rel])[:12000]}"
        # Dynamic App Router match.
        endings = ("/route.js",) if api else ("/page.jsx", "/page.js")
        for rel in sorted(self.files):
            if not rel.startswith(prefix + "/") or not rel.endswith(endings):
                continue
            middle = rel[len(prefix) + 1:]
            middle = re.sub(r"/(?:page\.jsx|page\.js|route\.js)$", "", middle)
            parts = [p for p in middle.split("/") if not (p.startswith("(") and p.endswith(")"))]
            if len(parts) != len(segs):
                continue
            if all(a == b or (a.startswith("[") and a.endswith("]")) for a, b in zip(parts, segs)):
                return f"{clean} -> {rel}\n{str(self.files[rel])[:12000]}"
        return f"no source mapped for {clean}"

    def importers(self, target: str) -> str:
        target = _clean(target)
        stem = re.sub(r"\.(?:jsx?|mjs)$", "", target)
        aliases = {"@/" + stem, "@/" + target}
        rows = []
        for rel, body in sorted(self.files.items()):
            for spec in _IMPORT_RE.findall(str(body or "")):
                if spec in aliases or spec.rstrip("/") == "@/" + stem:
                    rows.append(rel)
                    break
                if spec.startswith("."):
                    base = Path(rel).parent
                    resolved = _clean(str(base / spec))
                    resolved = re.sub(r"\.(?:jsx?|mjs)$", "", resolved)
                    if resolved == stem:
                        rows.append(rel)
                        break
        return "\n".join(rows[:100]) or f"no importers found for {target}"

    def _resolve_local_spec(self, importer: str, spec: str) -> str:
        if spec.startswith("@/"):
            base = spec[2:]
        elif spec.startswith("."):
            base = _clean(str(Path(importer).parent / spec))
        else:
            return ""
        for rel in (base, base + ".jsx", base + ".js", base + ".mjs",
                    base + "/index.jsx", base + "/index.js"):
            if rel in self.files:
                return rel
        return ""

    def dependency_closure(self, target: str) -> str:
        root = _clean(target)
        if root not in self.files:
            return f"not found: {root}"
        queue = [(root, 0)]
        seen, rows = set(), []
        while queue and len(seen) < 24:
            rel, depth = queue.pop(0)
            if rel in seen or depth > 2:
                continue
            seen.add(rel)
            body = str(self.files.get(rel) or "")
            local = [self._resolve_local_spec(rel, x) for x in _IMPORT_RE.findall(body)]
            local = [x for x in local if x]
            rows.append(f"{'  '*depth}{rel} -> {', '.join(local) if local else '(no local imports)'}")
            queue.extend((child, depth + 1) for child in local)
        return "\n".join(rows)

    def tests_for(self, target: str) -> str:
        target = _clean(target)
        stem = re.sub(r"\.(?:jsx?|mjs)$", "", target)
        base = Path(stem).name.lower()
        rows = []
        for rel, body in sorted(self.files.items()):
            if not rel.startswith("tests/"):
                continue
            low = str(body or "").lower()
            if target.lower() in low or ("@/" + stem).lower() in low or base in Path(rel).name.lower():
                rows.append(rel)
        return "\n".join(rows[:80]) or f"no generated tests found for {target}"

    def route_map(self, prefix: str = "/") -> str:
        prefix = str(prefix or "/").strip() or "/"
        rows = []
        for rel in sorted(self.files):
            route = kind = ""
            if rel in ("app/page.jsx", "app/page.js"):
                route, kind = "/", "page"
            elif rel.startswith("app/") and rel.endswith(("/page.jsx", "/page.js")):
                mid = re.sub(r"/page\.jsx?$", "", rel[4:])
                parts = [x for x in mid.split("/") if not (x.startswith("(") and x.endswith(")"))]
                route, kind = "/" + "/".join(parts), "page"
            elif rel.startswith("app/api/") and rel.endswith("/route.js"):
                route, kind = "/api/" + rel[len("app/api/"):-len("/route.js")], "api"
            if route and route.startswith(prefix):
                rows.append(f"{route} -> {rel} ({kind})")
        return "\n".join(rows[:200]) or f"no routes under {prefix}"

    def plan_query(self, query: str) -> str:
        plan = getattr(self.arch, "plan", None) or {}
        md = str(getattr(self.arch, "plan_md", "") or "")
        q = str(query or "").strip().lower()
        compact = json.dumps({
            "capabilities": plan.get("capabilities") or [],
            "workflows": plan.get("workflows") or [],
            "contracts": plan.get("contracts") or [],
            "phases": plan.get("phases") or [],
        }, ensure_ascii=False, indent=2)
        text = compact + "\n\n" + md
        if not q or q == "current":
            return text[:18000]
        rows = [line for line in text.splitlines() if q in line.lower()]
        return "\n".join(rows[:160]) or "no matching plan lines"
