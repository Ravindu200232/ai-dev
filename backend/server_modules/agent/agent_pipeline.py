# Build updates and repair coordination.
def run_agent_pipeline(prompt: str, model: str, think: bool = None,
                       qa_model: str = "", resume_project: str = "",
                       logo: str = "", srs_id: str = "", theme: str = "",
                       theme_html: str = "", theme_page: str = "",
                       theme_dir: str = "", uploads: dict = None):
    """Raw prompt → LLM plan.md → LLM writes every file in one continuous pass."""
    cancel.begin()
    warn_if_agents_stale()
    set_tester_emit(emit)
    try:
        cloud = is_cloud_model(model)
        elog("INFO", "━" * 40)
        elog("INFO", f"🤖 Agent mode — {prompt[:80]}")
        elog("INFO", f"{'☁️  Cloud' if cloud else '💻 Local'}: {model}   "
                     f"ctx {max_context(model):,}"
                     + ("   🤔 thinking on" if think else
                        "   ⚡ thinking off" if think is False else ""))
        elog("INFO", "━" * 40)

        if resume_project:
            proj_dir = PROD_DIR / resume_project
            pname = proj_dir.name
            if not (proj_dir / ".agentforge" / "plan.json").is_file():
                intent = load_run_intent(proj_dir)
                if not intent.get("prompt"):
                    eerr(f"{resume_project} has nothing to resume from")
                    return
                elog("INFO", "   ↩ no plan yet — restarting from the original request")
                prompt = prompt or intent.get("prompt", "")
                model = model or intent.get("model", "")
                qa_model = qa_model or intent.get("qa_model", "")
                srs_id = srs_id or intent.get("srs_id", "")
                theme = theme or intent.get("theme", "")
                logo = logo or intent.get("logo", "")
                if think is None:
                    think = intent.get("think")
                resume_project = ""
        else:
            proj_dir = _project_dir_for(
                _project_slug(_srs_app_name(srs_id), prompt[:40]), "next")
            pname = proj_dir.name

        proj_dir.mkdir(parents=True, exist_ok=True)
        save_run_intent(proj_dir, prompt=prompt, model=model, think=think,
                        qa_model=qa_model, srs_id=srs_id, theme=theme,
                        theme_page=theme_page, logo=logo)
        elog("INFO", f"   📁 {proj_dir}")
        # From here a cancel has something to undo.
        cancel.note(project=pname, srs_id=srs_id)
        eproject(pname)

        eprog("Checking model…", 2)
        if not ensure_model(model):
            eerr(f"Cannot load model: {model}")
            return

        logo_ready = False
        if logo:
            try:
                src = Path(logo)
                if src.is_file():
                    dest = proj_dir / "public" / "logo.png"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(src.read_bytes())
                    logo_ready = True
                    elog("INFO", "   🖼 Using the logo you approved")
                else:
                    elog("WARN", f"   ⚠ The approved logo is gone: {logo}")
            except OSError as e:
                elog("WARN", f"   ⚠ Could not copy the logo: {e}")

        # Pictures the person brought themselves. They land where a drawn one
        # would, so nothing downstream has to know the difference — and the
        # generator skips any key that is already on disk, which is exactly
        # what "use mine instead of drawing one" has to mean.
        adopt_uploaded_images(uploads, proj_dir)

        if srs_id:
            adopt_srs(srs_id, proj_dir)

        if theme_dir:
            if theme and not theme_html:
                theme_html = staged_design_html(theme_dir, theme)
            if adopt_designs(theme_dir, proj_dir, theme):
                elog("INFO", "   🎨 the five designs are in "
                             ".agentforge/designs/ — the one you chose is "
                             "chosen.html")

        mongo_thread = threading.Thread(target=MONGO.ensure_running, daemon=True)
        mongo_thread.start()

        estep("plan", "active")

        cb = _agent_callbacks(proj_dir)

        drawing = {"thread": None}

        def _draw_early(path, content):
            estream_end(path, content)
            if path != "plan.md" or drawing["thread"]:
                return
            if not ((getattr(arch, "plan", None) or {}).get("images")):
                return
            t = threading.Thread(target=run_image_stage, args=(arch, proj_dir),
                                 daemon=True)
            drawing["thread"] = t
            t.start()

        cb["on_file_end"] = _draw_early

        qa_model = qa_model or model
        qa = QASession(proj_dir, callbacks=_qa_callbacks(), model=qa_model,
                       enabled=is_cloud_model(qa_model))
        if qa.enabled and qa_model != model:
            elog("INFO", f"   🧪 QA runs on {qa_model}")
        if not qa.enabled:

            elog("WARN", "   ⚠ QA is cloud-only — no unit tests, no "
                         "end-to-end flow, and no signed-in page sweep for "
                         f"{qa_model}. Pick a cloud QA model to get them.")
        cb["on_phase"] = qa.on_phase(cb["on_phase"])
        cb["on_file_written"] = qa.on_file_written(cb["on_file_written"])
        arch = ArchitectAgent(ollama, model, proj_dir, cb,
                              stack="next",
                              mongo_uri=MONGO.uri_for(pname),
                              db_name=db_name_for(pname),

                              dev_port=DEV_PORT,
                              think=think,
                              theme=theme, theme_html=theme_html,
                              theme_page=theme_page)
        qa.bind(arch)

        if resume_project:

            arch.load_existing()
            left = len(arch.unfinished())
            elog("INFO", f"⏭️  Resuming {pname} — {left} file(s) still missing")
            ok = arch.resume(brief=_srs_brief(proj_dir, model) if srs_id else "")
        else:

            requirement_brief = prompt
            if srs_id:
                requirement_brief = (_srs_name_line(proj_dir) + requirement_brief
                                     + _srs_brief(proj_dir, model))

            brief = requirement_brief
            if logo_ready:
                brief += ("\n\nThe app's logo already exists at `/logo.png` "
                          "(public/logo.png). Use it in the header and the "
                          "footer with a plain <img> and the app's name as its "
                          "alt text. Do not generate another logo, and do not "
                          "render the app name as text where the logo belongs.")
            brief += _image_brief_line(proj_dir)
            ok = arch.run(brief, requirement_source=requirement_brief)
        if not ok:
            estep("plan", "error")
            eerr("Agent failed to generate the project")
            qa.stop()
            return
        estep("plan", "done")
        estep("generate", "done")

        elog("INFO", f"   ✅ {len(arch.files)} files written "
                     f"({arch.tokens_out:,} tokens generated)")
        _report_unparseable(arch)

        def _draw_the_rest():
            """The plan's images, then every picture the finished code wants."""
            first = drawing.get("thread")
            if first is not None:
                first.join(timeout=900)
            try:
                _fill_missing_images(arch, proj_dir, "the pages")
            except Exception as e:
                elog("WARN", f"   ⚠ Image completion failed: {e}")
                log.exception("fill images")

        try:
            if drawing["thread"]:
                if drawing["thread"].is_alive():
                    elog("INFO", "   🖼 images continue in the background while code is built/tested")
            else:
                t = threading.Thread(target=run_image_stage, args=(arch, proj_dir),
                                     daemon=True)
                drawing["thread"] = t
                t.start()
            rest = threading.Thread(target=_draw_the_rest, daemon=True)
            drawing["rest"] = rest
            rest.start()
        except Exception as e:
            elog("WARN", f"   ⚠ Image stage could not continue in background: {e}")
            log.exception("image stage")

        analyzer = AnalyzerAgent(arch, proj_dir,
                                 base_url=f"http://localhost:{DEV_PORT}",
                                 callbacks=_analyzer_callbacks(),
                                 allow_reseed=True)

        qa.drain(timeout=180)
        _backfill_tests(arch, proj_dir, qa)

        qa_targets = FileSnapshot(proj_dir)
        qa_targets.capture(sorted({m.get("target") for m in qa.manifest.values()
                                   if m.get("target")}))
        try:
            analyzer.run(semantic=False)
        except Exception as e:
            elog("WARN", f"   ⚠ Analyzer failed: {e}")
            log.exception("analyzer (seam A)")
        qa.mark_stale(qa_targets.changed())

        mongo_thread.join(timeout=120)
        db_ok = MONGO.available
        if not db_ok:
            elog("WARN", "   ⚠ No database — the app is generated, but pages "
                         "that read data will error until MongoDB is available.")

        try:
            arch.sync_dependencies()
            arch.install_unresolved()
        except Exception as e:
            elog("WARN", f"   ⚠ dependency preflight could not finish: {e}")

        estep("install", "active")
        eprog("npm install…", 84)
        if not ensure_node_deps(proj_dir):
            estep("install", "error")
            eerr("Failed to install dependencies")
            return
        estep("install", "done")

        if db_ok:
            try:
                r = MONGO.reset_project_db(proj_dir, node_bin=NODE_BIN)
                if r.get("dropped"):
                    elog("INFO", f"   🧹 Cleared {r['db']} — {r['dropped']} "
                                 f"collection(s) left by a previous app")
            except Exception as e:
                log.warning(f"fresh-db reset skipped: {e}")

        build_ok = run_build_fix_loop(arch, proj_dir, db_ok)
        if not build_ok:
            estep("build", "error")

        pretest_targets = FileSnapshot(proj_dir)
        pretest_targets.capture(sorted({m.get("target") for m in qa.manifest.values()
                                        if m.get("target")}))
        flow_report, flow_clean, flow_conclusive, flow_written = (
            pretest_flow_convergence(
                arch, proj_dir, analyzer, build_ok=build_ok))
        if flow_written:
            qa.mark_stale(pretest_targets.changed())

        unit_out, e2e_out, runtime_errors = {}, {}, []
        runtime_report, api_report = AnalyzerReport(), AnalyzerReport()
        runtime_clean, api_clean = False, False
        try:
            unit = run_qa_unit_stage(arch, proj_dir, qa, build_ok=build_ok)
            unit_out = unit or {}
        except Exception as e:
            elog("WARN", f"   ⚠ Unit test stage failed: {e}")
            log.exception("qa unit stage")

        try:
            pending = [t for t in (drawing.get("thread"), drawing.get("rest"))
                       if t is not None and t.is_alive()]
            if pending:
                elog("INFO", "   🖼 waiting for the remaining background images before browser QA…")
            for t in (drawing.get("thread"), drawing.get("rest")):
                if t is not None:
                    t.join(timeout=900)
            # A top-up, not the main sweep: build repair can add a picture
            _fill_missing_images(arch, proj_dir, "the pages")
        except Exception as e:
            elog("WARN", f"   ⚠ Image completion failed: {e}")

        estep("serve", "active")
        eprog("Starting Next.js…", 90)
        start_next(proj_dir)
        wait_for_next()

        estep("test", "active")
        emit({"type": "test_start"})
        tester = TesterAgent(proj_dir, DEV_PORT, stack="next", smoke_only=True)

        runtime_deadline = time.time() + RUNTIME_DEADLINE
        prev_sig = None
        smoke_repaired = False

        for attempt in range(1, MAX_FIX + 2):
            emit({"type": "test_run", "attempt": attempt})

            mark = dev_log_mark()
            errors = tester.test()

            faults = terminal_faults(_filter_db_noise(dev_log_since(mark), db_ok))
            for f in faults:
                if not any(f[:60] in e for e in errors):
                    errors.append(f"Dev server error: {f}")
                    emit({"type": "test_result", "status": "fail",
                          "msg": "Dev server error", "detail": f[:160]})
                    elog("WARN", f"   ❌ terminal: {f[:110]}")

            if not db_ok:
                errors = [e for e in errors
                          if not any(m in e for m in _DB_ERROR_MARKERS)]

            if not errors:

                elog("INFO", "   🎉 Tests passed — no errors")
                estep("test", "done")
                break

            now_sig = frozenset(e.splitlines()[0][:120] for e in errors)
            if prev_sig is not None and now_sig >= prev_sig:
                elog("WARN", f"   ⚠ attempt {attempt - 1} changed none of the "
                             f"{len(now_sig)} error(s) — further attempts would "
                             f"repeat it")
                _report_unfixed(errors)
                estep("test", "done")
                break
            prev_sig = now_sig

            if time.time() > runtime_deadline:
                elog("WARN", f"   ⚠ the {RUNTIME_DEADLINE}s runtime-repair "
                             f"budget is spent with {len(errors)} error(s) left")
                _report_unfixed(errors)
                estep("test", "done")
                break

            if attempt > MAX_FIX:
                elog("WARN", f"   ⚠ Reached {MAX_FIX} fix attempts with "
                             f"{len(errors)} error(s) still unfixed")
                _report_unfixed(errors)
                estep("test", "done")
                break

            dev_errors = _filter_db_noise(dev_log_since(mark), db_ok)
            if not dev_errors.strip():
                dev_errors = _filter_db_noise(next_stderr(), db_ok)
            all_errors = "\n".join(errors[:8]) + "\n" + dev_errors

            if getattr(tester, "mcp_report", ""):
                all_errors = tester.mcp_report + "\n\n" + all_errors

            guidance = nextdocs.guidance_for(all_errors)
            if guidance:
                all_errors += "\n" + guidance

            emit({"type": "test_fixing", "attempt": attempt, "errors": errors[:5]})
            elog("INFO", f"   🔧 Agent fixing (attempt {attempt}/{MAX_FIX})…")
            ephase({"phase": -2, "title": f"Fixing errors (try {attempt})",
                    "status": "active"})

            _stop_dev_proc()

            runtime_focus = _exact_runtime_focus(arch, all_errors)
            if runtime_focus:
                elog("INFO", "   🎯 runtime stack named exact source: "
                             + ", ".join(runtime_focus[:3]))
            fixed_paths = _repair_runtime(arch, proj_dir, qa, analyzer, all_errors,
                                         dev_errors, attempt,
                                         focus_paths=runtime_focus or None,
                                         strict_scope=bool(runtime_focus),
                                         exact_scope=bool(runtime_focus))
            smoke_repaired = smoke_repaired or bool(fixed_paths)

            ephase({"phase": -2, "title": f"Fixing errors (try {attempt})",
                    "status": "done"})
            ensure_node_deps(proj_dir)
            if fixed_paths:
                arch.repair_missing_imports()
                problems, _ = check_syntax(
                    proj_dir, {p: arch.files.get(p, "") for p in fixed_paths})
                if problems:
                    elog("WARN", "   ⚠ repaired source has syntax errors; the next pass will focus on them")
            start_next(proj_dir)
            wait_for_next()

        if smoke_repaired:
            elog("INFO", "   🔨 runtime fixes settled — one production build")
            _stop_dev_proc()
            build_ok = bool(run_build_fix_loop(arch, proj_dir, db_ok, max_rounds=2))
            start_next(proj_dir)
            wait_for_next()

        runtime_errors = list(errors or [])
        try:
            runtime_report, runtime_clean, runtime_written = (
                run_runtime_verification_stage(
                    arch, proj_dir, analyzer, db_ok=db_ok, build_ok=build_ok))
        except Exception as e:
            runtime_clean = False
            runtime_written = 0
            runtime_report = AnalyzerReport()
            runtime_report.findings.append(Finding(
                "blocker", "RUNTIME_STAGE_FAILED",
                f"runtime verification stage failed: {e}"))
            elog("WARN", f"   ⚠ Runtime verification stage failed: {e}")
            log.exception("runtime verification stage")

        try:
            api_report, api_clean, api_written = run_api_verification_stage(
                arch, proj_dir, analyzer, db_ok=db_ok, build_ok=build_ok)
        except Exception as e:
            api_clean = False
            api_written = 0
            api_report = AnalyzerReport()
            api_report.findings.append(Finding(
                "blocker", "API_STAGE_FAILED",
                f"API verification stage failed: {e}"))
            elog("WARN", f"   ⚠ API verification stage failed: {e}")
            log.exception("api verification stage")

        e2e_blockers = _e2e_hard_upstream_blockers(runtime_report, api_report)

        e2e_out = {}
        if build_ok and not e2e_blockers:
            try:
                e2e_out = run_qa_e2e_stage(arch, proj_dir, qa, analyzer,
                                           build_ok=build_ok, db_ok=db_ok) or {}
            except Exception as e:
                elog("WARN", f"   ⚠ End-to-end stage failed: {e}")
                log.exception("qa e2e stage")
        else:
            reasons = []
            for stage, f in e2e_blockers[:6]:
                label = f"{stage}:{getattr(f, 'code', '')}"
                if label not in reasons:
                    reasons.append(label)
            if not build_ok:
                reasons.insert(0, "build:not-green")
            reason_text = ", ".join(reasons) or "upstream correctness gate"
            elog("WARN", "   ⛔ End-to-end journeys skipped — hard upstream "
                         "defects remain (" + reason_text + "). Fix the known "
                         "production fault before authoring browser journeys; "
                         "otherwise E2E only reports downstream symptoms.")
            e2e_out = {
                "ran": False, "flow": "", "passed": 0, "failed": 1,
                "fixed": 0, "unwritable": 0, "flows": [],
                "failures": [{"case": "upstream correctness gate",
                              "kind": "BLOCKED_UPSTREAM",
                              "message": reason_text}],
                "blocked_upstream": True,
            }

        if build_ok and int((e2e_out or {}).get("fixed") or 0) > 0:
            elog("INFO", "   🔁 confirming runtime/API health after E2E repairs")
            # Lighthouse needs the final bytes and nothing
            if build_ok:
                perf_box = {}

                def _measure_perf():
                    try:
                        perf_box["scores"] = run_perf_stage(proj_dir)
                    except Exception as e:
                        perf_box["error"] = e

                perf_thread = threading.Thread(target=_measure_perf, daemon=True)
                perf_thread.start()

            try:
                fresh_tester = TesterAgent(proj_dir, DEV_PORT, stack="next", smoke_only=True)
                smoke_mark = dev_log_mark()
                fresh_errors = list(fresh_tester.test() or [])
                for fault in terminal_faults(_filter_db_noise(dev_log_since(smoke_mark), db_ok)):
                    if not any(fault[:60] in err for err in fresh_errors):
                        fresh_errors.append(f"Dev server error: {fault}")
                runtime_errors = fresh_errors
            except Exception as e:
                runtime_errors = [f"post-E2E smoke verification failed: {e}"]

            try:
                runtime_report, runtime_clean, _ = run_runtime_verification_stage(
                    arch, proj_dir, analyzer, db_ok=db_ok, build_ok=build_ok,
                    allow_repair=False)
            except Exception as e:
                runtime_clean = False
                runtime_report = AnalyzerReport()
                runtime_report.findings.append(Finding(
                    "blocker", "RUNTIME_STAGE_FAILED",
                    f"post-E2E runtime verification failed: {e}"))

            try:
                api_report, api_clean, _ = run_api_verification_stage(
                    arch, proj_dir, analyzer, db_ok=db_ok, build_ok=build_ok,
                    allow_repair=False)
            except Exception as e:
                api_clean = False
                api_report = AnalyzerReport()
                api_report.findings.append(Finding(
                    "blocker", "API_STAGE_FAILED",
                    f"post-E2E API verification failed: {e}"))

        sec_findings, sec_audit, perf_scores = [], {}, {}
        sec_ran = False
        try:
            sec_findings, sec_audit = run_security_stage(arch, proj_dir, analyzer)
            sec_ran = True
        except Exception as e:
            elog("WARN", f"   ⚠ Security stage failed, so nothing is known "
                         f"about whether this app is safe: {e}")
            log.exception("security stage")

        if build_ok:
            red = []
            if not runtime_clean:
                red.append("runtime")
            if not api_clean:
                red.append("API")
            if (e2e_out or {}).get("failed"):
                red.append(f"{(e2e_out or {}).get('failed')} journey(s)")
            if not (e2e_out or {}).get("ran"):
                red.append("E2E did not run")
            if red:
                elog("INFO", f"   ⚡ measuring performance anyway — {', '.join(red)} "
                             f"still red; the numbers describe the page as served")
            try:
                thread = locals().get("perf_thread")
                if thread is not None:
                    thread.join(timeout=600)
                    if perf_box.get("error"):
                        raise perf_box["error"]
                    perf_scores = perf_box.get("scores") or {}
                else:
                    perf_scores = run_perf_stage(proj_dir)
            except Exception as e:
                elog("WARN", f"   ⚠ Performance stage failed: {e}")
                log.exception("perf stage")
        else:
            elog("INFO", "   ⚡ performance skipped — the build is not green, "
                         "so there is no app to measure")

        final_report = flow_report

        write_qa_report(proj_dir, qa, unit=unit_out, security=sec_findings,
                        audit=sec_audit, perf=perf_scores, e2e=e2e_out,
                        runtime=runtime_errors, security_ran=sec_ran,
                        flow_report=final_report, flow_clean=flow_clean,
                        flow_conclusive=flow_conclusive)

        security_clean = bool(sec_ran) and not any(
            getattr(f, "severity", "") in ("blocker", "major")
            for f in (sec_findings or []))
        e2e_clean = (bool((e2e_out or {}).get("ran"))
                     and not bool((e2e_out or {}).get("failed", 0))
                     and not bool((e2e_out or {}).get("unwritable", 0))
                     and (e2e_out or {}).get("build_after_fix", True) is not False
                     and not bool((e2e_out or {}).get("stale_after_late_repair")))
        unit_clean = bool((unit_out or {}).get("ran")) and bool(
            (unit_out or {}).get("clean", False))
        quality_clean = bool(build_ok and flow_clean and flow_conclusive
                             and runtime_clean and api_clean
                             and security_clean and e2e_clean and unit_clean
                             and not runtime_errors)
        emit({"type": "quality_summary",
              "clean": quality_clean, "build": bool(build_ok),
              "flow": bool(flow_clean), "flow_conclusive": bool(flow_conclusive),
              "runtime": bool(runtime_clean), "api": bool(api_clean),
              "security": bool(security_clean), "e2e": bool(e2e_clean),
              "unit": bool(unit_clean), "runtime_errors": len(runtime_errors)})

        _write_final_flow_receipt(
            proj_dir, final_report, clean=quality_clean,
            conclusive=bool(flow_conclusive and runtime_clean and api_clean),
            db_ok=db_ok, build_ok=build_ok)

        url = f"http://localhost:{DEV_PORT}"

        serving = False
        try:
            with socket.create_connection(("127.0.0.1", DEV_PORT), timeout=2):
                serving = True
        except OSError:

            serving = wait_for_next(20)
        if serving:
            estep("serve", "done" if quality_clean else "error")
            if quality_clean:
                eprog("Done — clean and connected", 100)
                elog("INFO", f"🎉 Clean app live at {url}")
            else:
                eprog("Built — unresolved quality gate", 100)
                elog("WARN", f"⚠ App is live at {url}, but it is NOT labelled "
                             "clean because the final quality gate did not pass. "
                             "See .agentforge/final-flow.json and the QA report.")
        else:
            estep("serve", "error")
            eprog("Built, but not serving", 100)
            elog("WARN", "   ⚠ The app was built and tested, but nothing is "
                         f"answering on port {DEV_PORT}. The files and the "
                         "test results are all on disk — use the reload button "
                         "on the preview to try serving it again.")
        edone(url, pname)

    except cancel.BuildCancelled:
        # Everything this run made goes with it.
        elog("WARN", "   ⏹ build cancelled — removing what it had made")
        detail = cancel.cleanup(PROD_DIR, delete_project)
        if detail.get("project_error"):
            elog("WARN", f"   ⚠ {detail['project']} did not delete: {detail['project_error']}")
        if detail.get("srs_error"):
            elog("WARN", f"   ⚠ the specification did not delete: {detail['srs_error']}")
        ecancel(detail)
    except Exception as e:
        eerr(f"Agent error: {e}")
        log.exception("Agent pipeline error")
    finally:
        try:
            qa.stop()
        except Exception:
            pass
        stop_model(model)
        cancel.finish()


