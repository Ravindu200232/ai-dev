# The forge pipeline, wired to this server's websocket vocabulary.
from server_modules.forge.bridge import PlanGate, ui_relay

# One gate per running project, so a plan review can be answered over the
# same socket that reported it.
FORGE_GATES = {}
FORGE_PLAN_TIMEOUT = 900


def forge_decide(project: str, verdict: str, note: str = "") -> bool:
    """Answer a run's plan review. False when no run is waiting on one."""
    gate = FORGE_GATES.get(str(project or "").strip())
    if gate is None:
        return False
    if verdict == "approve":
        return gate.approve()
    if verdict == "revise":
        return gate.revise(note)
    return gate.reject()


def _title_for(prompt: str, name: str) -> str:
    """A display name for the scaffold — the request if it reads like one."""
    words = [w for w in str(prompt or "").split() if w.isalnum()][:4]
    return " ".join(words).title() or name.replace("-", " ").title()


def run_forge_pipeline(prompt: str, model: str = "", qa_model: str = "",
                       project: str = "", kinds=("unit", "e2e")):
    """Scaffold → plan → approval → build → unit → e2e, on the forge core."""
    from forge import Pipeline
    from forge.llm import Model

    cancel.begin()
    name = project or ""
    try:
        model = model or default_builder_model()
        proj_dir = (PROD_DIR / project if project else
                    _project_dir_for(_project_slug(prompt[:40]), "next"))
        proj_dir.mkdir(parents=True, exist_ok=True)
        name = proj_dir.name
        cancel.note(project=name)
        eproject(name)

        elog("INFO", "━" * 40)
        elog("INFO", f"⚒️  Forge — {prompt[:80]}")
        elog("INFO", f"   🔨 Builder: {model}")
        elog("INFO", f"   🧪 QA: {qa_model or model}")
        elog("INFO", f"   📁 {proj_dir}")
        elog("INFO", "━" * 40)

        gate = PlanGate(emit, timeout=FORGE_PLAN_TIMEOUT)
        FORGE_GATES[name] = gate
        relay = ui_relay(emit)

        pipeline = Pipeline(
            proj_dir, Model(model), on_event=relay,
            qa_model=Model(qa_model) if qa_model else None,
            should_stop=cancel.cancelled)

        result = pipeline.run(prompt, name=name, title=_title_for(prompt, name),
                              approve=gate.gate, kinds=tuple(kinds))
        relay.close("done" if result.green else "error")

        emit({"type": "forge_result", "project": name, "result": result.as_dict()})
        elog("INFO", f"   ⚒️  {result.summary()}")
        if result.stopped_at == "plan":
            elog("WARN", "   🧭 the plan was not approved — nothing was written")
        edone(f"http://127.0.0.1:{UI_PORT}", name)
    except cancel.BuildCancelled:
        ecancel({"project": name})
    except Exception as e:
        log.exception("forge pipeline failed")
        eerr(f"forge run failed: {type(e).__name__}: {e}")
    finally:
        FORGE_GATES.pop(name, None)
        cancel.finish()
