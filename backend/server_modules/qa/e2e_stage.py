# Evidence-first end-to-end checks and repairs.
from qa_agent.e2e_normalize import normalize_scenario_selectors
from qa_agent.e2e_contract import scenario_contract_issue, runtime_contract_issue
from qa_agent.e2e_invariants import validate_repair_invariants, repair_guard_feedback

def _repair_after_stages(proj_dir: Path, qa, arch, runner, failures):
    """Bring the tests back in line with the code that shipped."""
    for f in failures:
        try:
            f.stale = True
        except Exception:
            pass
    fixer = BugFixerAgent(arch, proj_dir, callbacks=_qa_callbacks(), session=qa,
                          model=QASession.model_for(qa, arch))
    for rnd in range(1, MAX_AFTER_FIX + 1):
        groups = _by_test_file(failures)
        ephase({"phase": -16, "title": "Re-aligning tests with the shipped code",
                "status": "active"})
        elog("INFO", f"   🔧 re-aligning {len(groups)} test file(s) with the "
                     f"code the later stages left behind (round {rnd}/"
                     f"{MAX_AFTER_FIX})")

        def repair(group):
            try:
                fixer.fix(group, build_ok=True, round_no=rnd, tier=0)
            except Exception as e:
                elog("WARN", f"   ⚠ re-alignment failed on "
                             f"{group[0].test_file}: {e}")
                log.exception("post-stage fix")

        with ThreadPoolExecutor(max_workers=QA_FIX_WORKERS) as pool:
            list(pool.map(repair, groups))
        ephase({"phase": -16, "title": "Re-aligning tests with the shipped code",
                "status": "done"})

        passed, failures, ok = runner.run()
        if not ok:
            elog("WARN", "   ⚠ the re-run produced no report — keeping the "
                         "last numbers that were measured")
            return passed, failures
        if not failures:
            elog("INFO", f"   ✅ {passed}/{passed} — the suite is green on the "
                         f"code that is shipping")
            return passed, failures
        elog("WARN", f"   ❌ {len(failures)} still failing after round {rnd}")
    _leave_unresolved(runner, failures,
                      "the code changed after these tests were written and "
                      "they could not be brought back into line")
    return passed, failures

def _e2e_detail(failures) -> list:
    """What actually failed, in the shape the report already uses for unit cases."""
    out = []
    for f in failures or ():
        out.append({
            "case": getattr(f, "name", ""),
            "target": getattr(f, "target", ""),
            "kind": getattr(f, "kind", ""),
            "message": getattr(f, "message", ""),
            "stack": (getattr(f, "stack", "") or "")[:2000],
        })
    return out

def run_qa_e2e_stage(arch, proj_dir: Path, qa, analyzer, *, build_ok: bool,
                     db_ok: bool) -> dict:
    """Sign in as a real seeded account and use the app."""
    out = {"ran": False, "flow": "", "passed": 0, "failed": 0, "fixed": 0,
           "unwritable": 0, "flows": [], "failures": [],
           "stale_after_late_repair": False}
    if not qa or not qa.enabled:
        return out
    if not build_ok:
        elog("WARN", "   ⚠ End-to-end flow skipped — the build is not green")
        return out
    if not db_ok:

        elog("WARN", "   ⚠ End-to-end flow skipped — no database")
        return out

    ephase({"phase": -17, "title": "End-to-end flow", "status": "active"})
    agent = E2EAgent(arch, proj_dir, callbacks=_qa_callbacks(), session=qa,
                     analyzer=analyzer, base_url=f"http://localhost:{DEV_PORT}")
    try:
        out.update(_e2e_rounds(agent, arch, proj_dir, qa, analyzer, out))
    except Exception as e:
        elog("WARN", f"   ⚠ End-to-end flow failed: {e}")
        log.exception("qa e2e")
    ephase({"phase": -17, "title": "End-to-end flow", "status": "done"})
    return out