def _open_for_edit(proj_name: str, model: str, think: bool = None):
    """Common preamble for every tool that edits an existing project."""
    t0 = time.time()
    proj_dir = PROD_DIR / proj_name
    if not proj_dir.exists():
        eerr(f"Project not found: {proj_name}")
        return None, None, None
    if not ensure_model(model):
        eerr(f"Cannot load model: {model}")
        return None, None, None
    stack = detect_stack(proj_dir)
    if stack == "next":
        MONGO.ensure_running()
    arch = ArchitectAgent(ollama, model, proj_dir, _agent_callbacks(proj_dir),
                          stack=stack,
                          mongo_uri=MONGO.uri_for(proj_name) if stack == "next" else "",
                          db_name=db_name_for(proj_name) if stack == "next" else "",
                          dev_port=DEV_PORT, think=think)
    arch.load_existing()
    elog("INFO", f"   ⏱ open {time.time() - t0:.1f}s")
    return proj_dir, arch, arch.stack


_FEATURE_TX_EXCLUDED_DIRS = {"node_modules", ".next", ".git"}
_FEATURE_SOAK_SECONDS = 8.0
_FEATURE_STABILIZE_ROUNDS = 3


def _feature_tx_paths(proj_dir: Path) -> set:
    """Files whose bytes belong to a feature transaction baseline."""
    root = Path(proj_dir)
    out = set()
    if not root.is_dir():
        return out
    for fp in root.rglob("*"):
        if not fp.is_file():
            continue
        try:
            rel = fp.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = rel.split("/")
        if any(part in _FEATURE_TX_EXCLUDED_DIRS for part in parts):
            continue
        if parts and parts[0] == ".agentforge" and rel != ".agentforge/convo.json":
            continue
        out.add(rel)
    return out


