"""Journey discovery and scenario authoring."""
from .e2e_common import *
from agents.core.workspace import STRUCTURE_CHARS, project_structure
from .e2e_contract import capability_contract, refresh_shipped_files


# What the scenario author may read while it writes. No writes, no commands:
# authoring is a read of the app, and one round of it keeps the stage fast.
E2E_AUTHOR_TOOLS = ("list_files", "read_file", "search_code", "route_source",
                    "dependency_closure", "route_map", "plan_query")

STRUCTURE_NOTE = (
    "Every path above is real and readable with the read_file / "
    "dependency_closure tools. Pick the ones this journey actually touches; "
    "never guess a filename that is not on this list.")
E2E_AUTHOR_TOOL_ROUNDS = 1


def _journey_values(value) -> list:
    """Normalize optional planner metadata without splitting strings to chars."""
    if isinstance(value, str):
        value = [value]
    return list(value) if isinstance(value, (list, tuple, set)) else []


class E2EJourneyAuthoringMixin:
    def _journeys_from_plan_md(self, roles: list) -> list:
        """The `### Journeys` chains out of the architect's plan.md."""
        md = getattr(self.arch, "plan_md", "") or ""
        i = md.find("## Page Flow")
        if i < 0:
            return []
        body = md[i:]
        end = body.find("\n## ", 4)
        body = body[:end] if end > 0 else body

        out = []
        for line in body.splitlines():
            if not self._BULLET.match(line):
                continue
            # Strip the markdown before parsing rather than trying to match.
            plain = self._BULLET.sub("", line).replace("`", "").replace("*", "")
            title, sep, rest = plain.partition(":")
            if not sep:
                continue
            title = " ".join(title.split())[:60]
            hops = [h.strip() for h in self._ARROW.split(rest)]
            hops = [h for h in hops if h.startswith("/")]
            if not title or len(hops) < 2:
                continue
            steps = [f"go to {h}" if n == 0 else f"then reach {h}"
                     for n, h in enumerate(hops)]
            # Infer the role from the route chain.
            role = ""
            for r in roles:
                if r and any(h.lstrip("/").split("/")[0] == r for h in hops):
                    role = r
                    break
            out.append({"title": title, "steps": steps, "role": role})
            if len(out) >= MAX_FLOWS:
                break
        if out:
            self._log("INFO", f"   🧭 {len(out)} journey(s) read from the "
                              f"plan's Page Flow — the workflows were empty")
        return out

    def testids_in_app(self, limit: int = 40) -> list:
        """Every `data-testid` the app really sets, read from its own markup."""
        files = getattr(self.arch, "files", None) or {}
        out = []
        for path in sorted(files):
            if not path.startswith(("app/", "components/")):
                continue
            for name in self._TESTID_RE.findall(files[path] or ""):
                if name not in out:
                    out.append(name)
                    if len(out) >= limit:
                        return out
        return out

    def _auto_journeys(self, roles: list, covered_ids=None) -> list:
        """Create E2E journeys when the project declares none."""
        covered_ids = {str(x or "").upper() for x in (covered_ids or []) if str(x or "").strip()}
        plan = getattr(self.arch, "plan", None) or {}
        caps = [c for c in (plan.get("capabilities") or [])
                if isinstance(c, dict) and bool(c.get("e2e", True))]
        contracts = [c for c in (plan.get("contracts") or []) if isinstance(c, dict)]
        out = []

        # Uncovered capabilities are grouped by role.
        pending = [c for c in caps
                   if str(c.get("id") or "").upper() not in covered_ids]
        groups = {}
        for c in pending:
            role = str(c.get("who") or "").strip().lower()
            groups.setdefault(role, []).append(c)

        for role, group in groups.items():
            for off in range(0, len(group), 3):
                chunk = group[off:off + 3]
                routes = []
                def add_route(url):
                    url = str(url or "").strip()
                    if url and url.startswith("/") and url not in routes:
                        routes.append(url)

                files = set()
                for c in chunk:
                    for rel in (c.get("files") or []):
                        rel = str(rel or "").strip()
                        if not rel:
                            continue
                        files.add(rel)
                        add_route(self._url_for_file(rel))

                contract_steps = []
                for c in contracts:
                    frm = str(c.get("from") or "").strip()
                    target = str(c.get("target") or "").strip()
                    if frm not in files and not any(f and f in target for f in files):
                        continue
                    add_route(self._url_for_file(frm))
                    if target.startswith("/") and "[" not in target:
                        add_route(target)
                    trigger = str(c.get("trigger") or "").strip()
                    effect = str(c.get("effect") or "").strip()
                    if trigger or effect:
                        contract_steps.append(
                            "perform " + (trigger or "the planned action")
                            + (" — prove " + effect if effect else ""))

                steps = []
                for url in routes[:4]:
                    if "[" not in url:
                        steps.append(f"go to {url}")
                steps.extend(contract_steps[:3])
                for c in chunk:
                    req = str(c.get("requirement") or "").strip()
                    proof = str(c.get("proof") or "").strip()
                    steps.append("prove " + req + (f" — expected: {proof}" if proof else ""))

                reqs = [str(c.get("requirement") or "").strip() for c in chunk]
                label = "; ".join(r for r in reqs if r)[:110] or "app capability"
                out.append({
                    "title": f"Auto E2E — {label}",
                    "steps": steps[:10],
                    "role": role,
                    "covers": [str(c.get("id") or "").upper() for c in chunk
                               if str(c.get("id") or "").strip()],
                    "generated": True,
                })
                if len(out) >= MAX_FLOWS:
                    return out

        # If a machine capability map exists.
        if caps:
            return out

        # No machine capability map either: existing/legacy app.
        try:
            route_map = self.az.enumerate_routes() or {} if self.az else {}
        except Exception:
            route_map = {}
        static = [u for u, m in sorted(route_map.items())
                  if m.get("kind") == "page" and not m.get("dynamic")
                  and "[" not in u and u not in ("/login", "/signup")]
        if not static:
            return []

        roles_to_make = list(roles) or [""]
        for role in roles_to_make:
            scored = []
            for u in static:
                low = u.lower()
                hit = 0
                if role and (low == f"/{role}" or low.startswith(f"/{role}/")
                             or role in low.replace("-", " ").replace("_", " ")):
                    hit = 2
                elif u == "/":
                    hit = 1
                scored.append((-hit, u.count("/"), u))
            chosen = [u for _, _, u in sorted(scored)[:4]]
            steps = [f"go to {u}" for u in chosen]
            steps.append("exercise one enabled navigation/form/action visible on these pages "
                         "and assert its URL or visible state changes")
            out.append({
                "title": f"Auto E2E — {role or 'visitor'} route and interaction coverage",
                "steps": steps,
                "role": role,
                "covers": [],
                "generated": True,
            })
            if len(out) >= MAX_FLOWS:
                break
        return out

    @staticmethod
    def _route_is_served(url: str, served) -> bool:
        """Whether one literal URL is answered by a real route, dynamic included."""
        target = [seg for seg in str(url or "").strip("/").split("/") if seg]
        for candidate in served:
            parts = [seg for seg in str(candidate or "").strip("/").split("/") if seg]
            if len(parts) != len(target):
                continue
            if all(b.startswith("[") or a == b for a, b in zip(target, parts)):
                return True
        return False

    def ground_journey_routes(self, journey: dict) -> dict:
        """Label every route the journey names as served or not served."""
        journey = journey or {}
        try:
            route_map = self.az.enumerate_routes() or {} if self.az else {}
        except Exception as e:
            log.debug(f"journey route grounding: {e}")
            route_map = {}
        if not route_map:
            journey.setdefault("served_routes", [])
            journey.setdefault("unserved_routes", [])
            return journey

        text = " ".join([str(journey.get("title") or "")]
                        + [str(x) for x in (journey.get("steps") or [])])
        served, unserved = [], []
        # `(?<![\w:/])` and not `(?<![\w:])`: the second `/`
        for raw in re.findall(r"(?<![\w:/])(/[A-Za-z0-9_./\[\]-]*)", text):
            url = raw.split("?", 1)[0].rstrip("/") or "/"
            if url in served or url in unserved:
                continue
            (served if self._route_is_served(url, route_map) else unserved).append(url)

        journey["served_routes"] = served[:12]
        journey["unserved_routes"] = unserved[:8]
        if unserved:
            self._log("INFO", f"   🧭 '{journey.get('title', '')}' names "
                              f"{len(unserved)} route(s) this build does not "
                              f"serve: " + " · ".join(unserved[:4]))
        return journey

    def planner_code_preflight(self, journey: dict) -> dict:
        """Compare the planned workflow with the code before authoring E2E."""
        self.ground_journey_routes(journey)
        contract = journey.get("contract") or capability_contract(self.arch, journey)
        refresh_shipped_files(self.arch, contract)
        missing = list(contract.get("missing_source_files") or [])
        unserved = list(journey.get("unserved_routes") or [])
        fields = self.fields_in_journey(journey)
        preflight = {
            "planner_checked": bool(journey.get("steps") or journey.get("covers")),
            "code_checked": True,
            "status": "gap" if missing or unserved else "ready",
            "source_files": list(contract.get("source_files") or []),
            "missing_source_files": missing,
            "served_routes": list(journey.get("served_routes") or []),
            "unserved_routes": unserved,
            "form_fields": fields,
        }
        journey["contract"] = contract
        journey["preflight"] = preflight
        state = "implementation gaps recorded" if preflight["status"] == "gap" \
            else "planner and generated code agree"
        self._log("INFO", f"   🔎 E2E preflight — {journey.get('title', 'journey')}: {state}")
        return preflight

    def journeys(self) -> list:
        """Every journey the app should be able to walk, from the plan."""
        out = []
        accs = self.accounts()
        roles = sorted({(a.get("role") or "").lower() for a in accs
                        if a.get("role")})

        sources = []
        plan = getattr(self.arch, "plan", None) or {}
        # Prefer the SRS testing handoff because it carries the deterministic
        # seed/account/record prerequisites that plain workflows omit.
        try:
            handoff_path = self.project_dir / ".agentforge" / "srs" / "handoff.json"
            if handoff_path.is_file():
                handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                testing = (handoff.get("testing_contract") or
                           (handoff.get("builder_handoff") or {}).get("testing_contract") or {})
                handoff_flows = []
                for case in testing.get("e2e") or []:
                    if not isinstance(case, dict):
                        continue
                    handoff_flows.append({
                        "name": case.get("name"),
                        "actor": case.get("actor"),
                        "covers": case.get("covers") or [],
                        "steps": case.get("steps") or [],
                        "routes": case.get("routes") or [],
                        "pre_journey": case.get("pre_journey") or {},
                        "preconditions": ((case.get("pre_journey") or {})
                                          .get("explicit_preconditions") or []),
                        "expected_results": case.get("proofs") or [],
                        "required_actions": case.get("required_actions") or [],
                        "testing_contract_id": case.get("id"),
                    })
                if handoff_flows:
                    sources.append(handoff_flows)
                    self._log("INFO", f"   🌱 loaded {len(handoff_flows)} E2E "
                                      "pre-journey contract(s) from the SRS handoff")
        except Exception as e:
            log.debug(f"SRS E2E testing handoff: {e}")
        if plan.get("workflows"):
            sources.append(plan["workflows"])
        try:
            srs_plan = self.project_dir / ".agentforge" / "srs" / "plan.json.srs"
            if srs_plan.is_file():
                d = json.loads(srs_plan.read_text(encoding="utf-8"))
                d = d.get("plan") or d
                if d.get("workflows"):
                    sources.append(d["workflows"])
        except Exception as e:
            log.debug(f"srs plan: {e}")

        if not sources:
            plan_flows = self._journeys_from_plan_md(roles)
            if plan_flows:
                sources.append(plan_flows)

        seen = set()
        for wfs in sources:
            for wf in wfs or []:
                if not isinstance(wf, dict):
                    continue
                title = str(wf.get("name") or wf.get("title") or "").strip()
                steps = [str(s).strip() for s in (wf.get("steps") or []) if str(s).strip()]
                if not title or not steps or title.lower() in seen:
                    continue
                seen.add(title.lower())
                # The role, if the workflow names one.
                role = str(wf.get("who") or wf.get("role") or wf.get("actor")
                           or "").strip().lower()
                if not role:
                    text = " ".join(steps).lower()
                    role = next((r for r in roles if r and r in text), "")
                covers = [str(x).strip().upper() for x in (wf.get("covers") or [])
                          if str(x).strip()] if isinstance(wf.get("covers"), list) else []
                out.append({"title": title, "steps": steps, "role": role,
                            "covers": covers,
                            "routes": _journey_values(wf.get("routes")),
                            "preconditions": _journey_values(wf.get("preconditions")),
                            "pre_journey": (wf.get("pre_journey") or {}),
                            "expected_results": _journey_values(wf.get("expected_results")),
                            "required_actions": _journey_values(wf.get("required_actions")),
                            "testing_contract_id": wf.get("testing_contract_id")})

        # If workflows are absent (common on imported/legacy apps).
        covered_ids = {str(x or "").upper()
                       for j in out for x in (j.get("covers") or [])}
        # Declared workflows are already meaningful E2E blueprints.
        has_capability_map = bool((plan.get("capabilities") or []))
        auto = ([] if (sources and not has_capability_map)
                else self._auto_journeys(roles, covered_ids))
        if auto:
            existing_specs = list((self.project_dir / "tests" / "e2e").glob("*.spec.js"))                 if (self.project_dir / "tests" / "e2e").is_dir() else []
            if not sources:
                self._log("INFO", f"   🧪 no declared E2E workflows — generated "
                                  f"{len(auto)} journey blueprint(s) from "
                                  f"capabilities/routes")
            elif any(j.get("covers") for j in auto):
                self._log("INFO", f"   🧪 generated {len(auto)} extra E2E "
                                  f"journey blueprint(s) for uncovered capabilities")
            if not existing_specs:
                self._log("INFO", "   🧪 project had no E2E specs — AgentForge "
                                  "will write tests/e2e/*.spec.js from these journeys")
            for j in auto:
                if len(out) >= MAX_FLOWS:
                    break
                key = (j.get("title", "").lower(), j.get("role", ""))
                if not any((x.get("title", "").lower(), x.get("role", "")) == key
                           for x in out):
                    out.append(j)

        covered_roles = {j["role"] for j in out if j.get("role")}
        for r in roles:
            if r not in covered_roles and len(out) < MAX_FLOWS:
                out.append({"title": f"Auto E2E — what the {r} does here",
                            "steps": [f"exercise the primary {r} workflow end to end "
                                      "using only controls observed in the real DOM"],
                            "role": r, "covers": [], "generated": True})
        if not out:
            out.append({"title": "Auto E2E — visitor smoke and interaction",
                        "steps": ["open the public entry page",
                                  "exercise one enabled user action and assert its result"],
                        "role": "", "covers": [], "generated": True})
        final = out[:MAX_FLOWS]
        for item in final:
            self.planner_code_preflight(item)
        return final

    def _author_stream(self, convo, feed, workspace):
        """One authoring turn, offering workspace reads when the host allows."""
        kwargs = dict(temperature=TEMPERATURE,
                      model=QASession.model_for(self.qa, self.arch),
                      timeout=CALL_BUDGET,
                      reasoning=QASession.reasoning_for(self.qa),
                      max_output_tokens=AUTHOR_OUTPUT_TOKENS)
        if workspace is not None:
            try:
                return self.arch._stream(
                    convo, feed,
                    tools=workspace.schemas(E2E_AUTHOR_TOOLS), **kwargs)
            except TypeError as exc:
                if "tools" not in str(exc):
                    raise
        return self.arch._stream(convo, feed, **kwargs)

    def author(self, previous: Scenario = None, why: str = "",
               page: str = "", journey: dict = None) -> Scenario:
        """One model call. Returns a parsed scenario — possibly an empty one."""
        # Re-read the app before describing it. `journeys()` grounds
        if journey:
            self.ground_journey_routes(journey)
            refresh_shipped_files(self.arch, journey.get("contract") or {})

        accs = self.accounts()
        roles = ", ".join(sorted({(a.get("role") or "user") for a in accs})) \
            or "there are no demo accounts — write a signed-out journey"
        idea = self._idea()

        budget = self.prompt_budget_chars()
        # The grammar and the verb list are not optional
        tids = self.testids_in_app()
        testid_room = min(2_000, len(", ".join(tids)) + 500) if tids else 0
        fixed = len(SYSTEM)
        tail = 1_200 + testid_room + (3_600 if (previous is not None and why) else 0)

        def room() -> int:
            return max(0, budget - fixed - tail - len(ask))

        structure = project_structure(
            getattr(self.arch, "files", None) or {},
            max_chars=max(1_200, min(STRUCTURE_CHARS, budget // 12)))
        ask = (f"## The app\n{self._fit(idea, 3_000)}\n\n"
               f"## Pages and endpoints it serves\n"
               f"{self._routes(cap=max(40, budget // 400)) or '  (unknown)'}\n\n"
               + (f"## The files this app is made of\n{structure}\n"
                  f"{STRUCTURE_NOTE}\n\n" if structure else "")
               + f"## Demo accounts available\nRoles: {roles}\n"
               f"Use {{{{email}}}} and {{{{password}}}} — AgentForge fills in the "
               f"real values for the role you name with AS.\n")

        if journey:
            steps = "\n".join(f"  {i}. {s}" for i, s in
                               enumerate(journey.get("steps") or [], start=1))
            ask += (f"\n## THE JOURNEY THIS SCENARIO WALKS\n"
                    f"{journey['title']}"
                    + (f" — as the {journey['role']}" if journey.get("role") else "")
                    + "\n" + (steps or "  (walk it end to end)") + "\n"
                    f"Walk EVERY step above, in order, and assert on what each "
                    f"one leaves behind — the row that appeared, the total that "
                    f"changed, the status that moved. A scenario that signs in "
                     f"and looks at one page has not walked this journey.\n")
            setup = journey.get("pre_journey") or {}
            preconditions = _journey_values(journey.get("preconditions"))
            expected = _journey_values(journey.get("expected_results"))
            required_actions = _journey_values(journey.get("required_actions"))
            if setup or preconditions or expected or required_actions:
                handoff = {
                    "pre_journey": setup,
                    "preconditions": preconditions,
                    "required_actions": required_actions,
                    "expected_results": expected,
                }
                ask += ("\n## PRE-JOURNEY TEST HANDOFF\n"
                        + self._fit(json.dumps(handoff, ensure_ascii=False, indent=2), 6_000)
                        + "\nUse the real deterministic seed, exact-role account, required "
                          "records/routes/APIs and real dynamic IDs described here. Never "
                          "borrow another role's account, invent fixture data, or depend on "
                          "a previous E2E journey. Prove every required action and expected "
                          "result.\n")
            contract = journey.get("contract") or capability_contract(self.arch, journey)
            preflight = journey.get("preflight") or self.planner_code_preflight(journey)
            ask += ("\n## PLANNER AND GENERATED-CODE PREFLIGHT\n"
                    + self._fit(json.dumps(preflight, ensure_ascii=False, indent=2), 4_000)
                    + "\nThe planner defines WHAT must work. The generated source, routes, "
                      "API calls and form fields define HOW this build implements it. "
                      "Do not hide a recorded gap by weakening or skipping the planned step.\n")
            ask += ("\n## EXECUTABLE PROOF CONTRACT\n"
                    + self._fit(json.dumps(contract, ensure_ascii=False, indent=2), 7_000)
                    + "\nThis contract is authoritative. Do not invent a field, control, route, "
                      "success sentence, or role outside it. Assertions must prove the listed "
                      "effects/proofs, not merely that a page rendered.\n")

            absent = [str(x) for x in (contract.get("missing_source_files") or [])]
            if absent:
                ask += ("\n## PLANNED FILES THIS BUILD DID NOT PRODUCE\n  "
                        + " · ".join(absent[:8])
                        + "\nThe contract names these, and they are not in the app. "
                          "Do not write selectors for them and do not assume the "
                          "screens they were meant to render exist. Keep the "
                          "business step that needs them so the run reports the "
                          "gap as an APP defect.\n")

            covers = {str(x).upper() for x in (journey.get("covers") or [])}
            if covers:
                caps = []
                for c in (getattr(self.arch, "plan", None) or {}).get("capabilities") or []:
                    if str(c.get("id") or "").upper() in covers:
                        caps.append(f"  {c.get('id')}: {c.get('requirement')} — PROVE: {c.get('proof')}")
                if caps:
                    ask += ("\n## CAPABILITIES THIS JOURNEY MUST PROVE\n" + "\n".join(caps) +
                            "\nDo not finish DONE until every proof above has an assertion after the action. "
                            "For filters/search/availability, enter values, trigger the filter, and assert "
                            "the result changed or an occupied/unavailable record is absent.\n")

        if journey:
            ids = self.action_id_block(journey)
            if ids:
                ask += "\n## " + ids + "\n"
            jroutes = self._journey_routes(journey)
            if jroutes:
                ask += ("\n## ROUTES RELEVANT TO THIS JOURNEY\n  "
                        + " · ".join(jroutes)
                        + "\nUse these exact routes. After login, explicitly GOTO the "
                          "route where the next job happens instead of asserting "
                          "that the login landing page has guessed copy.\n")

            unserved = [str(u) for u in (journey.get("unserved_routes") or [])]
            if unserved:
                ask += ("\n## ROUTES THIS JOURNEY NAMES THAT THE APP DOES NOT SERVE\n  "
                        + " · ".join(unserved[:8])
                        + "\nThese came out of the plan, not out of the app. GOTO on "
                          "any of them is a 404 and everything after it fails for the "
                          "wrong reason. Use a route from the table above instead. If "
                          "the journey genuinely cannot be walked without one of them, "
                          "keep the step and let the run report it as an APP defect.\n")

            # The generated code, first claim on what is left.
            code_room = int(room() * 0.55)
            source_bundle = self._fit(
                self.journey_source_bundle(journey, page=page, budget=code_room),
                code_room)
            if source_bundle:
                ask += ("\n## JOURNEY SOURCE CONTRACT — ACTUAL CODE ON DISK\n"
                        "```jsx\n" + source_bundle + "\n```\n"
                        "This code is authoritative for implementation details. "
                        "Derive field names, control types, API URLs/methods, "
                        "request shapes, auth helpers, navigation and post-save "
                        "effects from it. The plan tells you WHAT must be proved; "
                        "the code tells you HOW this build really does it. Never "
                        "invent a selector, request, route or success state that "
                        "contradicts these files. If the required workflow action "
                        "is absent from this real code/DOM, preserve that business "
                        "step so the run reports an APP defect rather than making "
                        "the test easier.\n")

            fields = self.fields_in_journey(journey, page=page)
            if fields:
                ask += ("\n## FORM FIELDS THIS APP REALLY HAS\n  "
                        + ", ".join(fields)
                        + "\nThese are the only values `field=` may be given for "
                          "this journey. If the value you want to type has no "
                          "field on this list, the app has no such control: keep "
                          "the business step so the run reports an APP defect, "
                          "and do not invent a field name for it.\n")

            live = self._fit(self.runtime_evidence(journey), int(room() * 0.45))
            if live:
                ask += ("\n## RUNTIME DOM EVIDENCE — OBSERVED IN A REAL BROWSER\n"
                        + live
                        + "\nThese names/fields/routes are facts. For page identity use "
                          "EXPECT_URL. For controls/fields choose only things "
                          "listed here or in the source markup below. Never "
                          "invent a heading the page does not have, or marketing "
                          "copy merely because the journey sounds like it.\n"
                          "If this journey TYPES a new value (for example a review "
                          "body, title, status or price), it is valid to assert that "
                          "exact value after the save even though it was not present "
                          "in source before the run. For any other success copy, use "
                          "only wording observed here/source — never invent a nice-"
                          "sounding confirmation sentence.\n")

        # Whatever the code and the DOM did not use goes to the raw
        markup_room = room()
        shown = ([page] if page else []) + self._journey_pages(journey)
        blocks, used = [], 0
        for rel in dict.fromkeys(s for s in shown if s):
            block = self.markup_for(rel)
            if not block:
                continue
            if used + len(block) > markup_room and blocks:
                break
            blocks.append(block)
            used += len(block)
        body = self._fit("\n\n".join(blocks), markup_room)
        if body:
            ask += (f"\n## The real markup\n```jsx\n{body}\n```\n"
                    f"Copy the placeholders, button labels and visible text "
                    f"above into your selectors. Do not guess at wording that "
                    f"is written down right here — and do not assert text that "
                    f"is not in it.\n")

        if tids:
            ask += (f"\n## Controls this app labels for you\n  "
                    + self._fit(", ".join(tids), testid_room)
                    + "\nThese are `data-testid` values that really are in the "
                      "markup above. When the control you need is one of them, "
                      "write `testid=<name>` — it is the one selector the app "
                      "promises not to reword. Never invent one that is not on "
                      "this list.\n")

        if journey:
            ask += (f"\nWrite ONE scenario that walks the journey above — "
                    f"'{journey['title']}' — end to end.")
        else:
            ask += ("\nWrite ONE scenario for the most important journey this "
                    "app exists to support.")
        if previous is not None and why:
            ask += (f"\n\n## Your previous scenario did not work\n{self._fit(why, 1_200)}\n\n"
                    f"It was:\n{self._fit(self._render(previous), 2_000)}\n\n"
                    f"Write it again. Keep every valid step. Replace only the "
                    f"ungrounded assertion/locator identified above. Match observed "
                    f"markup/DOM exactly, and for post-save state prefer the exact "
                    f"value the scenario entered over invented success copy.")

        over = len(ask) + fixed - budget
        if over > 0:
            self._log("WARN", f"   ⚠ the authoring prompt is {over:,} characters "
                              f"over this model's context window")

        convo = [{"role": "system", "content": system_prompt(self.accounts())},
                 {"role": "user", "content": ask}]
        from agents.core.ollama_client import is_transient, with_retry
        from agents.core.workspace import WorkspaceTools

        # The scenario is written against real markup, so the author may read
        # the app before it commits to a locator or an assertion.
        workspace = WorkspaceTools(self.arch)
        # Its own dedupe ledger: journey two may legitimately read the file
        # journey one read, and it must not be told the answer is already in
        # a context it never saw.
        workspace.cache = {}
        text = ""
        for round_no in range(E2E_AUTHOR_TOOL_ROUNDS + 1):
            raw = []
            parser = FileStreamParser(on_text=lambda t: raw.append(t),
                                      on_file_start=lambda p: None,
                                      on_file_token=lambda t: None,
                                      on_file_end=lambda p, c: None)
            offering = round_no < E2E_AUTHOR_TOOL_ROUNDS
            try:
                calls = with_retry(
                    lambda: self._author_stream(
                        convo, parser.feed,
                        workspace if offering else None),
                    what="the QA model")
            except Exception as e:                              # noqa: BLE001
                blocked = is_transient(e)
                self._log("WARN", f"   ⚠ could not write an end-to-end flow: {e}"
                                  + (" — the daemon stayed busy, so this is not a "
                                     "verdict on the app" if blocked else ""))
                empty = Scenario()
                # The caller must not read a busy daemon as a bad scenario.
                setattr(empty, "blocked", blocked)
                setattr(empty, "blocked_why", str(e)[:300] if blocked else "")
                return empty
            parser.close()

            text = "".join(raw)
            calls = list(calls or [])
            if not calls:
                break
            convo.append(workspace.assistant_message(text, calls))
            messages, used = workspace.serve_calls(calls, names=E2E_AUTHOR_TOOLS)
            convo.extend(messages)
            if not used:
                break
            self._log("INFO", f"   🧰 the scenario author read {used} thing(s) "
                              f"out of the app before writing a step")
            convo.append({"role": "user", "content":
                          "That is what the app really contains. Write the "
                          "scenario now, and ground every locator and every "
                          "assertion in what you just read — an exact "
                          "data-testid, role/name or visible string from those "
                          "files. Do not repeat a tool call."})
            text = ""
        sc = parse_scenario(text, account=self.account_for(
            self._role_in(text)))
        for _, line, reason in sc.dropped[:4]:
            self._log("WARN", f"   ⚠ dropped `{line}` — {reason}")
        return sc
