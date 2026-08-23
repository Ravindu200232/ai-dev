"""Project inventory, route mapping, and contract topology."""
from .analyzer_common import *  # gate-local shared report types


class AnalyzerContextMixin:
    def __init__(self, arch, project_dir: Path = None, *,
                 base_url: str = "http://localhost:5173",
                 callbacks: dict = None,
                 allow_reseed: bool = False):
        self.arch = arch
        self.project_dir = Path(project_dir or arch.project_dir)
        self.base_url = base_url.rstrip("/")
        self.cb = callbacks or {}
        self.allow_reseed = allow_reseed

        self.cmd = CommandRunner(
            self.project_dir,
            npm_bin=(self.cb or {}).get("npm_bin", "npm"),
            node_bin=(self.cb or {}).get("node_bin", "node"),
            on_log=lambda lvl, txt: self._fire("on_log", lvl, txt),
            on_event=lambda ev: self._fire("on_command", ev))
        self._files_cache = None

        self._cache_seq = -1
        # Findings that survived their own model repair are still reported,
        # but later QA stages must not buy the same rewrite again.
        self._exhausted_findings = []

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

    def source_files(self, refresh: bool = False) -> dict:
        """Every source file, read from disk."""

        seq = getattr(self.arch, "write_seq", 0)
        if (self._files_cache is not None and not refresh
                and self._cache_seq == seq):
            return self._files_cache
        out = {}
        for fp in sorted(self.project_dir.rglob("*")):
            if not fp.is_file() or any(s in fp.parts for s in SKIP_DIRS):
                continue
            if fp.suffix not in SOURCE_EXT or fp.name.startswith(".env"):
                continue
            if fp.name in ("package-lock.json", "test_screenshot.png"):
                continue
            try:
                if fp.stat().st_size > MAX_FILE_BYTES:
                    continue
                rel = str(fp.relative_to(self.project_dir)).replace("\\", "/")
                out[rel] = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        self._files_cache = out
        self._cache_seq = seq
        return out

    def code_files(self) -> dict:
        """Next.js source only."""
        return {p: c for p, c in self.source_files().items()
                if Path(p).suffix in CODE_EXT
                and (p.startswith(NEXT_ROOTS) or p in ROOT_SOURCE)}

    def plan_text(self) -> str:
        """The prose plan. Prefer the live agent, fall back to disk."""
        if getattr(self.arch, "plan_md", ""):
            return self.arch.plan_md
        return self.source_files().get("plan.md", "")

    def planned_paths(self) -> list:
        """Source files the plan committed to, from the prose AND the JSON."""
        found = set()
        for raw in PROSE_PATH_RE.findall(self.plan_text()):
            if not PLACEHOLDER_RE.search(raw):
                found.add(raw)
        for phase in (self.arch.plan or {}).get("phases", []):
            for f in phase.get("files", []):
                p = f.get("path") if isinstance(f, dict) else f
                if p and not PLACEHOLDER_RE.search(p):
                    found.add(p)
        return sorted(found)

    def _exists(self, rel: str) -> bool:
        """`.js` and `.jsx` name the same file as far as a plan is concerned."""
        base = rel[:-4] if rel.endswith(".jsx") else rel[:-3]
        return any((self.project_dir / c).exists()
                   for c in (rel, base + ".js", base + ".jsx"))

    PLACEHOLDER_MARKERS = ("Building…", "Building&hellip;", "Building...")

    def _is_placeholder(self, rel: str) -> bool:
        body = self.source_files().get(rel, "")
        return (bool(body) and len(body) < 400
                and any(m in body for m in self.PLACEHOLDER_MARKERS))

    ALWAYS_CHECKED = ("app/page.jsx", "app/page.js")

    def missing_files(self) -> list:
        out = [p for p in self.planned_paths()
               if not self._exists(p) or self._is_placeholder(p)]
        seen = set(out)
        for rel in self.ALWAYS_CHECKED:
            if rel not in seen and self._is_placeholder(rel):
                out.append(rel)
        return out

    def enumerate_routes(self) -> dict:
        """Every URL the app serves — uncapped, including dynamic segments."""
        app = self.project_dir / "app"
        routes = {}
        if not app.is_dir():
            return routes

        def url_for(fp: Path) -> str:
            segs = [s for s in fp.relative_to(app).parts[:-1]
                    if not (s.startswith("(") and s.endswith(")"))]
            return "/" + "/".join(segs) if segs else "/"

        for name, kind in (("page", "page"), ("route", "api")):
            for suffix in (".js", ".jsx"):
                for fp in sorted(app.rglob(f"{name}{suffix}")):
                    if any(s in fp.parts for s in SKIP_DIRS):
                        continue
                    url = url_for(fp)
                    if url in routes:
                        continue
                    try:
                        body = fp.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        body = ""
                    routes[url] = {
                        "file": str(fp.relative_to(self.project_dir)).replace("\\", "/"),
                        "kind": kind,
                        "dynamic": "[" in url,
                        "methods": sorted(set(HTTP_METHOD_RE.findall(body))) or
                                   (["GET"] if kind == "page" else []),
                    }
        return routes

    @staticmethod
    def _route_matches(target: str, served) -> bool:
        tp = [s for s in target.strip("/").split("/") if s]
        for url in served:
            sp = [s for s in url.strip("/").split("/") if s]
            if len(sp) != len(tp):
                continue
            if all(b.startswith("[") or a == b for a, b in zip(tp, sp)):
                return True
        return False

    def dead_links(self, routes: dict = None) -> list:
        """Literal in-app links that resolve to no page."""
        routes = self.enumerate_routes() if routes is None else routes
        pages = [u for u, r in routes.items() if r["kind"] == "page"]
        dead = set()
        for path, content in self.code_files().items():
            targets = [a or b for a, b in LINK_HREF_RE.findall(content)]
            targets += ROUTER_PUSH_RE.findall(content)
            for raw in targets:
                url = raw.split("?")[0].split("#")[0].rstrip("/") or "/"
                if url.startswith("/api"):
                    continue
                if not self._route_matches(url, pages):
                    dead.add(url)
        return sorted(dead)

    # A route written as a string anywhere in a file.
    ROUTE_LITERAL_RE = re.compile(
        r"[\"'`](/(?:[a-z0-9][a-z0-9/_-]*)?)(?=(?:[?#]|\$\{|[\"'`]))", re.I)
    ROUTE_TEMPLATE_RE = re.compile(
        r"[`](/(?:[a-z0-9][a-z0-9/_-]*/))\$\{", re.I)

    def _mentions(self, rel: str, seen: set = None) -> set:
        """Routes named by a file and by the project files it imports."""
        from agents.gates import exports as _ex

        files = getattr(self.arch, "files", None) or {}
        seen = seen if seen is not None else set()
        if not rel or rel in seen or rel not in files:
            return set()
        seen.add(rel)
        body = files.get(rel) or ""
        if not isinstance(body, str):
            return set()
        out = {m.group(1).rstrip("/") or "/"
               for m in self.ROUTE_LITERAL_RE.finditer(body)}
        # Preserve the fact that a list links into a dynamic segment.
        out |= {m.group(1).rstrip("/") + "/*"
                for m in self.ROUTE_TEMPLATE_RE.finditer(body)}
        for stmt in _ex.parse_imports(body):
            target = _ex.resolve_local(rel, stmt.spec, files)
            if target:
                out |= self._mentions(target, seen)
        return out

    def unreachable_pages(self, routes: dict = None) -> list:
        """Pages nobody can click their way to from the front door."""
        routes = self.enumerate_routes() if routes is None else routes
        # Dynamic detail pages are real nodes in the navigation graph.
        pages = {u: r for u, r in routes.items() if r["kind"] == "page"}
        if not pages:
            return []

        files = getattr(self.arch, "files", None) or {}
        shell = set()
        for rel in ("app/layout.jsx", "app/layout.js"):
            if rel in files:
                shell |= self._mentions(rel)

        def resolved(named):
            hits = set()
            if named in pages:
                hits.add(named)
            if named.endswith("/*"):
                prefix = named[:-1]  # Keep trailing slash
                for url, meta in pages.items():
                    if meta.get("dynamic") and url.startswith(prefix):
                        hits.add(url)
            return hits

        reachable, queue = {"/"}, ["/"]
        while queue:
            here = queue.pop()
            rel = (pages.get(here) or {}).get("file", "")
            named = (self._mentions(rel) | shell) if rel else shell
            for mention in named:
                for url in resolved(mention):
                    if url not in reachable:
                        reachable.add(url)
                        queue.append(url)

        return sorted(set(pages) - reachable - {"/"})

    # fetch('/api/<name>?<key>=' + x) and fetch(`/api/<name>?<key>=${x}`)
    FETCH_QUERY_RE = re.compile(
        r"""fetch\(\s*[`'"](/api/[A-Za-z0-9_\-/\[\]]*)\?([^`'"]{1,200})""")

    QUERY_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")

    @classmethod
    def _query_keys(cls, raw: str) -> list:
        """The literal parameter names a caller puts in a fetch URL."""
        keys = []
        for chunk in str(raw or "").split("&"):
            key = chunk.split("=")[0].strip()
            if cls.QUERY_KEY_RE.match(key) and key not in keys:
                keys.append(key)
        return keys

    def query_contract_findings(self, routes: dict = None) -> list:
        """Query parameters a caller sends that its route handler never reads.

        A handler that ignores the parameter answers with the whole
        collection instead of the row that was asked for. The build stays
        green, the page renders, and the first real click throws on a
        property of `undefined` — so this is caught by reading the two files
        against each other, not by waiting for the browser.
        """
        routes = self.enumerate_routes() if routes is None else routes
        api = {url: meta for url, meta in routes.items()
               if meta.get("kind") == "api"}
        if not api:
            return []
        files = self.code_files()
        out, seen = [], set()

        for src, body in sorted(files.items()):
            if src.endswith("/route.js") or src.endswith("/route.jsx"):
                continue                      # a route calling itself is not this
            for url, raw in self.FETCH_QUERY_RE.findall(body or ""):
                url = url.rstrip("/") or "/"
                match = next((meta for served, meta in api.items()
                              if self._route_matches(url, [served])), None)
                rel = str((match or {}).get("file") or "")
                if not rel or rel not in files:
                    continue
                handler = files.get(rel) or ""
                for key in self._query_keys(raw):
                    if f"'{key}'" in handler or f'"{key}"' in handler or f"`{key}`" in handler:
                        continue
                    identity = (rel, key)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    out.append(Finding(
                        "major", "IGNORED_QUERY_PARAM",
                        f"{src} calls {url}?{key}=… but {rel} never reads "
                        f"'{key}', so the handler answers with the unfiltered "
                        f"collection and the caller reads the wrong shape",
                        path=rel,
                        fix=(f"read `{key}` in {rel} with "
                             f"`const {key} = new URL(request.url).searchParams"
                             f".get('{key}')` and use it to query. If the "
                             f"caller sends `{key}` to look ONE row up, answer "
                             f"with that single object and a 404 when it does "
                             f"not exist — never an array, because the caller "
                             f"reads properties straight off the response."),
                        extra=[src, rel]))
        return out[:12]

    def contract_findings(self, routes: dict = None) -> list:
        """Deterministic failures in planner-declared cross-file hand-offs."""
        contracts = (getattr(self.arch, "plan", None) or {}).get("contracts") or []
        if not contracts:
            return []
        routes = self.enumerate_routes() if routes is None else routes
        files = self.code_files()
        out = []

        def served(target):
            return next(((url, r) for url, r in routes.items()
                         if self._route_matches(target, [url])), ("", None))

        for c in contracts:
            if not isinstance(c, dict):
                continue
            src = str(c.get("from") or "").lstrip("./").replace("\\", "/")
            target = str(c.get("target") or "").strip()
            kind = str(c.get("kind") or "").lower()
            name = str(c.get("name") or kind or "handoff")
            if not src or src not in files or not target.startswith("/"):
                continue

            url, route = served(target)
            if kind == "api":
                method = str(c.get("method") or "").upper()
                if not route:
                    expected = "app" + target.rstrip("/") + "/route.js"
                    call = f"{method} {target}" if method else target
                    out.append(Finding(
                        "blocker", "BROKEN_CONTRACT",
                        f"contract '{name}' says {src} calls {call}, but no "
                        f"route handler serves that URL",
                        path=src,
                        fix=f"implement {expected} and keep {src} wired to {call}",
                        extra=[src, expected]))
                    continue
                if method and method not in (route.get("methods") or []):
                    out.append(Finding(
                        "blocker", "BROKEN_CONTRACT",
                        f"contract '{name}' requires {method} {target}, but "
                        f"{route['file']} exports "
                        f"{('/'.join(route.get('methods') or []) or 'no HTTP method')}",
                        path=route["file"],
                        fix=f"make {route['file']} export {method} with the request/"
                            f"response shape in the contract",
                        extra=[src, route["file"]]))

            # For static URLs, a caller that never names the target.
            if "[" not in target:
                mentions = self._mentions(src)
                if target.rstrip("/") not in mentions and target not in mentions:
                    out.append(Finding(
                        "major", "BROKEN_CONTRACT",
                        f"contract '{name}' says {src} must reach {target} when "
                        f"'{c.get('trigger') or 'the action'}' happens, but that "
                        f"URL is absent from the caller and its local imports",
                        path=src,
                        fix=f"wire the declared action in {src} to {target} without "
                            f"changing the rest of the screen. MACHINE CHECK: the "
                            f"exact string '{target}' must appear in {src} or a "
                            f"component it imports — write fetch('{target}') "
                            f"literally, not via a variable or helper.",
                        extra=[src]))
        seen, unique = set(), []
        for f in out:
            key = (f.code, f.path, f.message)
            if key in seen:
                continue
            seen.add(key)
            unique.append(f)
        return unique[:12]