def _e2e_rounds(agent, arch, proj_dir, qa, analyzer, out):
    """Every journey in the plan, walked one after another."""
    # The app that shipped is read and repaired before any journey
    stage_source_paths, stage_baseline = _prepare_app_before_journeys(
        agent, arch, proj_dir, analyzer)
    stage_source_set = set(stage_source_paths)
    # The journeys are about to open these pages one at a time
    _warm_routes_async(agent)

    journeys = agent.journeys()
    total = len(journeys)
    if total > 1:
        elog("INFO", f"   🎭 {total} journeys to walk: "
                     + " · ".join(j["title"] for j in journeys))

    flows, all_fail = [], []
    blocked_retry = []
    agent._journey_outcomes = {}
    for n, journey in enumerate(journeys, start=1):
        if total > 1:
            elog("INFO", f"   🎭 journey {n}/{total} — {journey['title']}"
                         + (f" (as {journey['role']})" if journey.get("role") else ""))

        if not _dev_alive(2.0):
            elog("WARN", "   ↻ the dev server is not answering — starting it "
                         "again before walking this journey")
            start_next(proj_dir)
            # A new dev process has compiled nothing; every route is cold.
            _forget_warm(agent)
            if not wait_for_next():
                elog("WARN", "   ⚠ it did not come back; this journey will "
                             "report what it finds")
        one = {"ran": False, "flow": "", "passed": 0, "failed": 0, "fixed": 0,
               "unwritable": 0, "failures": []}
        try:
            _reseed_for_journey(agent, proj_dir, n)
            one = _e2e_one_flow(agent, arch, proj_dir, qa, analyzer, one, journey)
        except Exception as e:
            elog("WARN", f"   ⚠ journey '{journey['title']}' could not run: {e}")
            log.exception("qa e2e flow")
        flows.append({"title": journey["title"], "role": journey.get("role", ""),
                      **{k: one.get(k) for k in ("ran", "flow", "passed", "failed",
                                                 "fixed", "unwritable")},
                      "blocked_upstream": bool(one.get("blocked_upstream")),
                      "blocked": int(one.get("blocked") or 0)})
        agent._journey_outcomes[journey["title"]] = {
            "role": journey.get("role", ""),
            "status": ("blocked_model" if one.get("blocked") else
                       "blocked_upstream" if one.get("blocked_upstream") else
                       "pass" if one.get("ran") and not one.get("failed") else "fail"),
            "reason": (one.get("blocked_why") or one.get("blocked_reason", "")),
        }
        if one.get("blocked_upstream"):
            blocked_retry.append((len(flows) - 1, journey))
        else:
            all_fail.extend(one.get("failures") or [])
        out["ran"] = out["ran"] or bool(one.get("ran"))
        out["fixed"] += int(one.get("fixed") or 0)
        out["failed"] += int(one.get("failed") or 0)
        out["passed"] += 1 if (one.get("ran") and not one.get("failed")) else 0
        out["unwritable"] = out.get("unwritable", 0) + int(one.get("unwritable") or 0)
        out["blocked"] = out.get("blocked", 0) + int(one.get("blocked") or 0)

    if out.get("blocked"):
        elog("WARN", f"   ⏸ {out['blocked']} journey(s) were never walked "
                     f"because the QA model stayed unavailable. The gate is "
                     f"NOT green, but nothing here says the app is broken — "
                     f"re-run the E2E stage once the model is free.")

    if blocked_retry and not E2E_RETRY_BLOCKED:
        elog("INFO", f"   ⏭ {len(blocked_retry)} dependency-blocked journey(s) "
                     "will not be replayed; fast E2E mode records them and moves on")

    for flow_index, journey in (blocked_retry if E2E_RETRY_BLOCKED else []):
        elog("INFO", f"   🔁 retrying dependency-blocked journey — {journey['title']}")
        one = {"ran": False, "flow": "", "passed": 0, "failed": 0, "fixed": 0,
               "unwritable": 0, "failures": []}
        try:
            _reseed_for_journey(agent, proj_dir, 2)
            one = _e2e_one_flow(agent, arch, proj_dir, qa, analyzer, one, journey)
        except Exception as e:
            elog("WARN", f"   ⚠ dependency retry '{journey['title']}' could not run: {e}")
            log.exception("qa e2e dependency retry")
        old = flows[flow_index]
        # Remove the first-pass counters before replacing its result.
        out["fixed"] -= int(old.get("fixed") or 0)
        out["failed"] -= int(old.get("failed") or 0)
        out["passed"] -= 1 if (old.get("ran") and not old.get("failed")) else 0
        out["unwritable"] -= int(old.get("unwritable") or 0)
        flows[flow_index] = {"title": journey["title"], "role": journey.get("role", ""),
                             **{k: one.get(k) for k in ("ran", "flow", "passed", "failed",
                                                        "fixed", "unwritable")},
                             "blocked_upstream": bool(one.get("blocked_upstream"))}
        out["fixed"] += int(one.get("fixed") or 0)
        out["failed"] += int(one.get("failed") or 0)
        out["passed"] += 1 if (one.get("ran") and not one.get("failed")) else 0
        out["unwritable"] += int(one.get("unwritable") or 0)
        all_fail.extend(one.get("failures") or [])
        agent._journey_outcomes[journey["title"]] = {
            "role": journey.get("role", ""),
            "status": ("blocked_upstream" if one.get("blocked_upstream") else
                       "pass" if one.get("ran") and not one.get("failed") else "fail"),
            "reason": one.get("blocked_reason", ""),
        }

    global_failures = []
    global_mark = dev_log_mark()
    try:
        roles = {str(a.get("role") or "").strip()
                 for a in agent.accounts() if a.get("role")}
        if len(roles) < 2:
            elog("INFO", "   🔓 no role separation to check — this app serves "
                         + (f"one role ({', '.join(roles)})" if roles
                            else "no sign-in at all"))
        else:
            elog("INFO", "   🔐 one role-separation integrity sweep")
        global_failures = agent.global_integrity()
        for fault in terminal_faults(dev_log_since(global_mark)):
            rel_m = re.search(r"((?:app|components|lib)[\\/][A-Za-z0-9_./\\\[\]-]+\.(?:jsx?|mjs))", fault)
            rel = rel_m.group(1).replace("\\", "/") if rel_m else ""
            if not any(str(getattr(f, "message", ""))[:100] in fault for f in global_failures):
                global_failures.append(TestFailure(
                    test_file="tests/e2e/__global-integrity__.spec.js",
                    target=rel, name="Next runtime error during global integrity",
                    message=fault[:700], stack=fault[:3000], kind="CRASH"))
    except Exception as e:
        elog("WARN", f"   ⚠ global E2E integrity could not run: {e}")
        log.exception("qa e2e global integrity")

    for grnd in range(1, E2E_GLOBAL_REPAIR_ATTEMPTS + 1):
        if not global_failures:
            break
        targets = []
        for f in global_failures[:6]:
            rel = str(getattr(f, "target", "") or "").strip()
            if rel and rel not in targets:
                targets.append(rel)
        report = "\n\n".join(
            f"[{getattr(f, 'kind', 'E2E')}] {f.name}\n{f.message}\n{f.stack[:1000]}"
            for f in global_failures[:6])
        snap = FileSnapshot(proj_dir)
        snap.capture([p for p in arch.files
                      if p.startswith(("app/", "components/", "lib/"))])
        fixed = _repair_runtime(
            arch, proj_dir, qa, analyzer, report, "", 100 + grnd,
            model=QASession.model_for(qa, arch), focus_paths=targets,
            strict_scope=True)
        if not fixed:
            break
        problems, _ = check_syntax(
            proj_dir, {p: arch.files.get(p, "") for p in fixed})
        if problems:
            snap.restore(fixed)
            elog("WARN", "   ↩ global E2E repair did not parse — reverted")
            break
        out["fixed"] += len(fixed)
        try:
            agent.invalidate_runtime_evidence()
        except Exception:
            pass
        rerun_mark = dev_log_mark()
        global_failures = agent.global_integrity()
        for fault in terminal_faults(dev_log_since(rerun_mark)):
            rel_m = re.search(r"((?:app|components|lib)[\\/][A-Za-z0-9_./\\\[\]-]+\.(?:jsx?|mjs))", fault)
            rel = rel_m.group(1).replace("\\", "/") if rel_m else ""
            global_failures.append(TestFailure(
                test_file="tests/e2e/__global-integrity__.spec.js", target=rel,
                name="Next runtime error during global integrity", message=fault[:700],
                stack=fault[:3000], kind="CRASH"))

    if global_failures:
        out["failed"] += len(global_failures)
        all_fail.extend(_e2e_detail(global_failures))
        elog("WARN", f"   ⚠ global E2E integrity still has "
                     f"{len(global_failures)} failure(s)")
    else:
        elog("INFO", "   ✅ global E2E route/role integrity is clean")

    out["flows"] = flows
    out["failures"] = all_fail
    out["flow"] = (flows[0]["flow"] if flows and len(flows) == 1
                   else f"{out['passed']} of {total} journeys pass")
    if total > 1:
        elog("INFO" if out["passed"] == total else "WARN",
             f"   🎭 {out['passed']} of {total} journeys pass end to end")

    if out["fixed"]:
        elog("INFO", f"   🔨 {out['fixed']} file(s) repaired during end-to-end "
                     f"— one production build to confirm they compile")
        _stop_dev_proc()
        built = run_build_fix_loop(arch, proj_dir, True, max_rounds=1)
        out["build_after_fix"] = bool(built)
        if not built:
            elog("WARN", "   ↩ E2E repair transaction failed the production build — restoring the pre-E2E source")
            stage_baseline.restore()
            for rel in list(arch.files):
                if (rel.startswith(("app/", "components/", "lib/"))
                        and rel not in stage_source_set):
                    try:
                        (proj_dir / rel).unlink(missing_ok=True)
                    except Exception:
                        pass
            try:
                arch.load_existing()
            except Exception:
                pass
            errors, conclusive = _npm_build_errors(proj_dir, "next")
            if conclusive and not errors:
                elog("INFO", "   ✅ pre-E2E source restored and build is clean")
            out["fixed"] = 0
            out["failed"] += 1
            out["failures"].append({
                "case": "E2E repair transaction", "target": "", "kind": "BUILD",
                "message": "E2E candidate repairs were rolled back because the production build was not green",
                "stack": "",
            })
        start_next(proj_dir)
        _forget_warm(agent)
        wait_for_next()

    _e2e_final_clean_room(agent, arch, proj_dir, qa, analyzer,
                          journeys, flows, out)
    return out


