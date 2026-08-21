# Main build entrypoint used by the UI.
def run_pipeline(prompt: str, refine_model: str, build_model: str):
    cancel.begin()
    set_stream_callback(on_token)
    set_tester_emit(emit)
    try:
        elog("INFO", "━" * 40)
        elog("INFO", f"💡 {prompt[:90]}")
        elog("INFO", f"🧠 Refine: {refine_model}   🏗️  Build: {build_model}")
        elog("INFO", "━" * 40)

        eprog("Checking refine model…", 3)
        if not ensure_model(refine_model):
            eerr(f"Cannot load refine model: {refine_model}"); return

        estep("refine", "active")
        eprog("Refining idea…", 8)
        elog("INFO", f"🧠 Agent 1 — {refine_model}")

        refiner = RefinerAgent(OLLAMA_URL, refine_model)
        refined = refiner.refine(prompt)
        if not refined:
            eerr("Refiner failed — is Ollama running?"); return

        estep("refine", "done")
        try:
            s = json.loads(refined)
            edetect(s.get("site_type", "?"), s.get("strategy", "?"))
            elog("INFO", f"   type={s.get('site_type')}  strategy={s.get('strategy')}")
        except: pass

        stop_model(refine_model)

        eprog("Checking build model…", 18)
        if not ensure_model(build_model):
            eerr(f"Cannot load build model: {build_model}"); return

        spec = {}
        try: spec = json.loads(refined)
        except: pass

        raw_name = spec.get("project_name",
                   re.sub(r"[^a-z]", "", prompt[:15].lower()))
        pname = re.sub(r"[^a-z0-9]", "", raw_name)[:20] or "project"

        proj_dir = _project_dir_for(pname, "vite")
        pname = proj_dir.name
        proj_dir.mkdir(parents=True, exist_ok=True)
        elog("INFO", f"   📁 {proj_dir}")
        # From here a cancel has something to undo.
        cancel.note(project=pname)
        eproject(pname)

        estep("build", "active")
        eprog("Generating components…", 22)
        elog("INFO", f"🏗️  Agent 2 — {build_model}")

        builder = UIBuilder(OLLAMA_URL, build_model, proj_dir)
        if not builder.build(refined):
            eerr("Build failed"); return

        estep("build", "done")
        eprog("Components ready", 55)

        stop_model(build_model)

        estep("serve", "active")
        eprog("Starting Vite…", 72)
        elog("INFO", f"🌐 Starting Vite on :{DEV_PORT}")
        if not ensure_node_deps(proj_dir):
            eerr("Failed to install dependencies"); return
        start_vite(proj_dir)
        wait_for_vite(35)

        estep("test", "active")
        eprog("Running tests…", 80)
        elog("INFO", "🧪 Agent 3 — Playwright")
        emit({"type": "test_start"})

        tester = TesterAgent(proj_dir, DEV_PORT)

        npm_errors = ""

        for attempt in range(1, MAX_FIX + 2):
            elog("INFO", f"   🔬 Test run #{attempt}")
            emit({"type": "test_run", "attempt": attempt})

            errors = tester.test()

            if not errors:
                elog("INFO", "   🎉 All tests passed!")
                estep("test", "done")
                break

            if attempt > MAX_FIX:
                elog("WARN", f"   ⚠ Max fix attempts ({MAX_FIX}) reached — writing guaranteed fallbacks")

                from agents.builder import _safe_component
                for fpath, src in list(builder.built_files.items()):
                    if not (fpath.startswith("src/components/") and fpath.endswith(".jsx")):
                        continue
                    comp_name = fpath.split("/")[-1].replace(".jsx", "")
                    fp = proj_dir / fpath

                    if len(src.strip()) < 400 or npm_errors.strip():
                        safe = _safe_component(comp_name)
                        fp.write_text(safe, encoding="utf-8")
                        builder.built_files[fpath] = safe
                        elog("WARN", f"   🛟 Safe fallback written → {fpath}")
                estep("test", "done")
                break

            npm_errors = builder._npm_build_errors()
            vs_errors  = vite_stderr()
            all_errors = "\n".join(errors) + "\n" + npm_errors + "\n" + vs_errors

            elog("INFO", f"   📋 npm build output:\n{npm_errors[:300] or '  (none)'}")

            emit({"type": "test_fixing", "attempt": attempt,
                  "errors": errors[:5]})
            elog("INFO", f"   🔧 Fixing (attempt {attempt}/{MAX_FIX})…")

            if not ensure_model(build_model):
                elog("WARN", "   Cannot load build model for fix — skipping")
                break

            builder.fix_with_errors(all_errors)
            stop_model(build_model)

            elog("INFO", "   🔄 Restarting Vite…")
            if not ensure_node_deps(proj_dir):
                eerr("Dependency install failed")
                return
            start_vite(proj_dir)
            wait_for_vite(35)

        url = f"http://localhost:{DEV_PORT}"
        estep("serve", "done")
        eprog("Done!", 100)
        elog("INFO", f"🎉 Live at {url}")
        edone(url, pname)

    except cancel.BuildCancelled:
        elog("WARN", "   ⏹ build cancelled — removing what it had made")
        detail = cancel.cleanup(PROD_DIR, delete_project)
        if detail.get("project_error"):
            elog("WARN", f"   ⚠ {detail['project']} did not delete: {detail['project_error']}")
        ecancel(detail)
    except Exception as e:
        eerr(f"Pipeline error: {e}")
        log.exception("Pipeline error")
    finally:
        set_stream_callback(None)
        cancel.finish()


AGENT_STEPS = ["plan", "scaffold", "generate", "install", "test", "serve"]