def _capture_feature_transaction(arch, proj_dir: Path) -> dict:
    paths = _feature_tx_paths(proj_dir)
    snap = FileSnapshot(proj_dir)
    snap.capture(paths)
    return {
        "snapshot": snap,
        "paths": paths,
        "files": dict(getattr(arch, "files", {}) or {}),
        "plan_md": getattr(arch, "plan_md", ""),
        "convo": copy.deepcopy(getattr(arch, "convo", []) or []),
    }


def _restore_feature_transaction(arch, proj_dir: Path, tx: dict) -> list:
    """Restore the app to exactly the state before the feature started."""
    snap = tx.get("snapshot")
    restored = snap.restore() if snap is not None else []
    before_paths = set(tx.get("paths") or ())
    for rel in sorted(_feature_tx_paths(proj_dir) - before_paths, reverse=True):
        fp = Path(proj_dir) / rel
        try:
            if fp.is_file():
                fp.unlink()
                restored.append(rel)
        except OSError as e:
            log.warning(f"feature rollback: cannot remove {rel}: {e}")
    arch.files = dict(tx.get("files") or {})
    arch.plan_md = tx.get("plan_md", "")
    arch.convo = copy.deepcopy(tx.get("convo") or [])
    return sorted(set(restored))


def _feature_finding_key(f) -> tuple:
    text = str(getattr(f, "message", "") or "").lower()
    text = re.sub(r"\b[0-9a-f]{24}\b", "<id>", text)
    text = re.sub(r"\b\d+\b", "#", text)
    text = re.sub(r"\s+", " ", text).strip()[:260]
    return (str(getattr(f, "code", "") or ""),
            str(getattr(f, "path", "") or "").replace("\\", "/"), text)