def _repair_contract_backed_selector_failure(agent, arch, proj_dir, analyzer,
                                             failures) -> tuple:
    """Repair a soft E2E miss only when independent architecture evidence says app."""
    targets = {str(getattr(f, "target", "") or "").strip()
               for f in (failures or [])}
    targets.discard("")
    if not targets:
        return 0, []

    try:
        scanned = analyzer.scan()
    except Exception as e:
        log.debug(f"selector arbitration scan: {e}")
        return 0, []

    relevant = []
    flow_codes = {
        "BROKEN_CONTRACT", "NO_WAY_THERE", "UNBUILT_PROMISE",
        "MISSING_PLANNED_DATA", "INERT_CONTROL", "ROLE_REDIRECT",
        "MONGO_ID_TYPE", "DYNAMIC_ROUTE_ERROR",
    }
    for finding in (getattr(scanned, "findings", None) or []):
        if getattr(finding, "code", "") not in flow_codes:
            continue
        paths = {str(getattr(finding, "path", "") or "").strip()}
        paths |= {str(x or "").strip() for x in
                  (getattr(finding, "extra", None) or [])}
        paths.discard("")
        if targets & paths:
            relevant.append(finding)

    if not relevant:
        try:
            semantic = analyzer.unbuilt_promises(max_reads=4)
        except Exception as e:
            log.debug(f"selector arbitration semantic audit: {e}")
            semantic = []
        for finding in semantic or []:
            if getattr(finding, "code", "") != "UNBUILT_PROMISE":
                continue
            paths = {str(getattr(finding, "path", "") or "").strip()}
            paths |= {str(x or "").strip() for x in
                      (getattr(finding, "extra", None) or [])}
            paths.discard("")
            if targets & paths:
                relevant.append(finding)

    if not relevant:
        return 0, []

    elog("INFO", "   🔗 E2E miss matches independent capability/flow evidence "
                 "— repairing the app, not the test")
    for f in relevant[:4]:
        elog("WARN", f"      [{getattr(f, 'code', '')}] "
                     f"{getattr(f, 'message', '')[:140]}")

    snap = FileSnapshot(proj_dir)
    capture_paths = {rel for rel in arch.files
                     if rel.startswith(("app/", "components/", "lib/"))}
    for finding in relevant:
        for rel in [getattr(finding, "path", "")] + list(
                getattr(finding, "extra", None) or []):
            rel = str(rel or "").strip().lstrip("./").replace("\\", "/")
            if rel.startswith(("app/", "components/", "lib/")):
                capture_paths.add(rel)
    snap.capture(sorted(capture_paths))

    report = AnalyzerReport()
    report.findings = relevant
    report.missing = []
    try:
        analyzer.repair(report)
    except Exception as e:
        elog("WARN", f"   ⚠ contract-backed selector repair failed: {e}")
        log.exception("e2e selector contract repair")
        return 0, []

    try:
        arch.repair_missing_imports()
    except Exception:
        pass

    changed = snap.changed()
    if not changed:
        return 0, []

    problems, _ = check_syntax(
        proj_dir, {rel: arch.files.get(rel, "") for rel in changed})
    if problems:
        elog("WARN", "   ↩ contract-backed selector repair does not parse — "
                     "reverting it")
        snap.restore()
        try:
            arch.load_existing()
        except Exception:
            pass
        return 0, []

    return len(changed), changed


