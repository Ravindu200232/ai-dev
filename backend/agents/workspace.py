"""Read-only repo tools shared by AgentForge's LLM agents."""
from __future__ import annotations

import json
import re
from pathlib import Path

TOOL_HELP = r"""
AGENTIC WORKSPACE TOOLS — use them only when current context is insufficient.
Ask for at most four read-only tools in one turn, one tag per line.  AgentForge
will return the observations and you continue the SAME task.  Do not repeat an
identical request.

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

    def read_file(self, rel: str) -> str:
        rel = _clean(rel)
        if not rel or ".." in Path(rel).parts:
            return "refused unsafe path"
        body = self.files.get(rel)
        if body is None:
            return f"not found: {rel}"
        return f"--- {rel} COMPLETE ---\n{str(body)[:18000]}"

    def search_code(self, query: str) -> str:
        q = str(query or "").strip()
        if not q:
            return "empty search"
        try:
            rx = re.compile(q, re.I)
        except re.error:
            rx = re.compile(re.escape(q), re.I)
        rows = []
        for rel, body in sorted(self.files.items()):
            if not rel.startswith(("app/", "components/", "lib/", "tests/")):
                continue
            for n, line in enumerate(str(body or "").splitlines(), 1):
                if rx.search(line):
                    rows.append(f"{rel}:{n}: {line.strip()[:260]}")
                    if len(rows) >= 80:
                        return "\n".join(rows)
        return "\n".join(rows) or "no matches"

    def list_files(self, prefix: str) -> str:
        prefix = _clean(prefix)
        if ".." in Path(prefix or ".").parts:
            return "refused unsafe prefix"
        rows = [p for p in sorted(self.files) if not prefix or p.startswith(prefix)]
        return "\n".join(rows[:200]) or "no files"

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