def _feature_baseline_keys(report) -> set:
    return {_feature_finding_key(f) for f in _serious_findings(report)}


def _feature_changed_paths(before_files: dict, arch) -> set:
    now = dict(getattr(arch, "files", {}) or {})
    keys = set(before_files) | set(now)
    return {p for p in keys if before_files.get(p) != now.get(p)}


def _feature_related_findings(report, baseline_keys: set, changed_paths: set) -> list:
    """Serious findings introduced by this feature, not pre-existing debt."""
    out = []
    shared_changed = (
        any(p in changed_paths for p in ("middleware.js", "middleware.ts",
                                        "app/layout.js", "app/layout.jsx",
                                        "app/layout.ts", "app/layout.tsx"))
        or any(p.startswith("app/layout") for p in changed_paths)
    )
    for f in _serious_findings(report):
        key = _feature_finding_key(f)
        if key in baseline_keys:
            continue
        code = str(getattr(f, "code", "") or "")
        path = str(getattr(f, "path", "") or "").replace("\\", "/")
        msg = str(getattr(f, "message", "") or "")
        if code in {"RUNTIME_EXCEPTION", "ROUTE_ERROR", "BAD_CREDENTIALS"}:
            mentions_changed = any(p and p in msg for p in changed_paths)
            if path not in changed_paths and not mentions_changed and not shared_changed:
                continue
        out.append(f)
    return out


