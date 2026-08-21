"""Runtime incident evidence, auth checks, role separation and integrity helpers."""
from .e2e_common import *


class E2EEvidenceMixin:
    def _real_path(self, rel: str) -> str:
        """`rel` if the project really has that file, else ""."""
        rel = (rel or "").strip().lstrip("./").replace("\\", "/")
        if not rel:
            return ""
        cands = ([rel] if rel.endswith((".jsx", ".js", ".mjs"))
                 else [rel + ".jsx", rel + ".js",
                       rel + "/page.jsx", rel + "/route.js"])
        known = getattr(getattr(self, "arch", None), "files", None) or {}
        for c in cands:
            if c in known:
                return c
            try:
                if (self.project_dir / c).is_file():
                    return c
            except Exception:
                pass
        return ""

    def console_bug_sites(self, ev: dict = None) -> list:
        """Every (path, line, evidence) the browser itself named, best first."""
        ev = dict(ev or getattr(self, "_last_run_evidence", {}) or {})

        blobs = []
        # The structured events first.
        for item in (ev.get("runtime_events") or []):
            if not isinstance(item, dict):
                continue
            url, line = str(item.get("url") or ""), item.get("line") or 0
            if url:
                blobs.append(f"{url}:{line}" if line else url)
            for k in ("stack", "text"):
                if item.get(k):
                    blobs.append(str(item[k]))
        blobs.extend(str(x) for x in (ev.get("console") or []))
        blobs.extend(str(x) for x in (ev.get("network_errors") or []))
        if ev.get("message"):
            blobs.append(str(ev["message"]))

        seen, out = set(), []
        for blob in blobs:
            for rx in self._SITE_RES:
                for m in rx.finditer(blob):
                    rel = self._real_path(m.group(1))
                    if not rel:
                        continue
                    line = int(m.group(2)) if m.lastindex and m.group(2) else 0
                    if (rel, line) in seen:
                        continue
                    seen.add((rel, line))
                    out.append((rel, line, blob.strip()[:160]))
        return out

    def incident_evidence(self, failures=None, journey=None, dev_trace: str = "") -> str:
        """Tool-produced evidence for one E2E root-cause decision."""
        ev = dict(getattr(self, "_last_run_evidence", {}) or {})
        failures = list(failures or [])
        if failures and not ev.get("target"):
            ev["target"] = str(getattr(failures[0], "target", "") or "")
        if failures and not ev.get("message"):
            ev["message"] = str(getattr(failures[0], "message", "") or "")
        target = str(ev.get("target") or "").strip()

        lines = ["## E2E incident tools"]
        lines.append("browser=Playwright DOM/URL/console")
        lines.append("network=API request content-type/body + response status/body transcript")
        lines.append("auth=get-session response + cookie names")
        lines.append("source=owning page + local imports + referenced API routes")
        lines.append(f"route={ev.get('route') or '?'}")
        lines.append(f"target={target or '?'}")
        lines.append(f"failed_step={ev.get('step') or '?'}")
        lines.append(f"failure_kind={ev.get('kind') or '?'}")
        lines.append(f"failure={ev.get('message') or '?'}")
        contract = ((ev.get("journey") or {}).get("contract")
                    if isinstance(ev.get("journey"), dict) else None)
        if contract:
            try:
                lines.append("\n## Executable capability contract\n" +
                             json.dumps(contract, ensure_ascii=False, indent=2)[:7000])
            except Exception:
                pass

        sess = ev.get("session")
        if sess is not None:
            try:
                lines.append("auth_session=" + json.dumps(sess, ensure_ascii=False)[:1600])
            except Exception:
                lines.append(f"auth_session={str(sess)[:1600]}")
        lines.append("cookie_names=" + ", ".join(ev.get("cookie_names") or [])[:500])

        api = ev.get("api_network") or []
        if api:
            lines.append("\n## API transcript")
            lines.extend(str(x)[:1400] for x in api[-16:])
        mutations = ev.get("mutation_events") or []
        if mutations:
            lines.append("\n## Successful business mutations")
            for row in mutations[-10:]:
                try:
                    lines.append(json.dumps(row, ensure_ascii=False)[:1600])
                except Exception:
                    lines.append(str(row)[:1600])
        checkpoint = ev.get("checkpoint_values") or {}
        if checkpoint:
            try:
                lines.append("\n## Checkpoint business state\n" + json.dumps(checkpoint, ensure_ascii=False)[:2200])
            except Exception:
                pass
        step_errs = ev.get("step_network_errors") or []
        if step_errs:
            lines.append("\n## Network failures from the failing step")
            lines.extend(str(x)[:1200] for x in step_errs[-8:])
        errs = ev.get("network_errors") or []
        if errs:
            lines.append("\n## Network failures")
            lines.extend(str(x)[:1200] for x in errs[-10:])
        sites = self.console_bug_sites(ev)
        if sites:
            lines.append("\n## WHERE THE BROWSER SAYS THE BUG IS")
            lines.append("Resolved from the browser's own error stack to files "
                         "that really exist in this project. Read these first "
                         "and fix here unless the evidence below contradicts "
                         "them.")
            for rel, line, why in sites[:6]:
                lines.append(f"  {rel}:{line}" if line else f"  {rel}")
                lines.append(f"      {why}")

        runtime_events = ev.get("runtime_events") or []
        if runtime_events:
            lines.append("\n## Structured runtime events (higher priority than selector guesses)")
            for item in runtime_events[-10:]:
                if not isinstance(item, dict):
                    continue
                pos = str(item.get("url") or "")
                if item.get("line"):
                    pos += f":{item.get('line')}"
                    if item.get("column"):
                        pos += f":{item.get('column')}"
                row = (f"RUNTIME_EVENT :: {item.get('event') or 'error'} :: "
                       f"{str(item.get('text') or '')[:1000]}")
                if pos:
                    row += f" :: location={pos}"
                lines.append(row)
                stack = str(item.get("stack") or "").strip()
                if stack:
                    lines.append("stack=" + stack[:3500])
        cons = ev.get("console") or []
        if cons:
            lines.append("\n## Browser console")
            lines.extend(str(x)[:1400] for x in cons[-8:])
        dom = str(ev.get("dom") or "")
        if dom:
            lines.append("\n## DOM at failure\n" + dom[:7000])

        # Source graph tool: owner plus direct @/ local imports.
        if target:
            body = self.source_of(target, 12_000)
            if body:
                lines.append(f"\n## Source owner: {target}\n```js\n{body[:12000]}\n```")
                children = []
                for spec in dict.fromkeys(re.findall(
                        r"from\s+['\"]@/(components|lib)/([\w./-]+)['\"]", body)):
                    base = f"{spec[0]}/{spec[1]}"
                    for ext in (".jsx", ".js", ""):
                        rel = base + ext
                        child = self.source_of(rel, 5000)
                        if child:
                            children.append((rel, child))
                            break
                    if len(children) >= 4:
                        break
                for rel, child in children:
                    lines.append(f"\n### direct dependency {rel}\n```js\n{child[:5000]}\n```")

                api_paths = []
                for path in re.findall(r"['\"](/api/[A-Za-z0-9_./\[\]-]+)", body):
                    clean = path.split("?")[0].rstrip("/") or "/"
                    if clean not in api_paths:
                        api_paths.append(clean)
                files = getattr(self.arch, "files", None) or {}
                for api_path in api_paths[:6]:
                    seg = api_path[len("/api/"):]
                    exact = f"app/api/{seg}/route.js"
                    candidates = [exact]
                    # Concrete IDs may correspond to [id] route files.
                    parts = seg.split("/") if seg else []
                    if parts:
                        for i in range(len(parts)):
                            dyn = list(parts)
                            dyn[i] = "[id]"
                            candidates.append("app/api/" + "/".join(dyn) + "/route.js")
                    rel = next((x for x in candidates if x in files), "")
                    if rel:
                        lines.append(f"\n### referenced API {rel}\n```js\n{str(files.get(rel) or '')[:7000]}\n```")

        if dev_trace.strip():
            lines.append("\n## Next/dev-server trace\n" + dev_trace[-7000:])
        return "\n".join(lines)[:42_000]

    def global_integrity(self) -> list:
        """Run only the role-separation gate after business journeys."""
        spec = "tests/e2e/__global-integrity__.spec.js"
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            return [TestFailure(
                test_file=spec, target="", name="global Playwright runtime",
                message=f"Playwright is unavailable: {e}"[:500], stack="",
                kind=KIND_CRASH)]

        failures = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(viewport={"width": 1280, "height": 800})
                self._install_route_loop_guard(ctx)
                page = ctx.new_page()
                try:
                    page.goto(self.base_url + "/", timeout=GOTO_TIMEOUT,
                              wait_until="domcontentloaded")
                except Exception:
                    pass
                accs = self.accounts()
                if accs and not self._sign_in(page, accs[0]):
                    failures.append(TestFailure(
                        test_file=spec, target="app/login/page.jsx",
                        name="global authentication precheck",
                        message="demo account sign-in did not establish a session",
                        stack=self._selector_inventory(page)[:5000],
                        kind=KIND_BEHAVIOR))
                for r, msg, seen in self.role_separation(page):
                    failures.append(TestFailure(
                        test_file=spec, target=self._page_for(r),
                        name=f"{r} is not protected", message=msg[:500],
                        stack=seen[:5000], kind=KIND_BEHAVIOR))
                # The app's own route table and demo accounts decide
                if accs:
                    self._sign_in(page, accs[0])
                for route, msg in self.absent_record_integrity(page):
                    failures.append(TestFailure(
                        test_file=spec, target=self._page_for(route),
                        name=f"{route} breaks on a record that is not there",
                        message=msg[:500], stack="", kind=KIND_CRASH))
                for what, msg in self.credential_integrity(page):
                    failures.append(TestFailure(
                        test_file=spec, target="app/login/page.jsx",
                        name=f"sign-in accepts {what}",
                        message=msg[:500], stack="", kind=KIND_BEHAVIOR))

                loop_fault = self._consume_route_loop_fault()
                if loop_fault:
                    route = re.search(r"NAVIGATION_LOOP:\s*([^\s]+)", loop_fault)
                    route = route.group(1) if route else ""
                    failures.append(TestFailure(
                        test_file=spec, target=self._page_for(route),
                        name="same-page navigation loop", message=loop_fault[:500],
                        stack="", kind=KIND_BEHAVIOR))
                browser.close()
        except Exception as e:
            log.debug(f"global integrity: {e}")
            failures.append(TestFailure(
                test_file=spec, target="", name="global E2E integrity",
                message=f"{type(e).__name__}: {e}"[:500], stack="", kind=KIND_CRASH))
        return failures

    SWEEP_SKIP_RE = re.compile(r"log[-_ ]?out|sign[-_ ]?out", re.I)
    def _sign_in(self, page, acc) -> bool:
        """Sign in through Better Auth's own endpoint, not through the form."""
        try:

            page.context.clear_cookies()
            page.evaluate(
                """async ({ email, password }) => {
                    const r = await fetch('/api/auth/sign-in/email', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email, password }),
                    });
                    if (!r.ok) throw new Error('sign-in ' + r.status);
                }""",
                {"email": acc.get("email"), "password": acc.get("password")})
            return self._session_alive(page, timeout_ms=4000)
        except Exception as e:
            log.debug(f"sign in as {acc.get('email')}: {e}")
            return False

    def _session_alive(self, page, timeout_ms: int = 3200) -> bool:
        """Wait briefly for Better Auth to expose a real browser session."""
        deadline = time.time() + max(700, timeout_ms) / 1000
        while time.time() < deadline:
            try:
                data = page.evaluate(
                    """async () => {
                        try {
                          const r = await fetch('/api/auth/get-session', {cache:'no-store'})
                          if (!r.ok) return null
                          return await r.json()
                        } catch (_) { return null }
                    }""")
                if data and (data.get("user") if isinstance(data, dict) else data):
                    return True
            except Exception:
                pass
            try:
                page.wait_for_timeout(425)
            except Exception:
                time.sleep(0.425)
        return False

    @staticmethod
    def _is_auth_action(st) -> bool:
        if getattr(st, "verb", "") != "CLICK":
            return False
        sel = getattr(st, "selector", None)
        text = " ".join([str(getattr(sel, "pattern", "") or ""),
                         str(getattr(sel, "role", "") or "")]).lower()
        return bool(re.search(r"sign.?in|log.?in|login|authenticate", text))

    def role_separation(self, page) -> list:
        """Every demo role, against every other role's pages."""
        accs = [a for a in self.accounts() if a.get("role")]
        if len(accs) < 2:
            return []
        try:
            routes = [u for u, m in sorted(self.az.enumerate_routes().items())
                      if m.get("kind") == "page" and "[" not in u
                      and u not in ("/login", "/signup", "/")]
        except Exception as e:
            log.debug(f"role separation routes: {e}")
            return []
        if not routes:
            return []
        routes = routes[:self.SWEEP_CAP]

        self._log("INFO", f"   🔒 Checking {len(routes)} page(s) against "
                          f"{len(accs)} role(s)")

        reach = {}
        for acc in accs:
            role = acc.get("role") or acc.get("email", "?")
            if not self._sign_in(page, acc):
                log.debug(f"role separation: could not sign in as {role}")
                continue
            opened = set()
            for url in routes:
                try:
                    try:
                        page.goto(self.base_url + url, timeout=GOTO_TIMEOUT,
                                  wait_until="domcontentloaded")
                    except Exception as e:
                        if "ERR_ABORTED" not in str(e):
                            raise
                        page.wait_for_timeout(250)
                        page.goto(self.base_url + url, timeout=GOTO_TIMEOUT,
                                  wait_until="domcontentloaded")
                    if _landed_on(page.url, url):
                        opened.add(url)
                except Exception as e:
                    log.debug(f"role separation {role} {url}: {e}")
            reach[role] = opened

        allowed = {u: {r for r, o in reach.items() if u in o} for u in routes}
        sections = {}
        for u in routes:
            sections.setdefault(u.strip("/").split("/")[0], []).append(u)

        out = []
        for name, urls in sections.items():
            if len(urls) < 2:
                continue

            tightest = min(allowed[u] for u in urls if allowed[u]) \
                if any(allowed[u] for u in urls) else set()
            if not tightest or len(tightest) >= len(reach):
                continue
            for u in urls:
                extra = allowed[u] - tightest

                extra = {r for r in sorted(extra)
                         if self._still_open(page, u, r, accs)}
                if extra:
                    out.append((u, f"/{name} admits only "
                                   f"{', '.join(sorted(tightest))}, and {u} "
                                   f"also opens for "
                                   f"{', '.join(sorted(extra))} — the guard on "
                                   f"the section is missing from this page",
                                self._reach_table(urls, reach)))
        return out

    @staticmethod
    def _reach_table(urls, reach) -> str:
        """The measurement the finding is made of: who reached what."""
        wide = max((len(u) for u in urls), default=10)
        lines = ["what each role reached (the reading this is based on):"]
        for role in sorted(reach):
            got = ", ".join(f"{u}{'  OPEN' if u in reach[role] else '  redirected'}"
                            for u in sorted(urls))
            lines.append(f"  {role:>10}: {got}")
        del wide
        return "\n".join(lines)

    def _still_open(self, page, url, role, accs) -> bool:
        """Ask again, down a different road, before calling a page unguarded."""
        acc = next((a for a in accs if (a.get("role") or "") == role), None)
        if not acc:
            return True
        if not self._sign_in(page, acc):
            return True
        try:
            jar = {c["name"]: c["value"] for c in page.context.cookies()}
        except Exception as e:
            log.debug(f"re-check {role} {url}: no cookies: {e}")
            return True
        if not jar:
            return True
        try:
            r = requests.get(self.base_url + url, cookies=jar,
                             allow_redirects=False, timeout=20)
        except Exception as e:
            log.debug(f"re-check {role} {url}: {e}")
            return True
        if r.status_code in (301, 302, 303, 307, 308):
            self._log("INFO", f"   ✓ {url} answers {role} with "
                              f"{r.status_code} → {r.headers.get('location', '?')}"
                              f" — it is guarded, not reporting it")
            return False
        return True
