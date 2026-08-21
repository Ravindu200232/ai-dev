# Unit-test flow and QA report writing.
def write_qa_report(proj_dir: Path, qa, *, unit=None, security=None,
                    audit=None, e2e=None, perf=None, runtime=None,
                    security_ran: bool = True, flow_report=None,
                    flow_clean: bool = False, flow_conclusive: bool = False) -> bool:
    """Put everything the QA stages learned on disk, once, at the end."""
    from datetime import datetime, timezone

    rep = getattr(qa, "report", None)

    def failure(f):
        return {"file": getattr(f, "test_file", ""), "case": getattr(f, "name", ""),
                "target": getattr(f, "target", ""), "kind": getattr(f, "kind", ""),
                "message": getattr(f, "message", "")}

    def finding(f):
        return {"severity": getattr(f, "severity", ""), "code": getattr(f, "code", ""),
                "path": getattr(f, "path", ""), "message": getattr(f, "message", ""),
                "fix": getattr(f, "fix", "")}

    doc = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unit": dict(unit or {}),
        "suite": {
            "passed": getattr(rep, "passed", 0) if rep else 0,
            "failures": [failure(f) for f in (getattr(rep, "failures", []) or [])],
            "unresolved": list(getattr(rep, "unresolved", []) or []) if rep else [],
            "suspects": list(getattr(rep, "suspects", []) or []) if rep else [],
            "quarantined": list(getattr(rep, "quarantined", []) or []) if rep else [],
            "written": list(getattr(rep, "written", []) or []) if rep else [],
            "skipped_reason": getattr(rep, "skipped_reason", "") if rep else "",
        },
        "security": {

            "ran": bool(security_ran),
            "findings": [finding(f) for f in (security or [])],
            "audit": dict(audit or {}),
        },
        "e2e": dict(e2e or {}),
        "architecture": {
            "clean": bool(flow_clean),
            "conclusive": bool(flow_conclusive),
            "findings": [finding(f) for f in (getattr(flow_report, "findings", None) or [])
                         if getattr(f, "severity", "") in ("blocker", "major")],
            "missing": list(getattr(flow_report, "missing", None) or []),
        },
        "performance": dict(perf or {}),

        "runtime": list(runtime or []),
    }
    try:
        out = proj_dir / ".agentforge" / "qa" / "report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        return True
    except Exception as e:
        log.warning(f"could not write the QA report: {e}")
        return False


def _report_unfixed(errors) -> None:
    """Name the runtime bugs that are shipping, rather than "serving as-is"."""
    if not errors:
        return
    elog("WARN", f"   ❗ the app is being served with {len(errors)} known "
                 f"problem(s):")
    for e in errors[:6]:
        elog("WARN", f"      ✗ {' '.join(str(e).split())[:110]}")
    emit({"type": "test_result", "status": "fail",
          "msg": f"{len(errors)} runtime problem(s) unfixed",
          "detail": " | ".join(str(e).splitlines()[0][:80] for e in errors[:4])})


def _leave_unresolved(runner, failures, why: str) -> int:
    """Stop repairing, and say plainly that the suite is NOT green."""
    from qa_agent.runner import diagnose, diagnose_all

    if not failures:
        return 0
    files = _by_test_file(failures)
    qa_report = getattr(runner, "qa", None)
    if qa_report is not None and hasattr(qa_report, "report"):
        seen = {(u.get("file"), u.get("case"))
                for u in getattr(qa_report.report, "unresolved", [])}
        for f in failures:
            if (f.test_file, f.name) in seen:
                continue
            qa_report.report.unresolved.append({
                "file": f.test_file, "case": f.name, "target": f.target,
                "message": f.message, "why": why,
                "diagnosis": diagnose(f.stack or f.message or ""),
            })
    top = diagnose_all(failures)
    elog("WARN", f"   ❗ {len(failures)} case(s) in {len(files)} file(s) are "
                 f"still failing — {why}")
    if top:
        elog("WARN", "   ❗ " + ", ".join(f"{n} × {name}" for name, n in top[:3]))
    for f in failures[:6]:
        elog("WARN", f"      ✗ {f.test_file} — {f.name[:70]}")
    return len(files)