def _feature_soak_seconds() -> float:
    raw = os.environ.get("AGENTFORGE_FEATURE_SOAK_SECONDS", "").strip()
    try:
        return max(1.0, min(float(raw), 30.0)) if raw else _FEATURE_SOAK_SECONDS
    except ValueError:
        return _FEATURE_SOAK_SECONDS


def _observe_feature_upgrade(arch, proj_dir: Path, analyzer, *, db_ok: bool,
                             changed_paths=None, declared_routes=None, route_hint=""):
    """Open only the feature-owned routes and watch their Next/browser output."""
    if not _dev_alive():
        start_next(proj_dir)
        wait_for_next()

    try:
        report = analyzer.scan()
    except Exception as e:
        elog("WARN", f"   ⚠ post-feature static scan failed: {e}")
        report = AnalyzerReport()

    pages, apis = _feature_focus_scope(
        arch, changed_paths or [], declared_routes=declared_routes, route_hint=route_hint)
    page_msg = ", ".join(pages) if pages else "none"
    api_msg = ", ".join(apis) if apis else "none"
    elog("INFO", f"   🎯 Feature check scope — pages: {page_msg}; APIs: {api_msg}")

    mark = dev_log_mark()
    for route in pages:
        seen = _reproduce_complaint(proj_dir, route, "", analyzer)
        src = _runtime_route_source(route, arch, analyzer)
        for err in list(getattr(seen, "page_errors", []) or []) + list(getattr(seen, "console", []) or []):
            if any(sig in str(err) for sig in _TERMINAL_SIGNALS):
                report.findings.append(Finding(
                    "blocker", "RUNTIME_EXCEPTION",
                    f"post-feature browser exception on {route}: {str(err)[:500]}",
                    path=src, fix=f"repair {src or route} and re-open {route}"))
        for net in list(getattr(seen, "network", []) or []):
            m = re.match(r"HTTP\s+(\d{3})\s+(\w+)\s+(\S+)", str(net))
            if not m:
                continue
            status = int(m.group(1))
            url = m.group(3)
            if status == 404 or status >= 500:
                report.findings.append(Finding(
                    "blocker", "ROUTE_ERROR",
                    f"post-feature request failed on {route}: HTTP {status} {m.group(2)} {url}",
                    path=src, fix=f"repair the feature flow serving {route} and re-probe the failed request"))

    route_meta = getattr(report, "routes", None) or {}
    for api in apis:
        meta = route_meta.get(api) or {}
        methods = set(meta.get("methods") or [])
        if methods and "GET" not in methods:
            continue
        try:
            status = analyzer._get_status(analyzer.base_url + api)
        except Exception:
            status = None
        if status is None or status == 404 or status >= 500:
            report.findings.append(Finding(
                "blocker", "ROUTE_ERROR", f"feature API {api} returns HTTP {status}",
                path=meta.get("file", ""), fix=f"repair {api} and re-probe only this API"))

    seconds = _feature_soak_seconds()
    elog("INFO", f"   👀 Watching these feature routes for {seconds:.0f}s")
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(min(0.5, max(0.05, deadline - time.time())))
    trace = _filter_db_noise(dev_log_since(mark, limit=220), db_ok)
    report.findings.extend(_runtime_fault_findings(trace, arch, analyzer))
    return report, trace

