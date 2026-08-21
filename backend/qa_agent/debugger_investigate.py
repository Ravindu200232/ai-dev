"""Agentic E2E investigation loop, tool execution and runtime-source prioritization."""
from .debugger_common import *


class DebuggerInvestigateMixin:
    def __init__(self, agent, arch, analyzer=None, model: str | None = None,
                 notebook: DebugNotebook | None = None):
        self.agent = agent
        self.arch = arch
        self.analyzer = analyzer
        self.model = model
        self.notebook = notebook or DebugNotebook()
        self._tool_cache: dict[str, str] = {}
        self._runtime_frame_cache: list[dict] = []
        self._rejected_patches: list[str] = []

    def note_rejected_patch(self, why: str) -> None:
        """Remember a patch the live DOM refused, so it is not proposed twice."""
        why = str(why or "").strip()
        if why and why not in self._rejected_patches:
            self._rejected_patches.append(why)
            del self._rejected_patches[:-4]

    def investigate(self, failures, *, journey=None, scenario=None,
                    dev_trace: str = "") -> dict:
        failures = list(failures or [])
        self.notebook.remember_failure(failures)
        if journey and not self.notebook.goal:
            self.notebook.goal = str(journey.get("title") or "")

        packet = self.agent.incident_evidence(
            failures, journey=journey, dev_trace=dev_trace)
        try:
            ev = dict(getattr(self.agent, "_last_run_evidence", {}) or {})
            self.notebook.last_good_step = max(
                self.notebook.last_good_step, int(ev.get("last_good_index", -1) or -1))
        except Exception:
            pass
        deterministic = diagnose_checkpoint_auth(
            self.agent, self.arch, failures, packet)
        if not deterministic:
            deterministic = diagnose_navigation_loop(
                self.agent, self.arch, failures, packet)
        if not deterministic:
            deterministic = diagnose_page_health(
                self.agent, self.arch, failures, packet)
        if not deterministic:
            deterministic = diagnose_request_contract(
                self.agent, self.arch, failures, packet)
        if not deterministic:
            deterministic = diagnose_api_http_error(
                self.agent, self.arch, failures, packet)
        if not deterministic:
            deterministic = diagnose_native_form_submit(
                self.agent, self.arch, failures, packet)
        if not deterministic:
            deterministic = diagnose_broken_images(
                self.agent, self.arch, failures, packet)
        if not deterministic:
            deterministic = diagnose_post_mutation_proof(
                self.agent, self.arch, failures, scenario)
        if deterministic:
            dh = _norm(deterministic.get("hypothesis") or deterministic.get("root") or "")
            if not dh or dh not in self.notebook.rejected_hypotheses:
                self.notebook.record_decision(deterministic)
                deterministic["packet"] = packet
                return deterministic

        # Runtime exceptions outrank selector symptoms.
        runtime_frames = self._runtime_locations(packet)
        self._runtime_frame_cache = runtime_frames
        concrete_runtime = bool(runtime_frames) or bool(re.search(
            r"HTTP\s+[45]\d\d\b|REQUEST_FAILED|PageError:|ConsoleError:|"
            r"ReferenceError:|TypeError:|SyntaxError:",
            str(packet or ""), re.I))

        pure_selector = bool(failures) and all(
            str(getattr(f, "kind", "")).upper() == "SELECTOR"
            for f in failures)
        if pure_selector and scenario is not None and not concrete_runtime:
            try:
                exact_patch = self.agent.dom_grounded_test_patch(
                    failures[0], scenario)
            except Exception:
                exact_patch = ""
            if exact_patch:
                decision = {
                    "verdict": "TEST_FIX",
                    "root": "the live DOM exposes a unique stable field locator for the failing scenario step",
                    "files": [],
                    "test_step": str(getattr(failures[0], "name", "") or "")[:800],
                    "test_patch": exact_patch[:1200],
                    "hypothesis": "replace only the failing locator with the exact observed DOM field",
                    "next": "apply the one-step DOM-grounded patch and resume the same browser checkpoint",
                    "confidence": "high", "privileged": [],
                    "evidence_rule": "dom-grounded-field-locator",
                    "packet": packet,
                }
                self.notebook.record_decision(decision)
                return decision
        scenario_text = ""
        try:
            scenario_text = self.agent._render(scenario)
        except Exception:
            scenario_text = ""
        transcript = [
            "## Notebook\n" + self.notebook.context(),
            "## Current scenario\n" + (scenario_text[:9000] or "(unknown)"),
            packet[:42000],
        ]
        if runtime_frames:
            locs = "\n".join(
                f"- {x['path']}:{x['line']}:{x.get('column', 0)} [{x.get('signal','runtime')}] {x.get('message','')[:180]}"
                for x in runtime_frames[:6])
            transcript.append(
                "## Controller priority: exact runtime source locations detected\n"
                + locs
                + "\nBefore blaming a selector, use TOOL :: STACK_SOURCE :: current or explain why a stronger API/auth root cause supersedes these frames.")

        last_raw = ""
        for _turn in range(MAX_TOOL_TURNS):
            raw = self._ask("\n\n".join(transcript)[-56000:])
            last_raw = raw or last_raw
            tool = self._parse_tool(raw)
            if tool:
                name, arg = tool
                key = f"{name}::{arg}".lower()
                if key in self._tool_cache:
                    obs = "Tool call refused: exact same tool+argument was already used. Choose a different tool or decide."
                else:
                    obs = self._run_tool(name, arg, failures=failures,
                                         journey=journey, scenario=scenario)
                    self._tool_cache[key] = obs
                    self.notebook.tool_calls.append(key)
                transcript.append(f"## Tool result: {name} {arg}\n{obs[:TOOL_RESULT_CHARS]}")
                continue

            decision = self._parse_decision(raw, failures)
            if decision.get("verdict") == "UNKNOWN" and not decision.get("root"):
                if _turn == 0:
                    transcript.append(
                        "## Controller feedback\nYour answer did not contain a usable verdict/root in the required protocol. "
                        "Do not re-investigate from scratch. Use the evidence already above and output the exact decision lines now, "
                        "or request one new tool if a specific fact is missing.")
                    continue
                rescue = self._fallback(failures, packet=packet, raw=raw)
                if rescue.get("verdict") != "UNKNOWN":
                    decision = rescue
            elif decision.get("verdict") == "UNKNOWN":
                rescue = self._fallback(failures, packet=packet, raw=raw)
                if rescue.get("verdict") != "UNKNOWN":
                    decision = rescue
            decision = self._apply_runtime_location_priority(
                decision, failures, runtime_frames, packet)

            pure_selector = bool(failures) and all(
                str(getattr(f, "kind", "")).upper() == "SELECTOR"
                for f in failures)
            concrete_runtime = bool(runtime_frames) or bool(re.search(
                r"HTTP\s+[45]\d\d\b|REQUEST_FAILED|PageError:|ConsoleError:",
                str(packet or ""), re.I))
            if pure_selector and not concrete_runtime:
                arb = self._selector_arbitration(failures, packet)
                if arb:
                    decision = arb

            if decision.get("verdict") == "TEST_FIX":
                arb = self._selector_arbitration(failures, packet)
                if arb and arb.get("verdict") == "APP_FIX":
                    decision = arb
                elif (arb and self._successful_mutation(packet)
                      and all(str(getattr(f, "kind", "")).upper() == "ASSERTION"
                              for f in (failures or []))
                      and not str(arb.get("root") or "").startswith("the rendered DOM contains")):
                    f0 = failures[0] if failures else None
                    target = str(getattr(f0, "target", "") or "") if f0 else ""
                    decision = {
                        "verdict":"APP_FIX",
                        "root":"the required mutation succeeded but its downstream UI/state proof is not observable",
                        "files":[target] if target else [],
                        "test_step":"", "test_patch":"",
                        "hypothesis":"persistence/action completed; the destination state or refresh path failed to expose the result",
                        "next":"inspect the post-mutation destination/state refresh and repair that production path",
                        "confidence":"high", "privileged":[]}

            hyp = _norm(decision.get("hypothesis") or decision.get("root") or "")
            shape = self.notebook.decision_shape(decision)
            if (hyp and hyp in self.notebook.rejected_hypotheses) or (
                    shape.strip("|")
                    and shape in getattr(self.notebook, "rejected_shapes", [])):
                transcript.append(
                    "## Controller feedback\nThat hypothesis already failed to move the browser. "
                    "Do not repeat it. Use a different tool/hypothesis or choose UNKNOWN.")
                continue
            decision = self._add_producer_page(decision)
            self.notebook.record_decision(decision)
            decision["packet"] = packet
            return decision

        decision = self._fallback(failures, packet=packet, raw=last_raw)
        decision = self._apply_runtime_location_priority(
            decision, failures, runtime_frames, packet)

        fb = _norm(decision.get("hypothesis") or decision.get("root") or "")
        if fb and fb in self.notebook.rejected_hypotheses:
            decision["exhausted"] = True
        decision = self._add_producer_page(decision)
        self.notebook.record_decision(decision)
        decision["packet"] = packet
        return decision

    def _add_producer_page(self, decision: dict) -> dict:
        """Also hand the fixer the page the journey navigated FROM."""
        try:
            ev = dict(getattr(self.agent, "_last_run_evidence", {}) or {})
        except Exception:                                  # noqa: BLE001
            return decision

        before = str(ev.get("route_before") or "").strip()
        route = str(ev.get("route") or "").strip()
        if not before or before == route:
            return decision

        try:
            producer = self.agent._page_for(before)
        except Exception:                                  # noqa: BLE001
            return decision
        if not producer:
            return decision

        files = [f for f in (decision.get("files") or []) if f]
        if producer in files:
            return decision
        files.append(producer)
        # One wider than the runtime-frame cap: the producer is added.
        decision["files"] = files[:5]
        return decision

    def propose_test_patch(self, failure, scenario=None) -> str:
        """One cheap, exact-step recovery when diagnosis says TEST_FIX."""
        try:
            grounded = self.agent.dom_grounded_test_patch(failure, scenario)
        except Exception:
            grounded = ""
        if grounded:
            return grounded
        ev = dict(getattr(self.agent, "_last_run_evidence", {}) or {})
        try:
            idx = self.agent._failure_step_index(scenario, failure)
            old = scenario.steps[idx].describe() if idx >= 0 else str(getattr(failure, "name", ""))
        except Exception:
            old = str(getattr(failure, "name", "") or "")
        refused = ""
        if self._rejected_patches:
            refused = ("\nAlready tried and REFUSED — the live page does not "
                       "contain these, so do not propose them again:\n"
                       + "\n".join(f"  - {r}" for r in self._rejected_patches))
        prompt = f"""
TEST_FIX was established. Repair exactly ONE scenario step; do not discuss application code.
Original failing step: {old}
Failure: {getattr(failure, 'message', '')}
Observed DOM:
{str(ev.get('dom') or '')[:6500]}
{refused}
Return exactly one line:
TEST_PATCH :: <complete AgentForge scenario line>
Examples:
TEST_PATCH :: SELECT :: field=category :: Footwear
TEST_PATCH :: FILL :: field=phone :: +15550000000
TEST_PATCH :: CLICK :: role=button name=/save/i
TEST_PATCH :: WAIT_FOR :: text=/saved/i
Only name a control the Observed DOM above literally shows. If it shows none, answer TEST_PATCH :: NONE.
Do not output locator shorthand by itself.
"""
        raw = self._ask(prompt)
        patch = self._line(raw, "TEST_PATCH", "")
        if patch:
            return patch[:1200]
        line = next((ln.strip() for ln in str(raw or "").splitlines()
                     if re.match(r"^(GOTO|FILL|SELECT|CLICK|EXPECT_TEXT|EXPECT_URL|EXPECT_VALUE|WAIT_FOR|EXPECT_NO_ERROR)\s*::", ln.strip(), re.I)), "")
        return line[:1200]

    def _ask(self, user: str) -> str:
        buf: list[str] = []
        chars = 0

        def collect(delta: str) -> None:
            nonlocal chars
            if not delta:
                return
            buf.append(delta)
            chars += len(delta)
            text = "".join(buf)
            # The debugger protocol is tiny.
            if re.search(r"^\s*CONFIDENCE\s*::\s*(?:high|medium|low)\s*$", text, re.I | re.M):
                raise _StopDebuggerStream()
            if re.search(r"^\s*TOOL\s*::\s*[A-Z_]+\s*::\s*.+$", text, re.I | re.M) and "\n" in text.strip():
                raise _StopDebuggerStream()
            if chars >= DEBUGGER_OUTPUT_CHARS:
                raise _StopDebuggerStream()

        try:
            self.arch._stream(
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": user}],
                collect, temperature=0.05, model=self.model, timeout=100)
        except _StopDebuggerStream:
            pass
        except Exception as e:
            log.debug("agentic E2E debugger model call: %s", e)
            if not buf:
                return ""
        return "".join(buf)[:DEBUGGER_OUTPUT_CHARS]

    @staticmethod
    def _parse_tool(raw: str):
        m = re.search(r"^\s*TOOL\s*::\s*([A-Z_]+)\s*::\s*(.*?)\s*$",
                      str(raw or ""), re.I | re.M)
        if not m:
            return None
        return m.group(1).upper(), m.group(2).strip()[:500]

    def _run_tool(self, name: str, arg: str, *, failures, journey, scenario) -> str:
        name = name.upper()
        ev = dict(getattr(self.agent, "_last_run_evidence", {}) or {})
        files = getattr(self.arch, "files", None) or {}

        if name == "DOM":
            return str(ev.get("dom") or "(no DOM snapshot)")
        if name == "NETWORK":
            rows = list(ev.get("network_errors") or []) + list(ev.get("api_network") or [])
            return "\n".join(str(x) for x in rows[-30:]) or "(no network observations)"
        if name == "API_BODIES":
            return "\n".join(str(x) for x in (ev.get("api_network") or [])[-30:]) or "(no API observations)"
        if name == "AUTH":
            return json.dumps({"session": ev.get("session"),
                               "cookie_names": ev.get("cookie_names")},
                              ensure_ascii=False, indent=2)[:TOOL_RESULT_CHARS]
        if name == "JOURNEY_STATE":
            state = getattr(self.agent, "_journey_outcomes", {}) or {}
            return json.dumps(state, ensure_ascii=False, indent=2)[:TOOL_RESULT_CHARS]
        if name == "STACK_SOURCE":
            return self._stack_source()
        if name == "READ_FILE":
            rel = self._clean_path(arg)
            if rel not in files:
                return f"not found: {rel}"
            return f"### {rel}\n{str(files.get(rel) or '')[:TOOL_RESULT_CHARS]}"
        if name == "SEARCH_CODE":
            return self._search_code(arg)
        if name == "ROUTE_SOURCE":
            rel = self._route_source(arg)
            if not rel:
                return f"no source mapped for {arg}"
            return f"### {arg} -> {rel}\n{str(files.get(rel) or '')[:TOOL_RESULT_CHARS]}"
        if name == "ANALYZER":
            return self._analyzer(arg)
        if name == "PLAN":
            plan = getattr(self.arch, "plan", None) or {}
            text = json.dumps({
                "capabilities": plan.get("capabilities") or [],
                "workflows": plan.get("workflows") or [],
                "contracts": plan.get("contracts") or [],
                "pages": plan.get("pages") or [],
            }, ensure_ascii=False, indent=2)
            needle = str(arg or "").strip().lower()
            if not needle or needle == "current":
                return text[:TOOL_RESULT_CHARS]
            rows = [ln for ln in text.splitlines() if needle in ln.lower()]
            return "\n".join(rows[:100]) or "no matching plan lines"
        return f"unknown tool: {name}"

    def _runtime_locations(self, text: str) -> list[dict]:
        """Map browser/Next stack frames back to exact generated source lines."""
        files = getattr(self.arch, "files", None) or {}
        blob = str(text or "").replace("\\", "/")
        out = []
        seen = set()
        for rel in files:
            rel = str(rel or "").strip().lstrip("./").replace("\\", "/")
            if not rel.startswith(("app/", "components/", "lib/")):
                continue
            if not rel.endswith((".js", ".jsx", ".ts", ".tsx")):
                continue
            rx = re.compile(re.escape(rel) + r"\s*[:#]\s*(\d+)(?:\s*:\s*(\d+))?", re.I)
            for m in rx.finditer(blob):
                line = int(m.group(1) or 0)
                col = int(m.group(2) or 0)
                key = (rel, line, col)
                if key in seen:
                    continue
                seen.add(key)
                around = blob[max(0, m.start()-500):min(len(blob), m.end()+500)]
                low = around.lower()
                if "pageerror" in low:
                    signal = "pageerror"
                elif re.search(r"referenceerror|typeerror|rangeerror|syntaxerror", around, re.I):
                    signal = "js-exception"
                elif "runtime_event :: console.error" in low or "consoleerror:" in low:
                    signal = "console.error"
                elif re.search(r"event handlers cannot be passed|unhandled runtime error|runtime error|failed to compile", around, re.I):
                    signal = "next-runtime"
                else:
                    signal = "stack-frame"
                msg = " ".join(around.split())[:500]
                out.append({"path": rel, "line": line, "column": col,
                            "signal": signal, "message": msg})
        rank = {"pageerror": 0, "js-exception": 1, "next-runtime": 2,
                "console.error": 3, "stack-frame": 4}
        out.sort(key=lambda x: (rank.get(x.get("signal"), 9), x["path"], x["line"]))
        return out[:12]

    def _stack_source(self) -> str:
        """Read ±20 lines around every exact runtime frame."""
        if not self._runtime_frame_cache:
            return "(no exact project runtime source location was captured)"
        chunks = []
        seen = set()
        for frame in self._runtime_frame_cache[:6]:
            rel = frame.get("path") or ""
            line = int(frame.get("line") or 0)
            key = (rel, line)
            if not rel or line <= 0 or key in seen:
                continue
            seen.add(key)
            body = ""
            try:
                body = self.agent.source_of(rel, 80_000)
            except Exception:
                body = str((getattr(self.arch, "files", None) or {}).get(rel, "") or "")
            rows = body.splitlines()
            if not rows:
                continue
            lo = max(1, line - 20)
            hi = min(len(rows), line + 20)
            rendered = []
            for n in range(lo, hi + 1):
                mark = ">" if n == line else " "
                rendered.append(f"{mark}{n:4d} | {rows[n-1]}")
            chunks.append(
                f"### {rel}:{line}:{int(frame.get('column') or 0)} [{frame.get('signal','runtime')}]\n"
                + "\n".join(rendered))
        return "\n\n".join(chunks)[:TOOL_RESULT_CHARS] or "(source location could not be read)"

    def _apply_runtime_location_priority(self, decision: dict, failures,
                                         frames: list[dict], packet: str) -> dict:
        """Prevent selector repair from hiding a captured production exception."""
        if not frames:
            return decision
        strong = [x for x in frames if x.get("signal") in
                  {"pageerror", "js-exception", "next-runtime", "console.error"}]
        if not strong:
            return decision

        verdict = str(decision.get("verdict") or "UNKNOWN").upper()
        evidence = str(packet or "")
        has_api_5xx = bool(re.search(r"HTTP\s+5\d\d\b", evidence, re.I))
        has_auth = bool(re.search(r"HTTP\s+(?:401|403)\b", evidence, re.I))
        if verdict == "AUTH_FIX" and has_auth:
            return decision
        if verdict == "APP_FIX" and has_api_5xx:
            existing = list(decision.get("files") or [])
            for frame in strong:
                if frame["path"] not in existing:
                    existing.append(frame["path"])
            decision["files"] = existing[:4]
            decision["locations"] = strong[:6]
            return decision

        if verdict in {"TEST_FIX", "UNKNOWN", "RETRY_TRANSIENT"}:
            first = strong[0]
            files = []
            for frame in strong:
                if frame["path"] not in files:
                    files.append(frame["path"])
            loc = f"{first['path']}:{first['line']}:{first.get('column', 0)}"
            return {
                "verdict": "APP_FIX",
                "root": f"a captured browser/Next runtime error points to production source {loc}",
                "files": files[:4],
                "test_step": "", "test_patch": "",
                "hypothesis": f"the production exception at {loc} causes the downstream E2E symptom",
                "next": "read the exact source lines around the runtime frame and make the smallest production repair",
                "confidence": "high", "privileged": [],
                "locations": strong[:6],
            }

        if verdict == "APP_FIX":
            exact = []
            for frame in strong:
                if frame["path"] not in exact:
                    exact.append(frame["path"])
            existing = [str(x or "").strip() for x in (decision.get("files") or [])
                        if str(x or "").strip()]
            merged = (existing + exact) if has_api_5xx else (exact + existing)
            decision["files"] = list(dict.fromkeys(merged))[:4]
            if strong:
                first = strong[0]
                loc = f"{first['path']}:{first['line']}:{first.get('column', 0)}"
                if decision.get("confidence") != "high":
                    decision["confidence"] = "high"
                decision["next"] = (decision.get("next") or "") + \
                    f" First inspect the exact runtime frame at {loc}."

        decision["locations"] = strong[:6]
        return decision