def _e2e_scenario_issue(agent, sc, journey=None) -> str:
    """Authoring defects that should be rewritten before opening a browser."""
    notes = normalize_scenario_selectors(sc)
    for note in notes[:3]:
        elog("INFO", f"   ↪ normalized scenario locator grammar — {note}")
    why = sc.is_runnable()
    if why:
        return why
    contract = (journey or {}).get("contract") or {}
    why = scenario_contract_issue(contract, sc, agent._is_business_step)
    if why:
        return why
    executable = re.compile(
        r"^(?:GOTO|FILL|SELECT|CLICK|WAIT_FOR|EXPECT_TEXT|EXPECT_URL|"
        r"EXPECT_VALUE|EXPECT_NO_ERROR)\b", re.I)
    dropped = [(line, reason) for _, line, reason in (getattr(sc, "dropped", None) or [])
               if executable.search(str(line or ""))]
    if dropped:
        sample = "; ".join(f"{line} ({reason})" for line, reason in dropped[:3])
        return f"{len(dropped)} executable step(s) were dropped: {sample}"
    try:
        grounded = agent.grounding_issue(sc, journey)
    except Exception as e:
        log.debug(f"e2e grounding preflight: {e}")
        grounded = ""
    return grounded or ""


def _e2e_agentic_diagnosis(agent, arch, analyzer, failures, journey, dev_trace,
                           model=None, debugger=None, scenario=None) -> dict:
    """Claude/Codex-style tool loop: inspect first, decide second, edit later."""
    debugger = debugger or AgenticE2EDebugger(
        agent, arch, analyzer=analyzer, model=model,
        notebook=DebugNotebook(goal=str((journey or {}).get("title") or "")))
    elog("INFO", "   🧠 Agentic debugger — inspect → hypothesis → tool → verdict")
    diag = debugger.investigate(
        failures, journey=journey, scenario=scenario, dev_trace=dev_trace)

    auth_priv = {"lib/auth.js", "lib/auth-client.js",
                 "app/api/auth/[...all]/route.js"}
    files = [str(x or "").strip() for x in (diag.get("files") or [])]

    try:
        sites = agent.console_bug_sites() or []
    except Exception as e:
        log.debug(f"console bug sites: {e}")
        sites = []
    if sites:
        named = []
        for rel, line, _why in sites[:4]:
            if rel and rel not in named:
                named.append(rel)
        files = named + [f for f in files if f not in named]
        elog("INFO", "   \U0001f4cd the browser's stack names: "
                     + ", ".join(f"{r}:{ln}" if ln else r
                                 for r, ln, _ in sites[:3]))
    if diag.get("verdict") == "AUTH_FIX":
        diag["privileged"] = [x for x in files if x in auth_priv]
    else:
        diag["privileged"] = []
        diag["files"] = [x for x in files if x not in auth_priv]

    elog("INFO", f"   🤖 E2E diagnosis — {diag.get('verdict', 'UNKNOWN')}: "
                 f"{str(diag.get('root') or 'no root text')[:180]}")
    if diag.get("files"):
        elog("INFO", "      evidence-scoped files: "
                     + ", ".join(diag.get("files")[:4]))
    if diag.get("hypothesis"):
        elog("INFO", "      hypothesis: " + str(diag.get("hypothesis"))[:180])
    return diag