_CASE_LINE_RE = re.compile(
    r"^(\s*)(it|test)(\s*(?:\.\s*\w+)?\s*\(\s*)(['\"`])(.+?)\4", re.M)


def _backfill_tests(arch, proj_dir: Path, qa) -> int:
    """Author tests for every testworthy file the per-task jobs never reached."""

    if not qa or not getattr(qa, "enabled", True) or not qa.author:
        return 0
    try:
        from qa_agent.spec import GENERATED, select_targets
    except Exception as e:
        log.debug(f"backfill: {e}")
        return 0

    SKIP = ("node_modules", ".next", "tests/", ".agentforge", "public/")
    srcs = []
    for f in proj_dir.glob("**/*.js*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(proj_dir)).replace("\\", "/")
        if rel in GENERATED or any(s in rel for s in SKIP):
            continue
        srcs.append(rel)

    tested = {m.get("target") for m in qa.manifest.values() if m.get("target")}
    todo = [r for r in srcs if r not in tested]
    if not todo:
        return 0

    targets = select_targets(todo, qa.read_source, phase=0,
                             already=len(qa.manifest),
                             protected=getattr(arch, "NEXT_PROTECTED", None),
                             limit=len(todo))
    if not targets:
        return 0

    elog("INFO", f"   🧪 {len(targets)} file(s) still have no test — writing "
                 f"them now")
    ephase({"phase": -14, "title": "Backfilling tests", "status": "active"})
    written = []
    try:
        qa.ensure_runner()
        written = qa.author.write_for(targets, 0)
    except Exception as e:
        elog("WARN", f"   ⚠ Backfill failed: {e}")
        log.exception("backfill")
    ephase({"phase": -14, "title": "Backfilling tests", "status": "done",
            "written": len(written)})

    still = [t.path for t in targets
             if t.path not in {m.get("target") for m in qa.manifest.values()}]
    if still:
        elog("WARN", f"   ⚠ {len(still)} file(s) remain untested: "
                     f"{', '.join(p.split('/')[-1] for p in still[:4])}")
    return len(written)


ROUND_ONE_FLOOR = 90


def _record_round_one(proj_dir: Path, qa, passed: int, failures: list) -> None:
    """Say how round one went as a RATE, name what dominated it, and keep it."""
    from datetime import datetime, timezone

    from qa_agent.runner import diagnose_all

    total = passed + len(failures)
    if not total:
        return
    rate = passed * 100 // total
    elog("INFO", f"   📊 round 1: {passed}/{total} passing ({rate}%)")

    top = diagnose_all(failures)
    if top:
        elog("INFO", "   📊 " + ", ".join(f"{n} × {name}" for name, n in top[:3]))
    if rate < ROUND_ONE_FLOOR:
        elog("WARN", f"   ⚠ round-1 pass rate {rate}% — below the "
                     f"{ROUND_ONE_FLOOR}% floor")

    try:
        hist = proj_dir / ".agentforge" / "qa" / "history.jsonl"
        hist.parent.mkdir(parents=True, exist_ok=True)
        with hist.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "cases": total, "passed": passed, "failed": len(failures),
                "rate": rate, "floor": ROUND_ONE_FLOOR,
                "top": [{"class": n, "count": c} for n, c in top[:5]],
            }) + "\n")
    except Exception as e:
        log.warning(f"could not append qa history: {e}")

    try:
        from agents import lessons
        rows = [{"failed": len(failures),
                 "top": [{"class": n, "count": c} for n, c in top[:5]]}]
        lessons.record(proj_dir.name, lessons.from_qa_history(rows))
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"lessons from qa: {e}")


