# Runtime QA repair helpers.


def _emit_qa_results(passed, failures, rnd):
    """One event per case, capped, so the counter in the UI is truthful."""
    if passed:
        emit({"type": "test_result", "status": "pass",
              "msg": f"{passed} unit test(s) passed",
              "detail": f"round {rnd}"})
    for f in failures[:40]:
        emit({"type": "test_result", "status": "fail",
              "msg": f"{f.name}",
              "detail": f"{f.test_file} — {f.message[:200]}"})


def _exact_runtime_focus(arch, text: str) -> list[str]:
    """Project source files named by a runtime stack, strongest signal first."""
    blob = str(text or "").replace("\\", "/")
    out = []
    for rel in (getattr(arch, "files", None) or {}):
        rel = str(rel or "").replace("\\", "/").lstrip("./")
        if not rel.startswith(("app/", "components/", "lib/")):
            continue
        if not re.search(re.escape(rel) + r"(?::\d+)(?::\d+)?", blob, re.I):
            continue
        if rel not in out:
            out.append(rel)
    return out[:6]


def _repair_runtime(arch, proj_dir: Path, qa, analyzer, all_errors: str,
                    dev_errors: str, attempt: int, model: str = None,
                    focus_paths=None, strict_scope: bool = False,
                    privileged_paths=None, exact_scope: bool = False) -> list:
    """Repair runtime/E2E failures through evidence-first diagnosis."""
    planner = FeaturesAgent(arch, proj_dir, callbacks=_analyzer_callbacks(),
                            analyzer=analyzer, model=model)
    fixer = BugFixerAgent(arch, proj_dir, callbacks=_qa_callbacks(), session=qa,
                          model=model)

    evidence = "\n".join([all_errors or "", dev_errors or ""])
    if focus_paths and exact_scope:
        focus = []
        for rel in focus_paths:
            rel = str(rel or "").strip().replace("\\", "/").lstrip("./")
            if rel and rel in arch.files and rel not in focus:
                focus.append(rel)
    else:
        focus = planner.repair_focus_paths(focus_paths, evidence) if focus_paths else []

    def run_fixer(spec):
        if not spec or spec.is_empty():
            return []
        elog("INFO", f"   🧭 {spec.summary or 'Repair planned'} "
                     f"— {len(spec.files)} file(s)")
        for pkg in spec.packages:
            elog("INFO", f"   📦 npm install {pkg}")
            try:
                arch.cmd.run(f"npm install {pkg}")
            except Exception as e:
                elog("WARN", f"   ⚠ npm install {pkg} failed: {e}")
        try:
            return fixer.fix_runtime(all_errors, spec, server_log=dev_errors,
                                     round_no=attempt,
                                     privileged_paths=privileged_paths)
        except Exception as e:
            elog("WARN", f"   ⚠ Runtime repair failed: {e}")
            log.exception("runtime repair: fix")
            return []

    spec = None
    if exact_scope:
        try:
            from agents.runtime_repair_spec import exact_runtime_repair_spec
            spec = exact_runtime_repair_spec(
                arch, evidence, focus_paths=focus or focus_paths)
            if spec and not spec.is_empty():
                elog("INFO", "   🎯 exact runtime frame + real source prove a "
                             "minimal repair — sending that source directly to the fixer")
        except Exception as e:
            log.debug(f"exact runtime repair spec: {e}")
            spec = None

    if not spec or spec.is_empty():
        try:
            spec = planner.plan_repair(all_errors, server_log=dev_errors,
                                       focus_paths=focus or focus_paths)
        except Exception as e:
            from agents.ollama_client import is_transient
            elog("WARN", f"   ⚠ Repair planning failed: {e}"
                         + (" — the model was busy, not out of ideas"
                            if is_transient(e) else ""))
            log.exception("runtime repair: plan")

    if spec and not spec.is_empty():
        ctx = getattr(spec, "context", {}) or {}
        elog("INFO", f"   🧠 repair root cause — {str(ctx.get('cause') or spec.summary)[:180]}")
        evidence_paths = [str(e.get('path') or '') for e in (ctx.get('evidence') or [])
                          if isinstance(e, dict) and e.get('path')]
        if evidence_paths:
            elog("INFO", "   🔎 repair evidence — " + ", ".join(evidence_paths[:8]))

    written = run_fixer(spec)
    if written:
        elog("INFO", f"   ✅ Repaired {len(written)} file(s): "
                     f"{', '.join(written)}")
        return written

    if strict_scope:
        # Scope stays: the repair may only touch the files the evidence
        # pointed at. What is gone is the refusal to try at all — the fix is
        # still checked by the parser and the production build, and rolled
        # back whole if either says no.
        elog("WARN", "   ⚠ the scoped repair produced no change; the evidence "
                     "named nothing this fixer is allowed to edit")
        return []

    if focus:
        elog("INFO", "   🔭 focused repair was inconclusive — broadening source analysis")
        try:
            broad = planner.plan_repair(all_errors, server_log=dev_errors,
                                        focus_paths=None)
        except Exception as e:
            elog("WARN", f"   ⚠ Broad repair analysis failed: {e}")
            broad = None
        written = run_fixer(broad)
        if written:
            elog("INFO", f"   ✅ Broad evidence repair wrote {len(written)} file(s): "
                         f"{', '.join(written)}")
            return written

    elog("WARN", "   ⚠ Runtime repair remains unresolved — root cause was not proven, so no code was guessed")
    return []