def _runtime_contract_failure(agent, sc, journey):
    why = runtime_contract_issue((journey or {}).get("contract") or {}, sc,
                                 getattr(agent, "_last_run_evidence", {}) or {},
                                 agent._is_business_step)
    if not why:
        return []
    ev = getattr(agent, "_last_run_evidence", {}) or {}
    return [TestFailure(test_file=sc.spec_path(), target=str(ev.get("target") or ""),
                        name="capability side-effect proof", message=why,
                        stack=str(ev.get("api_network") or "")[:3000], kind="BEHAVIOR")]


def _reseed_for_journey(agent, proj_dir, journey_no: int) -> bool:
    """Put the fixtures back before a journey that follows a mutating one."""
    if journey_no <= 1:
        return False
    if not getattr(MONGO, "available", False):
        return False
    if not getattr(agent, "_mutation_ok", None):
        return False                      # Nothing was consumed; state is fine
    try:
        res = MONGO.reset_project_db(proj_dir, node_bin=NODE_BIN) or {}
    except Exception as e:                                 # noqa: BLE001
        log.debug(f"journey reseed: {e}")
        return False
    if not res.get("ok"):
        elog("INFO", "   ♻ fixtures left as they are — "
                     f"{res.get('error') or 'the database was not resettable'}")
        return False

    # A fresh process is what makes the app seed again
    _restart_for_reseed(proj_dir)
    # And the shell answering is not the seed finishing
    _wait_for_seeded_accounts(agent)
    _forget_warm(agent)
    try:
        agent._mutation_ok = set()
        agent._replay_dupe_urls = set()
        agent._replay_dupe_logged = False
        agent.invalidate_runtime_evidence()
    except Exception:
        pass
    elog("INFO", "   ♻ fixtures reseeded — this journey starts from the "
                 "state its steps were written against")
    return True