def drop_tests_for_missing_targets(proj_dir: Path, qa) -> list:
    """Remove tests whose subject no longer exists, before the suite runs."""
    if not qa or not getattr(qa, "manifest", None):
        return []
    dropped = []
    for test_path, meta in sorted(qa.manifest.items()):
        target = (meta or {}).get("target") or ""
        if not target:
            continue
        tf = proj_dir / target
        why = ""
        if not tf.is_file():
            why = "the file it tests was deleted"
        else:
            try:
                body = tf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not re.search(r"^\s*export\b", body, re.M):
                why = "the file it tests exports nothing — it was removed"
        if not why:
            continue
        try:
            (proj_dir / test_path).unlink(missing_ok=True)
        except OSError as e:
            log.debug(f"drop {test_path}: {e}")
            continue
        dropped.append((test_path, target, why))

    for test_path, _, _ in dropped:
        qa.manifest.pop(test_path, None)
    if dropped:
        qa._save_manifest()
        qa.report.written = sorted(qa.manifest)
        elog("WARN", f"   🗑 {len(dropped)} test file(s) removed before the run "
                     f"— their subject is gone:")
        for test_path, target, why in dropped:
            elog("WARN", f"      {test_path} → {target}: {why}")
    return dropped


def _tests_for_targets(qa, targets) -> list:
    """The test files written against these source files, from the manifest."""
    wanted = {str(t).replace("\\", "/").lstrip("./") for t in (targets or [])}
    if not wanted:
        return []
    return sorted({rel for rel, meta in (qa.manifest or {}).items()
                   if str((meta or {}).get("target", "")).replace("\\", "/")
                   .lstrip("./") in wanted})