def _stabilize_feature_upgrade(arch, proj_dir: Path, analyzer, *,
                               baseline_keys: set, before_files: dict,
                               db_ok: bool, declared_routes=None, route_hint="") -> tuple:
    """Observe → repair → rebuild → observe until the feature is quiet."""
    last_sig = None
    total_written = 0
    last_report = AnalyzerReport()
    for rnd in range(1, _FEATURE_STABILIZE_ROUNDS + 1):
        elog("INFO", f"   🩺 Post-feature stabilization {rnd}/{_FEATURE_STABILIZE_ROUNDS}")
        changed = _feature_changed_paths(before_files, arch)
        focus_pages, _focus_apis = _feature_focus_scope(
            arch, changed, declared_routes=declared_routes, route_hint=route_hint)
        related_paths = set(changed)
        for route in focus_pages:
            src = _runtime_route_source(route, arch, analyzer)
            if src:
                related_paths.add(src)
        report, _trace = _observe_feature_upgrade(
            arch, proj_dir, analyzer, db_ok=db_ok, changed_paths=changed,
            declared_routes=declared_routes, route_hint=route_hint)
        last_report = report
        serious = _feature_related_findings(report, baseline_keys, related_paths)
        if not serious:
            elog("INFO", "   ✅ Feature stayed clean during the live Next.js watch")
            return True, total_written, report

        sig = tuple(sorted(_feature_finding_key(f) for f in serious))
        elog("WARN", f"   ⚠ {len(serious)} new feature regression(s) appeared live")
        for f in serious[:5]:
            elog("WARN", f"      [{getattr(f,'code','')}] {getattr(f,'message','')[:140]}")
        if sig == last_sig:
            elog("WARN", "   ↔ the same feature regression survived the last repair")
            break
        last_sig = sig

        fix = AnalyzerReport()
        fix.findings = serious
        fix.missing = list(getattr(report, "missing", None) or [])
        written = repair_findings(arch, proj_dir, analyzer, fix, db_ok,
                                  restart_dev=True)
        total_written += written
        if not written:
            elog("WARN", "   ↔ no evidence-backed repair was possible")
            break
    return False, total_written, last_report