def _e2e_one_flow(agent, arch, proj_dir, qa, analyzer, out, journey=None):
    """Walk one journey with a Claude/Codex-style autonomous debug loop."""
    mark = dev_log_mark()
    notebook = DebugNotebook(goal=str((journey or {}).get("title") or ""))
    debugger = AgenticE2EDebugger(
        agent, arch, analyzer=analyzer, model=QASession.model_for(qa, arch),
        notebook=notebook)

    sc = agent.author(journey=journey)
    why = _e2e_scenario_issue(agent, sc, journey)
    for attempt in range(1, E2E_AUTHOR_REWRITE_ATTEMPTS + 1):
        if not why:
            break
        if getattr(sc, "blocked", False):
            # Rewriting cannot cure a busy daemon, and each try costs a call.
            elog("WARN", "   ⏸ the QA model never answered — not spending a "
                         "rewrite on that")
            break
        elog("WARN", f"   ⚠ the flow was not usable — {why}; one bounded rewrite "
                     f"({attempt}/{E2E_AUTHOR_REWRITE_ATTEMPTS})")
        sc = agent.author(previous=sc, why=why, journey=journey)
        why = _e2e_scenario_issue(agent, sc, journey)
    if why and getattr(sc, "blocked", False):
        title = str((journey or {}).get("title") or "required journey")
        out["flow"] = title
        out["blocked"] = 1
        out["blocked_why"] = str(getattr(sc, "blocked_why", ""))[:300]
        elog("WARN", f"   ⏸ {title} was never walked — the QA model stayed "
                     f"unavailable. This is NOT a pass and NOT an app defect; "
                     f"re-run the E2E gate when the model is free.")
        emit({"type": "test_result", "status": "skip",
              "msg": f"End-to-end blocked: {title}",
              "detail": out["blocked_why"]})
        return
    if why:
        title = str((journey or {}).get("title") or getattr(sc, "title", "") or "required journey")
        failure = TestFailure(
            test_file=getattr(sc, "spec_path", lambda: "tests/e2e/__authoring__.spec.js")(),
            target="", name=f"{title} scenario could not be grounded",
            message=str(why), stack="", kind="ASSERTION")
        out["flow"] = title
        out["failed"] = 1
        out["failures"] = _e2e_detail([failure])
        elog("WARN", f"   ⚠ End-to-end flow could not be grounded — {why}")
        emit({"type": "test_result", "status": "fail",
              "msg": f"End-to-end authoring: {title}", "detail": str(why)[:200]})
        return out

    out["flow"] = sc.title
    elog("INFO", f"   🎭 {sc.title}  ({len(sc.steps)} steps)")
    try:
        # Bind locators to what the DOM really has before spending
        bound = agent.preground(sc, journey)
        if bound:
            elog("INFO", f"   🧲 {bound} selector(s) bound to the live DOM "
                         "before the first run")
    except Exception as e:
        log.debug(f"preground: {e}")
    agent.write_spec(sc)
    failures = agent.run(sc, journey=journey)
    if not failures:
        failures = _runtime_contract_failure(agent, sc, journey)
    out["ran"] = True
    if not failures:
        agent.remember_scenario(journey, sc)
        elog("INFO", "   ✅ the end-to-end flow passed")
        emit({"type": "test_result", "status": "pass",
              "msg": f"End-to-end: {sc.title}",
              "detail": f"{len(sc.steps)} steps"})
        return out

    out["failed"] = len(failures)
    out["failures"] = _e2e_detail(failures)
    for f in failures[:5]:
        emit({"type": "test_result", "status": "fail",
              "msg": f"End-to-end: {f.name}", "detail": f.message[:200]})

    round_budget = max(E2E_BASE_FIX, E2E_MIN_FIX)
    rnd = 0
    no_progress = 0
    seen_signatures = {_e2e_failure_signature(failures)}

    while failures and rnd < round_budget and rnd < E2E_HARD_FIX:
        rnd += 1
        real = list(failures)
        ephase({"phase": -18, "title": f"Agentic E2E debug (round {rnd})",
                "status": "active"})
        elog("INFO", f"   🧠 Agentic E2E round {rnd}/{round_budget}"
                     + (f" (hard cap {E2E_HARD_FIX})"
                        if round_budget < E2E_HARD_FIX else "")
                     + f" — {len(real)} failure(s)")

        trace = _filter_db_noise(dev_log_since(mark), True)
        diag = _e2e_agentic_diagnosis(
            agent, arch, analyzer, real, journey, trace,
            model=QASession.model_for(qa, arch), debugger=debugger, scenario=sc)
        verdict = diag.get("verdict") or "UNKNOWN"
        old_failure = real[0]
        before = failures
        try:
            before_step = int((getattr(agent, "_last_run_evidence", {}) or {}).get("failed_index", -1))
        except Exception:
            before_step = -1
        mark = dev_log_mark()

        if verdict == "TEST_FIX":
            patched = agent.patch_scenario_step(
                sc, old_failure, diag.get("test_patch") or "")
            if not patched:
                exact_patch = debugger.propose_test_patch(old_failure, sc)
                patched = agent.patch_scenario_step(sc, old_failure, exact_patch)
            rejected = str(getattr(agent, "_last_patch_rejection", "") or "")
            if not patched and rejected:
                # Round 2 proposed the identical dead locator once
                try:
                    debugger.note_rejected_patch(rejected)
                except Exception as e:
                    log.debug(f"note rejected patch: {e}")
            if patched:
                elog("INFO", "   🧪 TEST_FIX — patched exactly one failing scenario step")
                agent.write_spec(sc)
                failures = agent.run_resume(sc, journey=journey,
                                            failure=old_failure)
            else:
                nfix, changed = _repair_contract_backed_selector_failure(
                    agent, arch, proj_dir, analyzer, real)
                if changed:
                    elog("INFO", "   🔗 TEST_FIX escalation found an app-side contract defect")
                    out["fixed"] += nfix
                    try:
                        agent.invalidate_runtime_evidence()
                    except Exception:
                        pass
                    failures = agent.run_resume(sc, journey=journey,
                                                failure=old_failure)
                else:
                    # The mechanical fallback scores locators and throws away
                    # anything it is not sure of. The model reads the same
                    # evidence and sometimes finds a handle the scoring
                    # discarded — a label, say, where the input has no `name`.
                    # If the DOM really offered what it named, that is
                    # evidence, not a guess, and refusing it loses a fix
                    # nobody else was going to make.
                    own = str(diag.get("test_patch") or "").strip()
                    trusted = False
                    try:
                        trusted = bool(own) and agent.patch_is_observed(own)
                    except Exception as e:                      # noqa: BLE001
                        log.debug(f"patch grounding check: {e}")
                    if trusted and agent.patch_scenario_step(sc, old_failure, own):
                        elog("INFO", "   🧪 TEST_FIX — the debugger's own patch "
                                     "names a control the DOM really exposed; "
                                     "taking it")
                        agent.write_spec(sc)
                        failures = agent.run_resume(sc, journey=journey,
                                                    failure=old_failure)
                    else:
                        elog("WARN", "   ⚠ TEST_FIX named nothing the DOM "
                                     "exposed and there is no app-side defect "
                                     "behind it — preserving the scenario")
                        failures = before

        elif verdict == "DATA_PREREQUISITE":
            instruction = (diag.get("next") or diag.get("root") or
                           "establish the required record/state through an existing user workflow before asserting it")
            elog("INFO", "   🌱 DATA_PREREQUISITE — fixing journey setup, not inventing UI/data")
            sc2 = agent.author(
                previous=sc,
                why="Add the real prerequisite setup using existing routes/controls. " + instruction,
                page=old_failure.target, journey=journey)
            unusable = _e2e_scenario_issue(agent, sc2, journey)
            if unusable:
                failures = before
            else:
                sc = sc2
                agent.write_spec(sc)
                failures = agent.run(sc, journey=journey)

        elif verdict == "BLOCKED_UPSTREAM":
            out["blocked_upstream"] = True
            out["blocked_reason"] = (diag.get("root") or diag.get("next") or
                                     "upstream journey failed")
            elog("WARN", "   ⛓ E2E journey appears blocked by an upstream business outcome — "
                         + str(out["blocked_reason"])[:220])
            failures = before

        elif verdict == "RETRY_TRANSIENT":
            elog("INFO", "   ♻ transient incident — retrying from the last browser checkpoint")
            failures = agent.run_resume(sc, journey=journey,
                                        failure=old_failure)

        elif verdict == "UNKNOWN":
            nfix, changed = _repair_contract_backed_selector_failure(
                agent, arch, proj_dir, analyzer, real)
            if changed:
                elog("INFO", "   🧭 UNKNOWN rescued by independent capability/flow evidence")
                out["fixed"] += nfix
                try:
                    agent.invalidate_runtime_evidence()
                except Exception:
                    pass
                failures = agent.run_resume(sc, journey=journey, failure=old_failure)
            else:
                elog("WARN", "   ⚠ debugger could not establish a safe root cause — preserving the app and trying a different hypothesis")
                failures = before

        else:  # APP_FIX / AUTH_FIX / ROUTE_FIX
            targets = []
            for rel in (diag.get("files") or []):
                rel = str(rel or "").strip()
                if rel and rel not in targets:
                    targets.append(rel)
            for f in real:
                rel = str(getattr(f, "target", "") or "").strip()
                if rel and rel not in targets:
                    targets.append(rel)

            report = "\n\n".join(
                f"[{getattr(f, 'kind', 'E2E')}] {f.name}\n{f.message}\n{f.stack[:1800]}"
                for f in real[:5])
            report += (
                "\n\nAGENTIC ROOT CAUSE\n" + str(diag.get("root") or "")
                + "\nHYPOTHESIS\n" + str(diag.get("hypothesis") or "")
                + "\nNEXT\n" + str(diag.get("next") or "")
            )
            if diag.get("locations"):
                report += "\nRUNTIME SOURCE LOCATIONS (highest-priority evidence)\n"
                for loc in (diag.get("locations") or [])[:6]:
                    if not isinstance(loc, dict):
                        continue
                    report += (f"- {loc.get('path')}:{loc.get('line')}:{loc.get('column', 0)} "
                               f"[{loc.get('signal', 'runtime')}]\n")

            snap = FileSnapshot(proj_dir)
            snap.capture([p for p in arch.files
                          if p.startswith(("app/", "components/", "lib/"))])
            exact_runtime_scope = bool(diag.get("locations"))
            if exact_runtime_scope:
                exact = []
                for loc in (diag.get("locations") or []):
                    rel = str((loc or {}).get("path") or "").strip() if isinstance(loc, dict) else ""
                    if rel and rel in arch.files and rel not in exact:
                        exact.append(rel)
                if exact:
                    targets = exact
            before_sources = {p: str(arch.files.get(p, "") or "")
                              for p in arch.files
                              if p.startswith(("app/", "components/", "lib/"))}
            fixed = _repair_runtime(
                arch, proj_dir, qa, analyzer, report, trace, rnd,
                model=QASession.model_for(qa, arch), focus_paths=targets,
                strict_scope=True,
                privileged_paths=diag.get("privileged") or [],
                exact_scope=exact_runtime_scope)

            if fixed:
                violations = validate_repair_invariants(
                    before_sources, arch.files, fixed, verdict, diag,
                    failure=old_failure, journey=journey)
                if violations:
                    elog("WARN", "   ↩ repair invariant guard rejected the candidate patch")
                    for item in violations[:4]:
                        elog("WARN", f"      • {item}")
                    snap.restore(fixed)
                    try:
                        arch.load_existing()
                    except Exception:
                        pass
                    guarded_report = report + repair_guard_feedback(
                        violations, diag, failure=old_failure)
                    fixed = _repair_runtime(
                        arch, proj_dir, qa, analyzer, guarded_report, trace, rnd,
                        model=QASession.model_for(qa, arch), focus_paths=targets,
                        strict_scope=True,
                        privileged_paths=diag.get("privileged") or [],
                        exact_scope=exact_runtime_scope)
                    if fixed:
                        violations = validate_repair_invariants(
                            before_sources, arch.files, fixed, verdict, diag,
                            failure=old_failure, journey=journey)
                        if violations:
                            elog("WARN", "   ↩ corrected repair still crossed the evidence boundary — reverted")
                            snap.restore(fixed)
                            try:
                                arch.load_existing()
                            except Exception:
                                pass
                            fixed = []

            ephase({"phase": -18, "title": f"Agentic E2E debug (round {rnd})",
                    "status": "done", "written": len(fixed)})
            if not fixed:
                elog("WARN", "   ⚠ evidence-scoped fixer changed nothing safe")
                failures = before
            else:
                problems, _ = check_syntax(
                    proj_dir, {p: arch.files.get(p, "") for p in fixed})
                if problems:
                    elog("WARN", f"   ↩ E2E fix does not parse ({syntax_messages(problems)[0][:90]}) — reverting")
                    snap.restore(fixed)
                    try:
                        arch.load_existing()
                    except Exception:
                        pass
                    failures = before
                else:
                    out["fixed"] += len(fixed)
                    try:
                        agent.invalidate_runtime_evidence()
                    except Exception:
                        pass
                    time.sleep(0.15)
                    failures = agent.run_resume(sc, journey=journey,
                                                failure=old_failure)

        if not failures:
            failures = _runtime_contract_failure(agent, sc, journey)
        out["failed"] = len(failures)
        out["failures"] = _e2e_detail(failures)
        if not failures:
            out["blocked_upstream"] = False
            out["blocked_reason"] = ""
            agent.remember_scenario(journey, sc)
            debugger.notebook.record_outcome(diag, progressed=True)
            elog("INFO", f"   ✅ the end-to-end flow passes after agentic round {rnd}")
            emit({"type": "test_result", "status": "pass",
                  "msg": f"End-to-end: {sc.title}",
                  "detail": f"agentic repair in {rnd} round(s)"})
            break

        try:
            after_step = int((getattr(agent, "_last_run_evidence", {}) or {}).get("failed_index", -1))
        except Exception:
            after_step = -1
        progressed, reason = _e2e_progress(
            before, failures, seen_signatures,
            before_step=before_step, after_step=after_step)
        debugger.notebook.record_outcome(diag, progressed=progressed)
        new_sig = _e2e_failure_signature(failures)
        if progressed:
            no_progress = 0
            if new_sig:
                seen_signatures.add(new_sig)
            elog("INFO", f"   ↗ agentic E2E progress — {reason}")
            old_budget = round_budget
            round_budget = _e2e_extend_budget(
                rnd, round_budget, E2E_HARD_FIX, E2E_PROGRESS_BONUS, True)
            if round_budget > old_budget:
                elog("INFO", f"   ➕ progress earned {round_budget - old_budget} more E2E repair round(s) "
                             f"— budget {old_budget}→{round_budget}, hard cap {E2E_HARD_FIX}")
        else:
            # A round the debugger never decided is not a round that failed.
            # It costs a call, tells us nothing, and must not spend the budget
            # that exists for actually trying things.
            undecided = (str(diag.get("verdict") or "UNKNOWN").upper() == "UNKNOWN"
                         and not str(diag.get("root") or "").strip())
            if undecided and rnd < E2E_HARD_FIX:
                elog("WARN", f"   ↻ round {rnd} produced no verdict at all — "
                             f"that is the debugger, not the app. Re-asking "
                             f"rather than spending a repair round.")
                if new_sig:
                    seen_signatures.discard(new_sig)
                if round_budget < E2E_HARD_FIX:
                    round_budget += 1          # give the round back
                continue

            no_progress += 1
            if new_sig:
                seen_signatures.add(new_sig)
            if rnd < E2E_MIN_FIX:
                elog("WARN", f"   ↔ no progress in round {rnd}; trying another hypothesis — minimum {E2E_MIN_FIX} rounds")
            else:
                elog("WARN", f"   ↔ no progress after required round {rnd}")
            exhausted = bool(diag.get("exhausted"))
            if _e2e_stop_no_progress(
                    rnd, progressed, E2E_MIN_FIX, exhausted=exhausted):
                if exhausted:
                    elog("WARN", f"   ⏹ round {rnd} could only repeat a hypothesis the browser "
                                 "already rejected — evidence is exhausted, so no guess/retry is allowed")
                else:
                    elog("WARN", f"   ⏹ no journey progress after round {rnd}; stopping this obstruction")
                break

        elog("WARN", f"   ⚠ still failing after agentic round {rnd}: "
                     f"{failures[0].message[:110]}")

    if failures:
        if E2E_HARD_FIX and rnd >= E2E_HARD_FIX:
            elog("WARN", f"   ⏹ E2E repair reached hard cap {E2E_HARD_FIX} — recording the failure "
                         "and moving to the next journey")
        elif E2E_HARD_FIX and rnd >= round_budget:
            elog("WARN", f"   ⏹ E2E repair used its current {round_budget}-round budget without "
                         "further journey progress — recording the failure")
        elif not E2E_HARD_FIX:
            elog("WARN", "   ⏹ E2E auto-repair is disabled — recording the failure")
    return out


def _by_test_file(failures):
    """Group failures by test file — one model call per file, not per case."""
    groups = {}
    for f in failures:
        groups.setdefault(f.test_file, []).append(f)
    return [groups[k] for k in sorted(groups)]