def run_qa_unit_stage(arch, proj_dir: Path, qa, *, build_ok: bool,
                      scope=None) -> dict:
    """Install the runner, run the generated tests, and repair what fails."""
    out = {"ran": False, "passed": 0, "failed": 0, "fixed": 0, "code_fixes": 0}
    if not qa or not qa.has_tests():
        return out
    if not build_ok:
        _qa_skip(qa, "the build is not green, so a failing test would be a "
                     "symptom rather than the bug")
        return out

    deadline = time.time() + QA_DEADLINE
    ephase({"phase": -15, "title": "Running unit tests", "status": "active"})

    harness = TestHarness(proj_dir, callbacks=_qa_callbacks(), cmd=qa.cmd)
    harness.materialise()
    if not harness.install():
        _qa_skip(qa, "the test runner could not be installed")
        ephase({"phase": -15, "title": "Running unit tests", "status": "done"})
        return out

    orphaned = drop_tests_for_missing_targets(proj_dir, qa)
    if not qa.has_tests():
        _qa_skip(qa, "every generated test was written against a file that no "
                     "longer exists")
        ephase({"phase": -15, "title": "Running unit tests", "status": "done"})
        return out

    runner = VitestRunner(proj_dir, cmd=qa.cmd, callbacks=_qa_callbacks(),
                          session=qa)
    fixer = BugFixerAgent(arch, proj_dir, callbacks=_qa_callbacks(), session=qa,
                          model=QASession.model_for(qa, arch))
    snap = FileSnapshot(proj_dir)
    unit_code_baseline = FileSnapshot(proj_dir)
    unit_code_baseline.capture([p for p in arch.files
                                if p.startswith(("app/", "components/", "lib/"))])
    unit_code_touched = False

    previous, stalls, best = None, 0, None

    dead = 0

    tier = 0

    watch = None
    scoped = _tests_for_targets(qa, scope) if scope else []
    if scope:
        if not scoped:
            elog("INFO", "   🧪 Nothing that changed has a test — skipping the "
                         "suite rather than running everything else")
            ephase({"phase": -15, "title": "Running unit tests", "status": "done"})
            return out
        watch = scoped
        others = max(0, len(qa.manifest or {}) - len(scoped))
        elog("INFO", f"   🧪 Testing only what changed — {len(scoped)} test "
                     f"file(s)"
                     + (f", leaving {others} untouched" if others else ""))

    baseline_cases = set()
    limit = MAX_QA_FIX
    for rnd in range(1, QA_ROUND_CEILING + 2):
        passed, failures, ok = runner.run(paths=watch)
        if ok and rnd == 1:
            baseline_cases = runner.case_names()
        if not ok:
            out.update(ran=False, clean=False,
                       infrastructure_error="Vitest produced no JSON report")
            elog("ERROR", "   ❌ Unit QA is inconclusive — no Vitest report was "
                          "produced; this build cannot be marked clean")
            break
        out.update(ran=True, passed=passed, failed=len(failures))
        qa.report.passed, qa.report.failures = passed, failures
        _emit_qa_results(passed, failures, rnd)

        if rnd == 1:
            _record_round_one(proj_dir, qa, passed, failures)

        if not failures:
            elog("INFO", f"   ✅ {passed} unit test(s) passed")
            break

        if rnd > limit:
            elog("WARN", f"   ❌ {len(failures)} unit test(s) still failing "
                         f"after {rnd - 1} repair round(s)")
        else:
            elog("WARN", f"   ❌ {len(failures)} unit test(s) failing "
                         f"(round {rnd}/{limit})")
        if rnd > limit:
            _leave_unresolved(runner, failures,
                             "still failing after every repair round")
            break
        if time.time() > deadline:
            _qa_skip(qa, f"the {QA_DEADLINE}s QA budget is spent")
            _leave_unresolved(runner, failures,
                             f"the {QA_DEADLINE}s QA budget ran out before "
                             f"this could be repaired")
            break

        now_cases = {(f.test_file, f.name) for f in failures}
        worse = (previous is not None
                 and not (now_cases < previous and len(failures) < len(best)))
        if worse:
            stalls += 1
            new_red = sorted(now_cases - previous)
            why = (f"broke {len(new_red)} case(s) that were passing"
                   if new_red else
                   f"did not reduce the failures ({len(best)} → {len(failures)})")
            elog("WARN", f"   ↩ round {rnd - 1} {why} — reverting it")
            reverted = snap.restore()
            if reverted:
                elog("INFO", f"   ↩ restored {len(reverted)} file(s)")
            dead += 1
            if dead >= MAX_QA_DEAD:
                if best is not None:
                    failures = best
                    out["failed"] = len(failures)
                    qa.report.failures = failures
                elog("WARN", f"   ⏹ {dead} rounds in a row left the count "
                             f"where it was ({len(failures)}) — stopping "
                             f"rather than spending the rest on it")
                _qa_skip_note(qa, "the repair rounds stopped making a "
                                  "difference")
                break

            if stalls >= MAX_QA_STALLS:

                if best is not None:
                    failures = best
                    out["failed"] = len(failures)
                    qa.report.failures = failures

                if tier < MAX_QA_TIER:
                    tier = min(MAX_QA_TIER, 2 if tier == 0 else tier + 1)
                    stalls = 0
                    elog("INFO", f"   ⤴ this strategy made no progress — "
                                 f"escalating to {QA_TIERS[tier]} "
                                 f"({tier}/{MAX_QA_TIER})")
                else:
                    elog("WARN", f"   ⚠ every repair strategy is spent — "
                                 f"{len(failures)} case(s) still failing")
                    _leave_unresolved(runner, failures,
                                      "every repair strategy was tried and "
                                      "none of them made it pass")
                    break
            else:
                elog("INFO", f"   ↻ trying again ({stalls}/{MAX_QA_STALLS})")
        else:
            stalls = 0
            had_baseline = previous is not None
            previous, best = now_cases, failures

            snap.forget()
            if had_baseline and limit < QA_ROUND_CEILING:
                limit += 1
                elog("INFO", f"   ↗ that round made progress — round {limit} "
                             f"is now available (ceiling {QA_ROUND_CEILING})")

        ephase({"phase": -16, "title": f"Fixing failing tests (round {rnd})",
                "status": "active"})
        groups = _by_test_file(failures)
        snap.capture([g[0].test_file for g in groups]
                     + [g[0].target for g in groups if g[0].target])

        code_written, lock = [], threading.Lock()

        def repair(group):
            if time.time() > deadline:
                return
            try:
                v = fixer.fix(group, build_ok=build_ok, round_no=rnd,
                              tier=tier)
            except Exception as e:
                elog("WARN", f"   ⚠ repair failed on {group[0].test_file}: {e}")
                log.exception("qa fix")
                return
            with lock:
                if v.quarantine:
                    runner.quarantine(v.test_file, v.evidence or "set aside")
                elif v.touched_code:
                    code_written.append(v.written)
                    out["code_fixes"] += 1
                elif v.written:
                    out["fixed"] += 1

        with ThreadPoolExecutor(max_workers=QA_FIX_WORKERS) as pool:
            list(pool.map(repair, groups))

        watch = sorted({g[0].test_file for g in groups})
        ephase({"phase": -16, "title": f"Fixing failing tests (round {rnd})",
                "status": "done"})

        if code_written:
            unit_code_touched = True
            problems, _ = check_syntax(
                proj_dir, {p: arch.files.get(p, "") for p in code_written})
            if problems:
                elog("WARN", "   ↩ a QA code fix does not parse — reverting "
                             "this round without paying for a full Next build")
                snap.restore(code_written)
                out["code_fixes"] -= len(code_written)
                unit_code_touched = out.get("code_fixes", 0) > 0

    # Every write this stage made went to a file in `watch`
    saw_everything = watch is None
    swept_everything = watch is not None and ok and not scoped
    if swept_everything and not unit_code_touched and not failures:
        elog("INFO", "   ⏭ the rest of the suite is unchanged since it last "
                     "ran green — not running it again")
        saw_everything = True
        swept_everything = False
    if swept_everything:
        passed, failures, ok = runner.run()
        if ok:
            saw_everything = True
            out.update(passed=passed, failed=len(failures))
            qa.report.passed, qa.report.failures = passed, failures
            if failures:
                elog("WARN", f"   ⚠ {len(failures)} test(s) the targeted runs "
                             f"did not cover are failing")

    if failures:
        _leave_unresolved(runner, failures, "still failing when the stage ended")
        out["failed"] = len(failures)
        qa.report.failures = failures

    left = runner.skipped() if saw_everything else []
    if left:
        out["skipped"] = len(left)
        elog("WARN", f"   ❗ {len(left)} case(s) are marked it.skip and never "
                     f"ran — they are not passing")
        for path, name in left[:5]:
            elog("WARN", f"      ⤬ {path} — {name[:70]}")

    gone = (runner.vanished(baseline_cases)
            if baseline_cases and saw_everything else [])
    if gone:
        out["deleted"] = len(gone)
        elog("WARN", f"   ❗ {len(gone)} case(s) that existed at the start of "
                     f"the stage are gone from the suite")
        for path, name in gone[:5]:
            elog("WARN", f"      ⤬ {path} — {name[:70]}")

    if orphaned:
        out["orphaned"] = [t for t, _, _ in orphaned]
    out["clean"] = not failures and not left and not gone

    if unit_code_touched:
        elog("INFO", "   🔨 unit repair changed app code — one production build to confirm it")
        if not run_build_fix_loop(arch, proj_dir, True, max_rounds=1):
            elog("WARN", "   ↩ unit-stage code changes could not produce a clean build — reverting production code to the pre-unit baseline")
            unit_code_baseline.restore()
            run_build_fix_loop(arch, proj_dir, True, max_rounds=1)
            out["code_fixes"] = 0
            out["stale_after_revert"] = True
            out["clean"] = False

    ephase({"phase": -15, "title": "Running unit tests", "status": "done"})
    if fixer.verdicts:
        elog("INFO", f"   🧪 {fixer.summary()}")
    for s in qa.report.suspects[:5]:
        emit({"type": "test_result", "status": "warn",
              "msg": f"Possible bug in {s['test']}", "detail": s["note"]})
    return out


