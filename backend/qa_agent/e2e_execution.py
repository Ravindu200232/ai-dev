"""Browser execution, failure capture, scenario patching and checkpoint resume."""
import base64
from .e2e_common import *


class E2EExecutionMixin:
    def _business_state_at(self, sc, start_index: int) -> bool:
        """Restore whether a resumed checkpoint is already post-mutation."""
        return any(self._is_business_step(step)
                   for step in sc.steps[:max(0, int(start_index or 0))])

    def run(self, sc: Scenario, journey: dict = None, *, global_checks: bool = False,
            start_index: int = 0, initial_url: str = "", storage_state=None) -> list:
        """Drive the scenario in a real browser."""
        try:
            from playwright.sync_api import TimeoutError as PWTimeout
            from playwright.sync_api import sync_playwright
        except Exception as e:
            self._log("WARN", f"   ⚠ Playwright is not available: {e}")
            return [TestFailure(
                test_file=sc.spec_path(), target="", name="Playwright runtime",
                message=f"Playwright is unavailable: {e}"[:500], stack="",
                kind=KIND_CRASH)]

        self._scenario_changed = False
        self._active_journey = journey or {}
        self._begin_mutation_memory(sc)
        self._business_started = self._business_state_at(sc, start_index)
        self.warm(sc)
        spec = sc.spec_path()
        failures, problems, net, api_net = [], [], [], []
        mutation_events = []
        runtime_events = []
        route = initial_url or "/"
        active_label = ""
        step_trace = []
        resume_auth_error = ""
        current_step_index = -1
        self._fire("on_e2e_event", {
            "state": "journey_start",
            "title": str((journey or {}).get("title") or "End-to-end journey"),
            "role": str((journey or {}).get("role") or ""),
            "route": route,
            "total": len(sc.steps),
        })

        def live_frame(page):
            """Small in-memory frame for the Studio's live E2E browser."""
            if not self.cb.get("on_e2e_event"):
                return ""
            try:
                raw = page.screenshot(type="jpeg", quality=48, full_page=False)
                return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
            except Exception:
                return ""

        def fail(kind, name, message, stack=""):
            failures.append(TestFailure(
                test_file=spec, target=self._page_for(route), name=name[:200],
                message=message[:500], stack=stack[:5000], kind=kind))

        def context(n=6):
            """What the browser saw, console first and then the requests."""
            return "\n".join(problems[-n:] + net[-n:])

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
            if storage_state:
                ctx_kwargs["storage_state"] = storage_state
            ctx = browser.new_context(**ctx_kwargs)
            self._install_route_loop_guard(ctx)
            page = ctx.new_page()
            if initial_url:
                try:
                    page.goto(self.base_url + initial_url, timeout=GOTO_TIMEOUT,
                              wait_until="domcontentloaded")
                    route = self._route_from_page(page, include_query=True) or initial_url
                except Exception as e:
                    log.debug(f"resume initial navigation {initial_url}: {e}")
                if start_index > 0:
                    ok_auth, auth_note = self._restore_checkpoint_session(
                        page, journey=journey, scenario=sc, route=initial_url)
                    if ok_auth:
                        route = self._route_from_page(page, include_query=True) or initial_url
                    else:
                        resume_auth_error = auth_note
                        self._log("WARN", f"   ⚠ {auth_note}")
            def _loc_dict(loc):
                loc = loc or {}
                try:
                    return {
                        "url": str(loc.get("url") or ""),
                        "line": int(loc.get("lineNumber") or 0),
                        "column": int(loc.get("columnNumber") or 0),
                    }
                except Exception:
                    return {"url": str((loc or {}).get("url") or ""),
                            "line": 0, "column": 0}

            def _pageerror(e):
                text = str(e or "")
                try:
                    stack = str(getattr(e, "stack", "") or "")
                except Exception:
                    stack = ""
                if stack and stack.strip() != text.strip():
                    row = f"PageError: {text}\n{stack}"
                else:
                    row = f"PageError: {text}"
                problems.append(row[:6000])
                runtime_events.append({
                    "event": "pageerror", "text": text[:1200],
                    "stack": stack[:6000], "url": "", "line": 0, "column": 0,
                })

            page.on("pageerror", _pageerror)

            def _console(m):
                if m.type != "error":
                    return
                loc = _loc_dict(getattr(m, "location", None))
                pos = ""
                if loc["url"]:
                    pos = loc["url"]
                    if loc["line"]:
                        pos += f":{loc['line']}"
                        if loc["column"]:
                            pos += f":{loc['column']}"
                row = f"ConsoleError: {m.text}" + (f"  [{pos}]" if pos else "")
                if self._console_is_replay_duplicate(str(m.text or ""),
                                                     loc["url"]):
                    return
                problems.append(row[:4000])
                runtime_events.append({
                    "event": "console.error", "text": str(m.text or "")[:1600],
                    "stack": "", "url": loc["url"],
                    "line": loc["line"], "column": loc["column"],
                })

            page.on("console", _console)

            def _safe_payload(value):
                """Keep request evidence useful without leaking credentials."""
                text = str(value or "")[:2200]
                text = re.sub(
                    r'(?i)(password|passwd|token|secret|authorization|cookie|session)'
                    r'(["\'\s:=&]+)([^&\s,"}]+)',
                    lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
                return " ".join(text.split())[:1600]

            def _response(r):
                # Keep API traffic and successful business mutations.
                is_api = "/api/" in r.url
                method = str(r.request.method or "").upper()
                is_mutation = method in ("POST", "PUT", "PATCH", "DELETE")
                if r.status < 400 and not is_api and not is_mutation:
                    return
                body = ""
                try:
                    body = " ".join(r.text().split())[:700]
                except Exception:
                    pass
                req = r.request
                content_type = ""
                post_data = ""
                try:
                    headers = req.headers or {}
                    content_type = str(headers.get("content-type") or "")[:180]
                except Exception:
                    pass
                try:
                    post_data = _safe_payload(req.post_data or "")
                except Exception:
                    pass
                request_bits = []
                if content_type:
                    request_bits.append(f"content-type={content_type}")
                if post_data:
                    request_bits.append(f"request-body={post_data}")
                req_meta = (" | " + " | ".join(request_bits)) if request_bits else ""
                row = f"HTTP {r.status} {req.method} {r.url}{req_meta}" \
                      + (f" — response={body}" if body else "")
                if r.status >= 400 and self._is_replay_duplicate(
                        method, r.url, r.status):
                    self._note_replay_duplicate(r.url)
                    return
                if r.status >= 400:
                    net.append(row[:1800])
                if is_api:
                    key = f"{req.method} {r.url}"
                    api_net[:] = [x for x in api_net if not x.startswith(key + " :: ")]
                    api_net.append(key + " :: " + row[:2200])
                    if len(api_net) > 30:
                        del api_net[:-30]
                if r.status < 300 and is_mutation:
                    self._note_mutation_ok(method, r.url)
                    mutation_events.append({
                        "method": method, "url": str(r.url or "")[:900],
                        "status": int(r.status), "step_index": int(current_step_index),
                        "request": post_data[:900], "response": body[:700],
                    })

            def _request_failed(req):
                if "/api/" not in str(getattr(req, "url", "") or ""):
                    return
                reason = ""
                try:
                    reason = str(req.failure or "")
                except Exception:
                    pass
                row = f"REQUEST_FAILED {req.method} {req.url} — {reason or 'unknown network failure'}"
                net.append(row[:1200])
                api_net.append(f"{req.method} {req.url} :: {row[:1200]}")

            page.on("response", _response)
            page.on("requestfailed", _request_failed)

            try:
                for idx, st in enumerate(sc.steps[start_index:], start_index):
                    current_step_index = idx
                    route_before = route
                    mutation_before = len(mutation_events)
                    if st.verb == "GOTO":
                        route = st.value
                    self._intended_route = route
                    label = st.describe()
                    active_label = label
                    self._fire("on_e2e_event", {
                        "state": "step", "index": idx + 1, "total": len(sc.steps),
                        "label": label, "verb": str(getattr(st, "verb", "") or ""),
                        "value": str(getattr(st, "value", "") or ""),
                        "route": route,
                        "title": str((journey or {}).get("title") or "End-to-end journey"),
                        "role": str((journey or {}).get("role") or ""),
                    })
                    next_step = sc.steps[idx + 1] if idx + 1 < len(sc.steps) else None
                    net_before = len(net)
                    try:
                        if resume_auth_error:
                            err = (KIND_BEHAVIOR, resume_auth_error)
                        else:
                            err = self._step(page, st, next_step=next_step)
                    except PWTimeout:

                        if st.verb == "GOTO":
                            err = (KIND_CRASH,
                                   f"{st.value} never finished loading within "
                                   f"{GOTO_TIMEOUT // 1000}s")
                        elif st.selector:
                            err = (KIND_SELECTOR,
                                   f"nothing matched {st.selector.describe()}")
                        else:
                            err = (KIND_CRASH, "the step timed out")
                    except Exception as e:
                        err = (KIND_CRASH, f"{type(e).__name__}: {e}")
                    loop_fault = self._consume_route_loop_fault()
                    if loop_fault:
                        err = (KIND_BEHAVIOR, loop_fault)
                        problems.append(loop_fault)
                    actual = self._route_from_page(page, include_query=True)
                    if actual:
                        route = actual
                    step_trace.append({"index": idx, "step": label,
                                       "before": route_before, "after": route,
                                       "ok": not bool(err)})
                    self._fire("on_e2e_event", {
                        "state": "step_done" if not err else "step_failed",
                        "index": idx + 1, "total": len(sc.steps),
                        "label": label, "route": route, "ok": not bool(err),
                        "message": str((err or ("", ""))[1] if err else ""),
                        "title": str((journey or {}).get("title") or "End-to-end journey"),
                        "role": str((journey or {}).get("role") or ""),
                        "frame": live_frame(page),
                    })
                    if (not err and (getattr(self, "_active_journey", {}) or {}).get("role")):
                        recent_401 = [x for x in net[net_before:]
                                      if re.search(r"HTTP\s+401\b", x)]
                        if recent_401:
                            err = (KIND_BEHAVIOR,
                                   "authenticated journey received HTTP 401: "
                                   + recent_401[0][:260])
                    if err:
                        kind, msg = err
                        self._test("fail", label, msg)
                        evidence = context()
                        if kind in (KIND_SELECTOR, "ASSERTION", KIND_BEHAVIOR):
                            inv = self._selector_inventory(page)
                            evidence = (inv + "\n" + evidence).strip()
                        # Freeze the state before the browser closes.
                        try:
                            sess = page.evaluate(
                                """async () => { try { const r = await fetch('/api/auth/get-session', {cache:'no-store'}); return {status:r.status, body:await r.json()}; } catch (e) { return {status:0, error:String(e)}; } }""")
                        except Exception as e:
                            sess = {"status": 0, "error": str(e)[:160]}
                        try:
                            cookies = ctx.cookies()
                            self._last_cookie_jar = {c.get("name", ""): c.get("value", "")
                                                     for c in cookies if c.get("name")}
                            cookie_names = [c.get("name", "") for c in cookies if c.get("name")]
                        except Exception:
                            self._last_cookie_jar = {}
                            cookie_names = []
                        try:
                            storage = ctx.storage_state()
                        except Exception:
                            storage = None
                        self._last_run_evidence = {
                            "route": route, "target": self._page_for(route),
                            "route_before": route_before,
                            "resume_from": initial_url or "",
                            "resume_start_index": start_index,
                            "resume_auth_error": resume_auth_error,
                            "failed_index": idx,
                            "step": label, "kind": kind, "message": msg,
                            "dom": self._selector_inventory(page, limit=20),
                            "fields": self._field_rows(page, limit=80),
                            "console": list(problems[-10:]),
                            "runtime_events": list(runtime_events[-12:]),
                            "network_errors": list(net[-12:]),
                            "step_network_errors": list(net[net_before:][-8:]),
                            "api_network": list(api_net[-20:]),
                            "mutation_events": list(mutation_events[-12:]),
                            "session": sess, "cookie_names": cookie_names,
                            "storage_state": storage,
                            "checkpoint_values": self._checkpoint_business_values(route, sc, idx),
                            "step_trace": list(step_trace[-40:]),
                            "last_good_index": max(
                                (row.get("index", -1) for row in step_trace if row.get("ok")),
                                default=-1),
                            "journey": dict(journey or {}),
                        }
                        fail(kind, label, msg, evidence)
                        break
                    if self._is_business_step(st) or len(mutation_events) > mutation_before:
                        self._business_started = True
                    self._test("pass", label)
                else:
                    try:
                        final_session = self._session_snapshot(page)
                    except Exception:
                        final_session = {}
                    try:
                        final_storage = ctx.storage_state()
                    except Exception:
                        final_storage = None
                    self._last_run_evidence = {
                        "completed": True, "route": route,
                        "target": self._page_for(route),
                        "failed_index": -1, "last_good_index": len(sc.steps) - 1,
                        "api_network": list(api_net[-20:]),
                        "mutation_events": list(mutation_events[-12:]),
                        "session": final_session, "storage_state": final_storage,
                        "checkpoint_values": self._checkpoint_business_values(route, sc, len(sc.steps)),
                        "step_trace": list(step_trace[-40:]),
                        "journey": dict(journey or {}),
                    }
                    self._fire("on_e2e_event", {
                        "state": "journey_done", "route": route,
                        "index": len(sc.steps), "total": len(sc.steps),
                        "title": str((journey or {}).get("title") or "End-to-end journey"),
                        "role": str((journey or {}).get("role") or ""),
                    })

                    # Journey runs prove the journey only.
                    if global_checks:
                        for r, msg in self.sweep(page):
                            route = r
                            self._test("fail", f"{r} is broken", msg)
                            fail(KIND_CRASH, f"{r} is broken", msg, context())
                        for r, msg, seen in self.role_separation(page):
                            route = r
                            self._test("fail", f"{r} is not protected", msg)
                            fail(KIND_CRASH, f"{r} is not protected", msg,
                                 seen + "\n" + context())
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        real = [p for p in problems if not _is_noise(p)]
        if real and not failures:
            fail(KIND_CRASH, "the page logged an error",
                 real[0][:300], "\n".join(real[:6]))

        if getattr(self, "_scenario_changed", False):
            self.write_spec(sc)
        return failures

    def _failure_step_index(self, sc: Scenario, failure) -> int:
        """Map a TestFailure back to the exact parsed scenario step."""
        name = str(getattr(failure, "name", "") or "").strip()
        for i, st in enumerate(sc.steps):
            if st.describe() == name:
                return i
        ev = getattr(self, "_last_run_evidence", {}) or {}
        try:
            idx = int(ev.get("failed_index"))
            if 0 <= idx < len(sc.steps):
                return idx
        except Exception:
            pass
        return -1

    @staticmethod
    def _canonical_patch_selector(text: str) -> str:
        """Translate LLM human locator shorthand into AgentForge grammar."""
        t = str(text or "").strip().strip("`")
        m = re.fullmatch(r"field\s+['\"]?([^'\"]+)['\"]?", t, re.I)
        if m:
            return "field=" + m.group(1).strip()
        m = re.fullmatch(r"(testid|label|placeholder|href|text)\s+['\"](.+?)['\"]", t, re.I)
        if m:
            return f"{m.group(1).lower()}={m.group(2)}"
        m = re.fullmatch(r"(?:role\s+)?(button|link|combobox|checkbox|radio|textbox|spinbutton|option)\s+(.+)", t, re.I)
        if m:
            return f"role={m.group(1).lower()} name={m.group(2).strip()}"
        return t

    def _normalize_test_patch(self, old, patch_line: str) -> str:
        """Make a diagnosis patch structural instead of trusting prose syntax."""
        patch = str(patch_line or "").strip().strip("`")
        if not patch or patch.upper() == "DROP":
            return patch
        verbs = {"GOTO","FILL","SELECT","CLICK","EXPECT_TEXT","EXPECT_URL",
                 "EXPECT_VALUE","WAIT_FOR","EXPECT_NO_ERROR"}
        head = patch.partition("::")[0].strip().upper()
        if head in verbs:
            parts = [x.strip() for x in patch.split("::")]
            if len(parts) >= 2 and head not in ("GOTO", "EXPECT_TEXT", "EXPECT_URL"):
                parts[1] = self._canonical_patch_selector(parts[1])
                return " :: ".join(parts)
            return patch
        selector = self._canonical_patch_selector(patch)
        if old.verb in ("FILL", "SELECT", "EXPECT_VALUE"):
            return f"{old.verb} :: {selector} :: {old.value}"
        if old.verb in ("CLICK", "WAIT_FOR"):
            return f"{old.verb} :: {selector}"
        if old.verb in ("EXPECT_TEXT", "EXPECT_URL"):
            return f"{old.verb} :: {selector}"
        return patch

    _MANGLED_RE = re.compile(
        r"\b(?:role|testid|placeholder|label|text)\s*[=']"
        # Text /Footwear/i — the same wrapping written with a regex.
        r"|^\s*(?:role|testid|placeholder|label|text)\s+/"
        r"|\bindex\s*=", re.I)

    def _selector_is_mangled(self, step) -> bool:
        """Whether a patched step's selector was wrapped around another one."""
        sel = getattr(step, "selector", None)
        pat = str(getattr(sel, "pattern", "") or "")
        return bool(pat) and bool(self._MANGLED_RE.search(pat))

    # A create that already succeeded
    _DUPLICATE_STATUSES = frozenset({400, 403, 409, 422})

    @staticmethod
    def _endpoint_key(method: str, url: str) -> str:
        try:
            path = urlparse(str(url or "")).path or "/"
        except Exception:
            path = str(url or "")
        path = re.sub(r"/[0-9a-f]{24}(?=/|$)", "/<id>", path)
        path = re.sub(r"/\d+(?=/|$)", "/<id>", path)
        return f"{str(method or '').upper()} {path.rstrip('/') or '/'}"

    def _begin_mutation_memory(self, sc) -> None:
        """Keep the success ledger across replays of one journey, not beyond."""
        title = str(getattr(sc, "title", "") or "")
        if getattr(self, "_mutation_journey", None) != title:
            self._mutation_journey = title
            self._mutation_ok = set()
            self._replay_dupe_urls = set()

    def _note_mutation_ok(self, method: str, url: str) -> None:
        try:
            self._mutation_ok.add(self._endpoint_key(method, url))
        except AttributeError:
            self._mutation_ok = {self._endpoint_key(method, url)}

    def _is_replay_duplicate(self, method: str, url: str, status: int) -> bool:
        if int(status or 0) not in self._DUPLICATE_STATUSES:
            return False
        if str(method or "").upper() not in ("POST", "PUT", "PATCH", "DELETE"):
            return False
        return self._endpoint_key(method, url) in getattr(self, "_mutation_ok",
                                                          set())

    def _note_replay_duplicate(self, url: str) -> None:
        try:
            self._replay_dupe_urls.add(str(url or ""))
        except AttributeError:
            self._replay_dupe_urls = {str(url or "")}
        if not getattr(self, "_replay_dupe_logged", False):
            self._replay_dupe_logged = True
            self._log("INFO", "   ♻ a create that already succeeded in this "
                              "journey was replayed and refused — fixture "
                              "reuse, not an app fault")

    def _console_is_replay_duplicate(self, text: str, url: str) -> bool:
        seen = getattr(self, "_replay_dupe_urls", set())
        if not seen:
            return False
        if url and any(url.startswith(u) or u.startswith(url) for u in seen):
            return True
        blob = str(text or "")
        return bool(re.search(r"\b(?:400|403|409|422)\b", blob)) and \
            any(u.split("?", 1)[0] in blob for u in seen)

    def patch_scenario_step(self, sc: Scenario, failure, patch_line: str) -> bool:
        """Apply one diagnosis-approved TEST_FIX without regenerating the test."""
        idx = self._failure_step_index(sc, failure)
        if idx < 0:
            return False
        patch = self._normalize_test_patch(sc.steps[idx], patch_line)
        if not patch:
            return False
        if patch.upper() == "DROP":
            if sc.steps[idx].verb not in ("WAIT_FOR", "EXPECT_TEXT", "EXPECT_URL",
                                          "EXPECT_VALUE", "EXPECT_NO_ERROR"):
                return False
            del sc.steps[idx]
            self._scenario_changed = True
            return True

        prefix = f"FLOW :: patch\nAS :: {sc.role or 'SIGNED OUT'}\n"
        mini = parse_scenario(prefix + patch + "\nDONE",
                              account=self.account_for(sc.role))
        # A patch can smuggle the same wrapped grammar authoring
        try:
            from .e2e_normalize import normalize_scenario_selectors
            normalize_scenario_selectors(mini)
        except Exception as e:
            log.debug(f"patch normalize: {e}")
        if len(mini.steps) != 1:
            return False
        replacement = mini.steps[0]
        if self._selector_is_mangled(replacement):
            return False
        old_step = sc.steps[idx]
        if replacement.describe() == old_step.describe():
            return False
        old_action = sc.steps[idx].verb in ("FILL", "SELECT", "CLICK")
        new_action = replacement.verb in ("FILL", "SELECT", "CLICK")
        if old_action and not new_action:
            return False
        # A patch is only a fix if the DOM actually has what it now
        ok, why = self.verify_selector_live(sc, replacement, failure)
        if not ok:
            self._last_patch_rejection = why
            self._log("WARN", f"   🛑 patch rejected — {why}")
            return False
        self._last_patch_rejection = ""
        sc.steps[idx] = replacement
        self._scenario_changed = True
        return True

    def _resume_point(self, sc: Scenario, failure) -> tuple:
        """Return (start_index, exact_route, storage_state) for a focused rerun."""
        ev = dict(getattr(self, "_last_run_evidence", {}) or {})
        idx = self._failure_step_index(sc, failure)
        if idx < 0:
            return 0, "", None
        target_route = str(ev.get("route_before") or ev.get("route") or "").strip()
        if not target_route.startswith("/"):
            return 0, "", None
        if target_route.startswith("/api/"):
            return 0, "", None
        trace = list(ev.get("step_trace") or [])
        start = idx
        for row in reversed(trace):
            try:
                j = int(row.get("index"))
            except Exception:
                continue
            if j >= idx:
                continue
            if row.get("after") == target_route:
                start = j + 1
                continue
            if start != idx:
                break
        while start > 0 and sc.steps[start - 1].verb in ("FILL", "SELECT"):
            start -= 1
        return max(0, start), target_route, ev.get("storage_state")

    def run_resume(self, sc: Scenario, journey: dict = None, failure=None) -> list:
        """Focused post-repair rerun from the last trustworthy browser checkpoint."""
        if failure is None:
            return self.run(sc, journey=journey)
        start, route, state = self._resume_point(sc, failure)
        if start <= 0 or not route or not state:
            return self.run(sc, journey=journey)
        self._log("INFO", f"   ⏩ E2E checkpoint resume — {route} from step {start + 1}/{len(sc.steps)}")
        prior_evidence = dict(getattr(self, "_last_run_evidence", {}) or {})
        failures = self.run(sc, journey=journey, start_index=start,
                            initial_url=route, storage_state=state)
        current = dict(getattr(self, "_last_run_evidence", {}) or {})
        prior_mutations = list(prior_evidence.get("mutation_events") or [])
        if prior_mutations:
            seen = {(str(x.get("method")), str(x.get("url")), int(x.get("status") or 0), int(x.get("step_index") or -1))
                    for x in (current.get("mutation_events") or []) if isinstance(x, dict)}
            merged = list(current.get("mutation_events") or [])
            for row in prior_mutations:
                if not isinstance(row, dict):
                    continue
                key = (str(row.get("method")), str(row.get("url")), int(row.get("status") or 0), int(row.get("step_index") or -1))
                if key not in seen:
                    merged.insert(0, row)
                    seen.add(key)
            current["mutation_events"] = merged[-20:]
            self._last_run_evidence = current
        if failures:
            ev = dict(getattr(self, "_last_run_evidence", {}) or {})
            sess = ev.get("session") or {}
            body = sess.get("body") if isinstance(sess, dict) else None
            body_text = json.dumps(body, ensure_ascii=False).lower() if body is not None else ""
            route_now = str(ev.get("route") or "")
            msg = " ".join(str(getattr(f, "message", "") or "") for f in failures)
            role = str((journey or {}).get("role") or sc.role or "").strip().lower()
            signed_out = set(getattr(self, "_SIGNED_OUT", ()) or ())
            requires_session = bool(role and role not in signed_out)
            truly_stale = requires_session and (
                route_now.split("?", 1)[0] == "/login" or
                re.search(r"HTTP\s+401\b", msg, re.I) or
                (isinstance(sess, dict) and sess.get("status") in (401, 403)) or
                (body is None and isinstance(sess, dict) and sess.get("status") == 200) or
                (body_text in ("null", "{}") and needs_session(route_now))
            )
            if truly_stale:
                self._log("WARN", "   🔐 checkpoint auth could not be restored; preserving the focused failure for AUTH_FIX instead of replaying business steps")
        return failures
