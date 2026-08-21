"""Project, route, account and runtime-evidence discovery for E2E journeys."""
from .e2e_common import *
# `journey_source_bundle` falls back to it when the caller has
from .e2e_contract import capability_contract


class E2EContextMixin:
    def __init__(self, arch, project_dir=None, *, callbacks=None, session=None,
                 analyzer=None, base_url="http://localhost:5173"):
        self.arch = arch
        self.project_dir = Path(project_dir or arch.project_dir)
        self.cb = callbacks or {}
        self.qa = session
        self.az = analyzer
        self.base_url = base_url.rstrip("/")
        self.scenarios = []
        # Runtime DOM evidence is read-only and cached per role/journey.
        self._runtime_evidence_cache = {}
        # Per-role, per-route DOM snapshots.
        self._runtime_route_cache = {}
        self._runtime_dynamic_cache = {}
        self._warmed_routes = set()
        self._last_run_evidence = {}
        self._last_cookie_jar = {}
        # Browser-loop circuit breaker.
        self._route_loop_fault = ""
        self._page_path_cache = None
        self._accepted_scenarios = {}


    def remember_scenario(self, journey, scenario) -> None:
        """Keep the last green scenario for the final clean-room replay."""
        import copy
        key = str((journey or {}).get("title") or getattr(scenario, "title", "") or "").strip()
        if key and scenario is not None:
            self._accepted_scenarios[key] = copy.deepcopy(scenario)

    def accepted_scenario(self, journey):
        key = str((journey or {}).get("title") or "").strip()
        return self._accepted_scenarios.get(key)

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

    def _test(self, status, msg, detail=""):
        self._fire("on_test", status, msg, detail)

    def accounts(self) -> list:
        """The demo accounts, from `.agentforge/plan.json`."""
        plan = getattr(self.arch, "plan", None) or {}
        accs = [a for a in (plan.get("demo_accounts") or [])
                if a.get("email") and a.get("password")]
        if accs:
            return accs
        try:
            fp = self.project_dir / ".agentforge" / "plan.json"
            if fp.is_file():
                data = json.loads(fp.read_text(encoding="utf-8"))
                return [a for a in (data.get("demo_accounts") or [])
                        if a.get("email") and a.get("password")]
        except Exception as e:
            log.debug(f"plan.json: {e}")
        return []

    def account_for(self, role) -> dict:
        """The demo account a scenario signs in with."""
        accs = self.accounts()
        if not accs:
            return {}
        if not role:
            return {}
        for a in accs:
            if (a.get("role") or "").lower() == role.lower():
                return a
        return accs[0]

    def route_map(self) -> dict:
        """Every URL this build serves, from the analyzer or straight off disk."""
        if self.az:
            try:
                return self.az.enumerate_routes() or {}
            except Exception as e:
                log.debug(f"routes: {e}")
        app = self.project_dir / "app"
        if not app.is_dir():
            return {}
        routes = {}
        try:
            for name, kind in (("page", "page"), ("route", "api")):
                for suffix in (".js", ".jsx"):
                    for fp in sorted(app.rglob(f"{name}{suffix}")):
                        parts = fp.relative_to(app).parts[:-1]
                        if any(seg in ("node_modules", ".next") for seg in parts):
                            continue
                        segs = [seg for seg in parts
                                if not (seg.startswith("(") and seg.endswith(")"))]
                        url = "/" + "/".join(segs) if segs else "/"
                        if url in routes:
                            continue
                        routes[url] = {
                            "file": fp.relative_to(self.project_dir).as_posix(),
                            "kind": kind, "dynamic": "[" in url, "methods": []}
        except Exception as e:
            log.debug(f"disk routes: {e}")
        return routes

    def _routes(self, cap: int = 0) -> str:
        """The whole route table, with the HTTP methods each endpoint answers."""
        try:
            rows = []
            for url, meta in sorted(self.route_map().items()):
                note = ("  ← reach this by CLICKING a link, never GOTO"
                        if meta.get("dynamic") else "")
                methods = [str(m) for m in (meta.get("methods") or [])
                           if meta.get("kind") == "api"]
                verbs = f"  [{', '.join(methods)}]" if methods else ""
                rows.append(f"  {url}  ({meta['kind']}){verbs}{note}")
            if cap and len(rows) > cap:
                hidden = len(rows) - cap
                rows = rows[:cap] + [f"  … and {hidden} more route(s)"]
            return "\n".join(rows)
        except Exception as e:
            log.debug(f"routes: {e}")
            return ""

    CHARS_PER_TOKEN = 3.4
    PROMPT_SHARE = 0.55

    def prompt_budget_chars(self) -> int:
        """How much prompt the model authoring this journey can really read."""
        model = ""
        try:
            model = QASession.model_for(self.qa, self.arch) or ""
        except Exception as e:
            log.debug(f"qa model: {e}")
        ctx = 0
        try:
            fn = getattr(self.arch, "ctx_for", None)
            if callable(fn):
                ctx = int(fn(model) or 0)
        except Exception as e:
            log.debug(f"qa context window: {e}")
        if not ctx:
            try:
                ctx = int(getattr(self.arch, "num_ctx", 0) or 0)
            except Exception:
                ctx = 0
        return max(8_000, int((ctx or 16_384) * self.PROMPT_SHARE
                              * self.CHARS_PER_TOKEN))

    @staticmethod
    def _fit(text: str, limit: int) -> str:
        """`text` trimmed to `limit`, saying so, or "" when there is no room."""
        text = str(text or "")
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if limit < 400:
            return ""
        keep = text[:limit - 120].rstrip()
        return keep + (f"\n… [{len(text) - len(keep):,} more characters of "
                       f"this section did not fit the model context window]")

    def source_of(self, rel, cap=7000) -> str:
        if not rel:
            return ""
        try:
            fp = self.project_dir / rel
            if fp.is_file():
                return fp.read_text(encoding="utf-8", errors="replace")[:cap]
        except Exception:
            pass
        try:
            return str((getattr(self.arch, "files", None) or {}).get(rel, "") or "")[:cap]
        except Exception:
            return ""

    LOCAL_IMPORT_RE = re.compile(r"""from\s+['"]@/(components/[\w./-]+)['"]""")

    def markup_for(self, rel, cap=7000) -> str:
        """A page's source and the components it renders."""
        body = self.source_of(rel, cap)
        if not body:
            return ""
        blocks = [f"--- {rel} ---\n{body}"]
        for spec in dict.fromkeys(self.LOCAL_IMPORT_RE.findall(body)):
            for ext in (".jsx", ".js", ""):
                child = f"{spec}{ext}"
                inner = self.source_of(child, 4000)
                if inner:
                    blocks.append(f"--- {child} (rendered by {rel}) ---\n{inner}")
                    break
            if len(blocks) >= 4:
                break
        return "\n\n".join(blocks)

    SOURCE_BUNDLE_BUDGET = 42_000
    SOURCE_FILE_CAP = 9_000
    LOCAL_SOURCE_IMPORT_RE = re.compile(
        r"(?:from\s+|import\s*\(\s*)['\"](?:@/)?((?:app|components|lib)/[A-Za-z0-9_./\[\]-]+)['\"]")
    REL_SOURCE_IMPORT_RE = re.compile(
        r"(?:from\s+|import\s*\(\s*)['\"](\.{1,2}/[A-Za-z0-9_./\[\]-]+)['\"]")

    def _resolve_source_spec(self, spec: str, parent: str = "") -> str:
        """Resolve one project-local import to a real generated source file."""
        spec = str(spec or "").strip().replace("\\", "/")
        if not spec:
            return ""
        if spec.startswith("."):
            try:
                base = Path(parent).parent
                spec = (base / spec).as_posix()
                # Collapse./ and../ without touching the host filesystem.
                parts = []
                for bit in spec.split("/"):
                    if bit in ("", "."):
                        continue
                    if bit == "..":
                        if parts:
                            parts.pop()
                    else:
                        parts.append(bit)
                spec = "/".join(parts)
            except Exception:
                return ""
        spec = spec.lstrip("/")
        files = getattr(self.arch, "files", None) or {}
        candidates = [spec]
        if not re.search(r"\.(?:jsx?|mjs)$", spec, re.I):
            candidates += [spec + ".js", spec + ".jsx", spec + ".mjs",
                           spec.rstrip("/") + "/index.js",
                           spec.rstrip("/") + "/index.jsx"]
        for rel in candidates:
            if rel in files or (self.project_dir / rel).is_file():
                return rel
        return ""

    def journey_source_bundle(self, journey: dict = None, page: str = "",
                              budget: int = 0) -> str:
        """Actual source contract used before an E2E scenario is authored."""
        journey = journey or {}
        budget = int(budget or self.SOURCE_BUNDLE_BUDGET)
        max_files = max(24, min(90, budget // 3_000))
        file_cap = max(self.SOURCE_FILE_CAP, min(24_000, budget // 12))

        ordered, seen, depth = [], set(), {}

        def seed(bucket, rel):
            rel = self._resolve_source_spec(rel) if rel else ""
            if rel and rel.startswith(("app/", "components/", "lib/")) and rel not in seen:
                seen.add(rel)
                bucket.append(rel)
                depth[rel] = 0

        try:
            route_map = self.route_map()
        except Exception as e:
            log.debug(f"journey source routes: {e}")
            route_map = {}

        owned = []
        if page:
            seed(owned, page)
        contract = journey.get("contract") or capability_contract(self.arch, journey)
        for rel in (contract.get("source_files") or []):
            seed(owned, str(rel or ""))
        for handoff in (contract.get("handoffs") or []):
            if isinstance(handoff, dict):
                seed(owned, str(handoff.get("from") or ""))
        # `served_routes` is what the workflow itself names
        if "served_routes" not in journey:
            ground = getattr(self, "ground_journey_routes", None)
            if callable(ground):
                try:
                    ground(journey)
                except Exception as e:
                    log.debug(f"journey route grounding: {e}")
        for url in (journey.get("served_routes") or []):
            seed(owned, str((route_map.get(url) or {}).get("file") or ""))

        nearby = []
        for rel in self._journey_pages(journey):
            seed(nearby, rel)

        def walk(queue):
            """Follow at most three local-import layers out of these seeds."""
            i = 0
            while i < len(queue) and len(ordered) < max_files:
                rel = queue[i]
                i += 1
                if rel in ordered:
                    continue
                ordered.append(rel)
                body = self.source_of(rel, file_cap)
                if not body:
                    continue
                d = depth.get(rel, 0)
                if d < 3:
                    specs = list(self.LOCAL_SOURCE_IMPORT_RE.findall(body))
                    specs += list(self.REL_SOURCE_IMPORT_RE.findall(body))
                    for spec in specs:
                        child = self._resolve_source_spec(spec, rel)
                        if child and child not in seen:
                            seen.add(child)
                            queue.append(child)
                            depth[child] = d + 1

                # Include any real app/api route whose literal/prefix is called
                if "/api/" in body:
                    for url, meta in route_map.items():
                        url = str(url or "")
                        if not url.startswith("/api/"):
                            continue
                        rfile = str((meta or {}).get("file") or "")
                        if not rfile:
                            continue
                        prefix = url.split("[", 1)[0]
                        if (url in body) or (len(prefix) > 5 and prefix in body):
                            child = self._resolve_source_spec(rfile)
                            if child and child not in seen:
                                seen.add(child)
                                queue.append(child)
                                depth[child] = min(2, d + 1)

        walk(owned)
        walk(nearby)

        blocks, used = [], 0
        for rel in ordered:
            body = self.source_of(rel, file_cap)
            if not body:
                continue
            block = f"--- {rel} ---\n{body}"
            if used + len(block) > budget and blocks:
                break
            blocks.append(block)
            used += len(block)
        return "\n\n".join(blocks)

    def entry_page(self) -> str:
        """The sign-in page's file, because every journey starts there."""
        if not self.az:
            return ""
        try:
            for url, meta in (self.az.enumerate_routes() or {}).items():
                if meta.get("kind") == "page" and re.search(
                        r"log[-_ ]?in|sign[-_ ]?in", url, re.I):
                    return meta.get("file", "")
        except Exception as e:
            log.debug(f"entry page: {e}")
        return ""

    def landing_page(self) -> str:
        """Where signing in lands."""
        if not self.az:
            return ""
        try:
            return ((self.az.enumerate_routes() or {}).get("/") or {}).get("file", "")
        except Exception:
            return ""

    MARKUP_BUDGET = 30_000
    JOURNEY_PAGES = 8

    def _journey_pages(self, journey: dict = None) -> list:
        """Page files this particular journey is likely to touch."""
        out = [self.entry_page(), self.landing_page()]
        if not self.az:
            return [p for p in out if p]
        try:
            routes = self.az.enumerate_routes() or {}
        except Exception as e:
            log.debug(f"journey pages: {e}")
            return [p for p in out if p]

        def add_file(rel):
            if rel and rel not in out:
                out.append(rel)

        def file_for_url(url):
            meta = routes.get(url) or routes.get((url or "").rstrip("/")) or {}
            return meta.get("file", "")

        journey = journey or {}
        jparts = [str(journey.get("title") or ""),
                  str(journey.get("role") or "")]
        jparts += [str(x) for x in (journey.get("steps") or [])]
        jtext = " ".join(jparts).lower()

        for url in re.findall(r"(?<![\w:])(/[A-Za-z0-9_./\[\]-]+)", jtext):
            add_file(file_for_url(url.rstrip("/") or "/"))

        stop = {"the", "and", "then", "from", "with", "into", "that", "this",
                "page", "user", "admin", "member", "customer", "client",
                "visitor", "staff", "owner", "manager", "click", "go", "reach"}
        jwords = {w for w in re.findall(r"[a-z0-9]+", jtext)
                  if len(w) > 3 and w not in stop}

        plan = getattr(self.arch, "plan", None) or {}

        covers = {str(x or "").upper() for x in (journey.get("covers") or [])}
        if covers:
            for cap in (plan.get("capabilities") or []):
                if not isinstance(cap, dict):
                    continue
                if str(cap.get("id") or "").upper() not in covers:
                    continue
                for rel in (cap.get("files") or []):
                    add_file(str(rel or "").strip())

        scored = []
        for c in (plan.get("contracts") or []):
            if not isinstance(c, dict):
                continue
            ctext = " ".join(str(c.get(k) or "") for k in
                             ("name", "trigger", "effect", "target")).lower()
            cwords = {w for w in re.findall(r"[a-z0-9]+", ctext)
                      if len(w) > 3 and w not in stop}
            overlap = len(jwords & cwords)
            if overlap:
                scored.append((overlap, c))
        for _, c in sorted(scored, key=lambda x: -x[0]):
            add_file(str(c.get("from") or ""))
            target = str(c.get("target") or "")
            if target and "[" not in target:
                add_file(file_for_url(target.rstrip("/") or "/"))

        role = str(journey.get("role") or "").lower().strip()
        role_rows, rest = [], []
        for url, meta in routes.items():
            if meta.get("kind") != "page" or meta.get("dynamic") or "[" in url:
                continue
            item = (url, meta.get("file", ""))
            if role and (url == f"/{role}" or url.startswith(f"/{role}/")):
                role_rows.append(item)
            else:
                rest.append(item)

        role_rows.sort(key=lambda t: (t[0].count("/"), t[0]))
        rest.sort(key=lambda t: (t[0].count("/"), t[0]))
        for _, rel in role_rows + rest:
            add_file(rel)
            if len([p for p in out if p]) >= self.JOURNEY_PAGES:
                break

        return [p for p in out if p][:self.JOURNEY_PAGES]

    def _url_for_file(self, rel: str) -> str:
        """Static URL served by a planned page file, or `""`."""
        if not rel or not self.az:
            return ""
        try:
            for url, meta in (self.az.enumerate_routes() or {}).items():
                if meta.get("file") == rel and meta.get("kind") == "page" and \
                        not meta.get("dynamic") and "[" not in url:
                    return url or "/"
        except Exception:
            pass
        return ""

    def _journey_routes(self, journey: dict = None, limit: int = 7) -> list:
        """Concrete static routes that are relevant to one workflow."""
        journey = journey or {}
        out = []
        def add(url):
            url = str(url or "").strip()
            if url and url.startswith("/") and "[" not in url and url not in out:
                out.append(url)

        try:
            route_map = self.az.enumerate_routes() or {} if self.az else {}
        except Exception:
            route_map = {}

        # Explicit URLs in the workflow are authoritative.
        text = " ".join([str(journey.get("title") or ""),
                         str(journey.get("role") or "")] +
                        [str(x) for x in (journey.get("steps") or [])])
        for url in re.findall(r"(?<![\w:])(/[A-Za-z0-9_./\[\]-]+)", text):
            url = url.rstrip("/") or "/"
            if "[" in url:
                parent = re.sub(r"/\[[^/]+\](?:/.*)?$", "", url) or "/"
                if parent in route_map and not (route_map.get(parent) or {}).get("dynamic"):
                    add(parent)
                continue
            add(url)
        for rel in self._journey_pages(journey):
            add(self._url_for_file(rel))
            for pattern, meta in route_map.items():
                if meta.get("file") != rel or "[" not in pattern:
                    continue
                parent = re.sub(r"/\[[^/]+\](?:/.*)?$", "", pattern) or "/"
                if parent in route_map and not route_map[parent].get("dynamic"):
                    add(parent)

        role = str(journey.get("role") or "").strip().lower()
        if role:
            # Put role work pages before generic public pages.
            order = {u: i for i, u in enumerate(out)}
            out.sort(key=lambda u: (0 if (u == f"/{role}" or
                                          u.startswith(f"/{role}/")) else 1,
                                    order.get(u, 999)))
        return out[:limit]

    def _journey_dynamic_patterns(self, journey: dict = None) -> list:
        journey = journey or {}
        try:
            route_map = self.az.enumerate_routes() or {} if self.az else {}
        except Exception:
            route_map = {}
        pages = set(self._journey_pages(journey))
        text = " ".join([str(journey.get("title") or "")] +
                        [str(x) for x in (journey.get("steps") or [])])
        out = []
        for url, meta in route_map.items():
            if not meta.get("dynamic"):
                continue
            if meta.get("file") in pages or url in text:
                out.append(url)
        return out[:3]

    @staticmethod
    def _matches_dynamic(pattern: str, url: str) -> bool:
        path = str(url or "").split("?", 1)[0]
        rx = re.escape(str(pattern or ""))
        rx = re.sub(r"\\\[[^]]+\\\]", r"[^/?#]+", rx)
        return bool(re.fullmatch(rx, path))

    def runtime_evidence(self, journey: dict = None, limit: int = 4) -> str:
        """Read-only DOM map, including a real concrete dynamic record page."""
        journey = journey or {}
        role = str(journey.get("role") or "").strip().lower()
        routes = self._journey_routes(journey, limit=limit)
        patterns = self._journey_dynamic_patterns(journey)
        key = (role, tuple(routes), tuple(patterns))
        if key in self._runtime_evidence_cache:
            return self._runtime_evidence_cache[key]
        if not routes and not patterns:
            return ""

        missing = [u for u in routes if (role, u) not in self._runtime_route_cache]
        dyn_key = (role, tuple(patterns))
        need_dynamic = bool(patterns and dyn_key not in self._runtime_dynamic_cache)
        if missing or need_dynamic:
            try:
                from playwright.sync_api import sync_playwright
            except Exception:
                return ""
            dynamic_blocks = []
            found_dynamic = set()
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
                    self._install_route_loop_guard(ctx)
                    page = ctx.new_page()
                    try:
                        page.goto(self.base_url + "/", timeout=GOTO_TIMEOUT,
                                  wait_until="domcontentloaded")
                        self._wait_hydrated(page)
                    except Exception:
                        pass
                    acc = self.account_for(role) if role else {}
                    signed = self._sign_in(page, acc) if acc else True
                    for url in routes:
                        if acc and url == "/login":
                            continue
                        if acc and not signed:
                            self._runtime_route_cache[(role, url)] = (
                                f"DOM preflight for {role or 'role'} at {url}: authentication did not establish a session")
                            continue
                        try:
                            page.goto(self.base_url + url, timeout=GOTO_TIMEOUT,
                                      wait_until="domcontentloaded")
                            self._wait_hydrated(page)
                            self._wait_app_settled(page)
                            block = self._selector_inventory(page, limit=22)
                            loop_fault = self._consume_route_loop_fault()
                            if loop_fault:
                                block = loop_fault + "\n" + block
                            if need_dynamic:
                                try:
                                    hrefs = page.eval_on_selector_all(
                                        'a[href]', "els => els.map(e => e.getAttribute('href')).filter(Boolean)")
                                except Exception:
                                    hrefs = []
                                for href in hrefs:
                                    h = str(href or "").strip()
                                    if not h.startswith("/"):
                                        continue
                                    hpath = h.split("?", 1)[0]
                                    pat = next((pat for pat in patterns
                                                if pat not in found_dynamic and self._matches_dynamic(pat, hpath)), "")
                                    if not pat:
                                        continue
                                    try:
                                        detail = ctx.new_page()
                                        detail.goto(self.base_url + h, timeout=GOTO_TIMEOUT,
                                                    wait_until="domcontentloaded")
                                        self._wait_hydrated(detail)
                                        self._wait_app_settled(detail)
                                        dynamic_blocks.append(
                                            f"Dynamic pattern {pat} resolved to real URL {h}\n" +
                                            self._selector_inventory(detail, limit=24))
                                        self._warmed_routes.add(hpath)
                                        found_dynamic.add(pat)
                                        detail.close()
                                    except Exception as e:
                                        log.debug(f"dynamic runtime evidence {h}: {e}")
                        except Exception as e:
                            block = f"DOM at {url}: navigation failed: {type(e).__name__}"
                        self._runtime_route_cache[(role, url)] = block
                        self._warmed_routes.add(url)
                    browser.close()
            except Exception as e:
                log.debug(f"runtime evidence: {e}")
            # `need_dynamic`, not `patterns`.
            if need_dynamic:
                self._runtime_dynamic_cache[dyn_key] = "\n\n".join(dynamic_blocks)[:9000]

        blocks = [self._runtime_route_cache.get((role, u), "") for u in routes]
        dyn = self._runtime_dynamic_cache.get(dyn_key, "")
        if dyn:
            blocks.append(dyn)
        text = "\n\n".join(b for b in blocks if b)[:18_000]
        self._runtime_evidence_cache[key] = text
        return text

    def invalidate_runtime_evidence(self):
        """A production edit may change selectors or route output; drop DOM cache."""
        self._runtime_evidence_cache.clear()
        self._runtime_route_cache.clear()
        self._runtime_dynamic_cache.clear()
        self.forget_warmed_routes()

    def forget_warmed_routes(self):
        """Every route is cold again."""
        self._warmed_routes.clear()