from server_modules.qa.e2e_policy import (
    E2E_AUTHOR_REWRITE_ATTEMPTS, E2E_BASE_FIX, E2E_GLOBAL_REPAIR_ATTEMPTS,
    E2E_HARD_FIX, E2E_PROGRESS_BONUS, E2E_RETRY_BLOCKED,
    E2E_FINAL_CLEAN_ROOM, E2E_FINAL_REPAIR_ATTEMPTS, E2E_FINAL_RESET_DB,
)


def _report_unparseable(arch) -> list:
    """Generated files with a bracket left open, named out loud."""
    from qa_agent.author import UnitTestAuthor as _A

    broken = []
    for rel, body in sorted(arch.files.items()):
        if not rel.endswith((".js", ".jsx")) or rel.startswith("tests/"):
            continue
        try:
            said = _A._unbalanced(_A, body)
        except Exception as e:
            log.debug(f"bracket scan {rel}: {e}")
            continue
        if said:
            broken.append((rel, said))
    if broken:
        elog("WARN", f"   ⚠ {len(broken)} generated file(s) do not parse. "
                     f"Nothing imports them yet, so the build compiles anyway "
                     f"— they are still broken:")
        for rel, said in broken[:6]:
            elog("WARN", f"      ✗ {rel} — {said}")
    return broken


