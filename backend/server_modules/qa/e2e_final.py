# Final clean-room E2E proof after every repair has settled.


def _e2e_clean_boot(proj_dir: Path, agent) -> bool:
    """Fresh server and, when safe, a fresh AgentForge-owned database."""
    _stop_dev_proc()
    if E2E_FINAL_RESET_DB:
        try:
            result = MONGO.reset_project_db(proj_dir, node_bin=NODE_BIN)
            if not result.get("ok"):
                elog("INFO", "   ↪ clean-room DB reset skipped — "
                     + str(result.get("error") or "not available")[:180])
        except Exception as e:
            elog("WARN", f"   ⚠ clean-room DB reset failed safely: {e}")
    start_next(proj_dir)
    if not wait_for_next():
        return False
    try:
        # Most apps seed from the public shell or layout.
        requests.get(f"http://localhost:{DEV_PORT}/", timeout=20)
    except Exception:
        pass
    try:
        agent.invalidate_runtime_evidence()
    except Exception:
        pass
    try:
        # The replays about to run decide their own reseeds
        agent._mutation_ok = set()
    except Exception:
        pass
    return True


def _e2e_clean_room_once(agent, journeys, proj_dir: Path) -> dict:
    """Replay the exact last-green scenarios in fresh browser contexts."""
    results = {}
    if not _e2e_clean_boot(proj_dir, agent):
        failure = TestFailure(
            test_file="tests/e2e/__clean-room__.spec.js", target="",
            name="clean-room dev server", message="fresh dev server did not become ready",
            stack="", kind="CRASH")
        return {"__server__": [failure]}

    for n, journey in enumerate(journeys, start=1):
        title = str(journey.get("title") or f"journey {n}")
        sc = agent.accepted_scenario(journey)
        if sc is None:
            results[title] = [TestFailure(
                test_file="tests/e2e/__clean-room__.spec.js", target="",
                name=f"{title} has no accepted scenario",
                message="the journey never produced a green grounded scenario",
                stack="", kind="ASSERTION")]
            continue
        # Each accepted scenario was authored and proven
        try:
            _reseed_for_journey(agent, proj_dir, n)
        except Exception as e:
            log.debug(f"clean-room reseed: {e}")
        elog("INFO", f"   🧼 clean-room {n}/{len(journeys)} — {title}")
        try:
            failures = agent.run(sc, journey=journey)
            if not failures:
                failures = _runtime_contract_failure(agent, sc, journey)
        except Exception as e:
            failures = [TestFailure(
                test_file=sc.spec_path(), target="", name=title,
                message=f"clean-room replay crashed: {type(e).__name__}: {e}"[:500],
                stack="", kind="CRASH")]
        results[title] = failures or []
        if failures:
            elog("WARN", f"   ❌ clean-room replay failed — {title}: "
                 f"{failures[0].message[:160]}")
        else:
            elog("INFO", f"   ✅ clean-room replay passed — {title}")
    return results


def _e2e_clean_failures(results: dict) -> list:
    out = []
    for failures in results.values():
        out.extend(failures or [])
    return out


def _e2e_final_clean_room(agent, arch, proj_dir: Path, qa, analyzer,
                          journeys, flows, out) -> None:
    """Last gate: fresh DB/server/browser, then the exact accepted journeys."""
    if not E2E_FINAL_CLEAN_ROOM:
        return
    if out.get("failed"):
        elog("INFO", "   ⏭ final clean-room replay waits for the ordinary E2E gate to become green")
        return
    if any(agent.accepted_scenario(j) is None for j in journeys):
        elog("WARN", "   ⚠ final clean-room replay cannot start — at least one required journey has no green scenario")
        return

    elog("INFO", "   🧼 final clean-room E2E — fresh DB/server/browser contexts")
    results = _e2e_clean_room_once(agent, journeys, proj_dir)
    failures = _e2e_clean_failures(results)

    for attempt in range(1, E2E_FINAL_REPAIR_ATTEMPTS + 1):
        if not failures:
            break
        elog("WARN", f"   🛠 clean-room regression recovery {attempt}/{E2E_FINAL_REPAIR_ATTEMPTS}")
        repaired_any = False
        recovery_code_fixes = 0
        recovery_fixed_before = int(out.get("fixed") or 0)
        recovery_paths = [p for p in arch.files
                          if p.startswith(("app/", "components/", "lib/"))]
        recovery_set = set(recovery_paths)
        recovery_snapshot = FileSnapshot(proj_dir)
        recovery_snapshot.capture(recovery_paths)
        for journey in journeys:
            title = str(journey.get("title") or "")
            if not results.get(title):
                continue
            one = {"ran": False, "flow": "", "passed": 0, "failed": 0,
                   "fixed": 0, "unwritable": 0, "failures": []}
            try:
                one = _e2e_one_flow(agent, arch, proj_dir, qa, analyzer, one, journey)
            except Exception as e:
                elog("WARN", f"   ⚠ clean-room recovery could not run {title}: {e}")
                continue
            nfix = int(one.get("fixed") or 0)
            out["fixed"] += nfix
            recovery_code_fixes += nfix
            repaired_any = repaired_any or bool(one.get("ran") and not one.get("failed"))

        if recovery_code_fixes:
            _stop_dev_proc()
            if not run_build_fix_loop(arch, proj_dir, True, max_rounds=1):
                elog("WARN", "   ↩ clean-room recovery failed the production build — rolling it back")
                recovery_snapshot.restore()
                for rel in list(arch.files):
                    if (rel.startswith(("app/", "components/", "lib/"))
                            and rel not in recovery_set):
                        try:
                            (proj_dir / rel).unlink(missing_ok=True)
                        except Exception:
                            pass
                try:
                    arch.load_existing()
                except Exception:
                    pass
                out["fixed"] = recovery_fixed_before
                break
        if not repaired_any:
            break
        results = _e2e_clean_room_once(agent, journeys, proj_dir)
        failures = _e2e_clean_failures(results)

    if failures:
        out["clean_room"] = False
        out["passed"] = sum(1 for j in journeys
                            if not results.get(str(j.get("title") or "")))
        out["failed"] += len(failures)
        out["failures"].extend(_e2e_detail(failures))
        for flow in flows:
            title = str(flow.get("title") or "")
            if results.get(title):
                flow["clean_room"] = "fail"
                flow["failed"] = len(results[title])
            else:
                flow["clean_room"] = "pass"
        elog("WARN", f"   ❌ final clean-room E2E has {len(failures)} failure(s) — build is not E2E green")
        return

    final_global = agent.global_integrity()
    if final_global:
        out["clean_room"] = False
        out["failed"] += len(final_global)
        out["failures"].extend(_e2e_detail(final_global))
        elog("WARN", f"   ❌ final role integrity has {len(final_global)} failure(s)")
        return

    out["clean_room"] = True
    out["passed"] = len(journeys)
    for flow in flows:
        flow["clean_room"] = "pass"
    elog("INFO", f"   🟢 FINAL E2E GREEN — {len(journeys)}/{len(journeys)} journeys passed in a clean room")