def _remeasure_unit(proj_dir: Path, qa, unit: dict, arch=None) -> dict:
    """Run the unit suite once more, on the bytes that are actually shipping."""
    unit = dict(unit or {})
    if not unit.get("ran") or not qa or not getattr(qa, "enabled", False):
        return unit
    try:
        runner = VitestRunner(proj_dir, cmd=qa.cmd, callbacks=_qa_callbacks(),
                              session=qa)
        passed, failures, ok = runner.run()
    except Exception as e:
        elog("WARN", f"   ⚠ could not re-check the unit tests at the end: {e}")
        log.exception("final unit re-measure")
        unit["final_check"] = "did not run"
        return unit
    if not ok:
        unit["final_check"] = "did not run"
        return unit

    was_p, was_f = unit.get("passed", 0), unit.get("failed", 0)

    skipped = runner.skipped()
    quarantine_dir = proj_dir / "tests" / "quarantine"
    quarantined = sorted(
        str(fp.relative_to(proj_dir)).replace("\\", "/")
        for fp in quarantine_dir.rglob("*.test.*")
    ) if quarantine_dir.is_dir() else []
    old_unresolved = list(
        getattr(getattr(qa, "report", None), "unresolved", None) or [])
    failing_keys = {(f.test_file, f.name) for f in failures}
    unresolved, deleted_unresolved = [], []
    for u in old_unresolved:
        key = (str((u or {}).get("file") or ""),
               str((u or {}).get("case") or ""))
        if key in failing_keys:
            unresolved.append(u)
            continue
        fp = proj_dir / key[0] if key[0] else None
        try:
            body = fp.read_text(encoding="utf-8", errors="replace") if fp and fp.is_file() else ""
        except Exception:
            body = ""
        if not body or (key[1] and key[1] not in body):
            deleted_unresolved.append(u)

    unit.update(passed=passed, failed=len(failures),
                skipped=len(skipped), quarantined=len(quarantined),
                unresolved=len(unresolved),
                deleted_unresolved=len(deleted_unresolved),
                clean=not (failures or skipped or quarantined or unresolved
                           or deleted_unresolved),
                final_check="measured at the end")
    if qa.report is not None:
        qa.report.passed, qa.report.failures = passed, failures
        qa.report.unresolved = unresolved + deleted_unresolved

    if skipped:
        elog("WARN", f"   ❗ final unit check: {len(skipped)} it.skip case(s) "
                     "did not run")
    if quarantined:
        elog("WARN", f"   ❗ final unit check: {len(quarantined)} quarantined "
                     "test file(s) did not run")
    if unresolved:
        elog("WARN", f"   ❗ final unit check: {len(unresolved)} unresolved "
                     "unit case(s) remain")
    if deleted_unresolved:
        elog("WARN", f"   ❗ final unit check: {len(deleted_unresolved)} "
                     "previously-red case(s) disappeared instead of passing")

    if (passed, len(failures)) == (was_p, was_f):
        elog("INFO", f"   ✅ still {passed}/{passed + len(failures)} after every "
                     f"other stage")
        return unit
    if len(failures) > was_f:

        elog("WARN", f"   ⚠ the repairs after the unit stage broke "
                     f"{len(failures) - was_f} more case(s) — {passed} passing, "
                     f"{len(failures)} failing on the code that is shipping "
                     f"(the unit stage ended on {was_p}/{was_f})")
        for f in failures[:6]:
            elog("WARN", f"      ✗ {f.test_file} — {f.name}")
    else:
        elog("INFO", f"   ✅ {passed} passing, {len(failures)} failing on the "
                     f"code that is shipping (the unit stage ended on "
                     f"{was_p}/{was_f})")

    if failures:
        elog("WARN", "   🛑 final unit verification is read-only — these red "
                     "cases will NOT be rewritten to match later app repairs")
        unit["final_regression"] = True
    else:
        unit["final_regression"] = False
    return unit


MAX_AFTER_FIX = 2
