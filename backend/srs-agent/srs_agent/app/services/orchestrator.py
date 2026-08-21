"""Orchestration service — ties the LangGraph workflows to persistence."""
from __future__ import annotations

import copy
import re

from ..agents import clarify, interview, plan_generator
from ..agents.coverage_auditor import compute_coverage
from ..agents.customer_context import customer_context
from ..extraction.brief import build_brief
from ..generators.builder_brief import attach_handoff
from ..graph.workflow import run_analysis, run_customization, run_generation
from ..knowledge import app_types
from ..knowledge.plan_srs import _auth_on
from ..models import repositories as repo
from ..schemas.project import now_iso
from ..schemas.srs import summarize_srs
from ..services import plan_approval, storage
from ..services.events import bus


def _title_from_idea(idea: str) -> str:
    t = re.sub(r"\s+", " ", (idea or "").strip())
    if not t:
        return "Untitled Project"
    first = re.split(r"[.!?\n]", t)[0]
    return (first[:60] + "…") if len(first) > 60 else first


def _bump_minor(version: str) -> str:
    parts = (version or "1.0.0").split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        parts[1] = str(int(parts[1]) + 1)
        parts[2] = "0"
    except ValueError:
        return "1.1.0"
    return ".".join(parts[:3])


async def create_project(idea: str, language: str | None = None) -> dict:
    pid = repo.new_id("prj_")
    project = {
        "id": pid, "title": _title_from_idea(idea), "raw_idea": idea,
        "detected_domain": "Custom", "domain_key": "custom", "status": "intake",
        "current_version": "0.0.0", "language": language or "English",
        "classification": None, "complexity": None, "suggested_stack": None,
        "coverage_score": 0.0, "needs_clarification": False, "clarification_reason": None,
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await repo.create_project(project)

    await bus.emit(pid, "System", "Project created.", channel="agent_events", level="success")
    return project


async def add_source(project_id: str, mode: str, text: str, filename: str | None = None,
                     meta: dict | None = None) -> dict:
    src = {"id": repo.new_id("src_"), "project_id": project_id, "mode": mode,
           "filename": filename, "text": text or "", "meta": meta or {}, "created_at": now_iso()}
    await repo.add_source(src)
    await bus.emit(project_id, "IntakeExtractorAgent",
                   f"Added {mode} source" + (f" ({filename})" if filename else "") +
                   f" — {len(text or '')} chars extracted.", channel="agent_events")
    return src


async def _brief_for(project: dict) -> str:
    sources = await repo.list_sources(project["id"])
    return build_brief(project.get("raw_idea", ""), sources)


def _fallback_analysis(state: dict) -> dict:
    """Pure-fixed analysis (no LLM, no graph) used as the last-resort net."""
    from ..agents.domain_classifier import _complexity, _stack
    from ..agents.intake import _content_only, detect_language, looks_like_nonsense
    from ..knowledge.domains import classify_domain, get_domain

    brief = state.get("brief", "")
    nonsense, reason = looks_like_nonsense(_content_only(brief))
    key, conf, label = classify_domain(brief)
    dom = get_domain(key)
    classification = {
        "domain_key": key, "detected_domain": label, "app_type": dom["app_type_primary"],
        "confidence": conf, "similar_patterns": 2 if key != "custom" else 0,
        "reasoning": "Deterministic keyword classification (LLM path unavailable).",
    }
    return {
        **state, "classification": classification, "questions": [],
        "needs_clarification": nonsense, "clarification_reason": reason,
        "language": detect_language(brief),
        "project": {**state.get("project", {}),
                    "complexity": _complexity(key, brief), "suggested_stack": _stack(key)},
    }


async def analyze(project_id: str) -> dict:
    project = await repo.get_project(project_id)
    if not project:
        raise KeyError("project not found")
    await repo.update_project(project_id, {"status": "analyzing"})
    brief = await _brief_for(project)

    state = {
        "project_id": project_id, "project": project, "raw_idea": project.get("raw_idea", ""),
        "brief": brief, "language": project.get("language", "English"),
    }

    try:
        result = await run_analysis(state)
        if not result.get("classification"):
            raise RuntimeError("analysis produced no classification")
    except Exception as exc:  # noqa: BLE001
        await bus.error(project_id, "AnalyzerWorkflow",
                        f"Analysis failed ({exc}); using deterministic fallback.")
        result = _fallback_analysis(state)

    classification = result.get("classification") or {}
    needs_clar = bool(result.get("needs_clarification"))
    rproject = result.get("project", project)

    update = {
        "classification": classification or None,
        "detected_domain": classification.get("detected_domain", project.get("detected_domain", "Custom")),
        "domain_key": classification.get("domain_key", project.get("domain_key", "custom")),
        "complexity": rproject.get("complexity"),
        "suggested_stack": rproject.get("suggested_stack"),
        "language": result.get("language", project.get("language")),
        "needs_clarification": needs_clar,
        "clarification_reason": result.get("clarification_reason"),
        "status": "questioning",
    }
    await repo.update_project(project_id, update)

    session = _new_interview_session(project_id, brief, classification)
    question = await interview.ask(session)
    _remember(session, question)
    await repo.save_question_session(session)

    project = await repo.get_project(project_id)
    return {"project": project, "classification": classification,
            "needs_clarification": needs_clar, "clarification_reason": update["clarification_reason"],
            "question": question, "questions": session["questions"]}


def _new_interview_session(project_id: str, brief: str,
                           classification: dict | None = None) -> dict:

    guessed, confidence = app_types.guess_app_type(brief)
    why = ""
    if classification:
        picked = str(classification.get("build_category") or "").strip().lower()
        if picked in app_types.APP_TYPES:
            guessed = picked
            confidence = float(classification.get("build_category_confidence")
                               or confidence or 0.0)
            why = str(classification.get("build_category_why") or "")
    return {
        "id": repo.new_id("qs_"), "project_id": project_id, "mode": "interview",
        "raw_idea": brief, "guessed_app_type": guessed,

        "guessed_app_type_confidence": round(float(confidence or 0.0), 2),
        "guessed_app_type_why": why,
        "app_type": None, "pack": None,
        "answers": {}, "asked": [], "clarifications": {},
        "questions": [], "current": None,
        "total": 0, "coverage_score": 0.0, "complete": False,
        "created_at": now_iso(),
    }


def _remember(session: dict, question: dict | None) -> dict | None:
    """Park the question on the session as both 'what to show' and transcript."""
    session["current"] = question
    if not question:
        return None
    transcript = session.setdefault("questions", [])
    if not any(q.get("id") == question.get("id") for q in transcript):
        transcript.append(question)
    session["total"] = question.get("total", len(transcript))
    return question


def _confirmed_typed(session: dict) -> list[str]:
    """Typed answers already read back and confirmed — the duplicate check."""
    out = []
    for st in (session.get("clarifications") or {}).values():
        resolved = clarify.resolve(st)
        if resolved and resolved["text"]:
            out.append(resolved["text"])
    return out


def _apply_clarification(session: dict, st: dict) -> None:
    """Fold a confirmed reading back into the answer it came from."""
    resolved = clarify.resolve(st)
    if not resolved:
        return
    entry = (session.get("answers") or {}).get(st["question_id"])
    if not entry:
        return
    raw, text = resolved["raw"], resolved["text"]

    answer = resolved.get("answer") or text
    entry["raw_text"] = raw
    entry["purpose"] = resolved.get("purpose", "")
    if text and text != raw:
        entry["custom_text"] = text
        value = entry.get("value")
        if isinstance(value, list):
            entry["value"] = [answer if str(v) == raw else v for v in value]
        elif str(value) == raw:
            entry["value"] = answer
        if entry.get("text") == raw:
            entry["text"] = answer


async def _sources_by_id(project_id: str, ids: list[str] | None) -> list[dict]:
    """Resolve uploaded source ids, keeping the order the customer attached in."""
    wanted = [str(i) for i in (ids or []) if str(i).strip()]
    if not wanted:
        return []
    by_id = {s["id"]: s for s in await repo.list_sources(project_id) if s.get("id")}
    return [by_id[i] for i in wanted if i in by_id]


async def _next_question(session: dict) -> dict | None:
    pending = clarify.pending(session)
    if pending:
        return _remember(session, clarify.question(pending))
    return _remember(session, await interview.ask(session))


async def interview_state(project_id: str) -> dict:
    """The question on screen right now, plus everything answered so far."""
    session = await repo.get_question_session(project_id)
    if not session:
        return {"question": None, "transcript": [], "done": False, "app_type": None}

    question = session.get("current")
    if question is None and not session.get("complete"):
        question = await _next_question(session)
        await repo.save_question_session(session)

    return {
        "question": question,
        "transcript": session.get("questions", []),
        "answers": interview.flat_answers(session),
        "done": bool(session.get("complete")) or question is None,
        "app_type": session.get("app_type"),
    }


async def interview_answer(project_id: str, key: str, value=None, text: str = "",
                           selected=None, custom: str = "",
                           attachments: list[str] | None = None) -> dict:
    """Record one answer and return whatever should be asked next."""
    project = await repo.get_project(project_id)
    if not project:
        raise KeyError("project not found")
    session = await repo.get_question_session(project_id)
    if not session:
        raise KeyError("no interview in progress")

    attached = await _sources_by_id(project_id, attachments)

    words = customer_context(session=session, project=project)

    pending = clarify.pending(session)
    if pending:

        await clarify.answer(pending, value=value, text=text,
                             existing=_confirmed_typed(session),
                             project_id=project_id, context=words)
        session["clarifications"][pending["question_id"]] = pending
        if clarify.is_ready(pending):
            _apply_clarification(session, pending)
    else:
        interview.record(session, key, value=value, text=text,
                         selected=selected, custom=custom, attachments=attached)
        typed = (custom or text or "").strip()
        question = session.get("current") or {}
        if clarify.needs_clarification(typed, question.get("options")):
            st = await clarify.start(
                key, typed, topic=key.split(":", 1)[0],
                question=question.get("question", ""),
                existing=_confirmed_typed(session),
                options=question.get("options"),
                project_id=project_id, context=words,
            )
            session.setdefault("clarifications", {})[key] = st
            if clarify.is_ready(st):
                _apply_clarification(session, st)

    await repo.save_answers(project_id, interview.flat_answers(session))
    question = await _next_question(session)

    if question is None:
        session["complete"] = True
        brief = await _brief_for(project)
        cov = compute_coverage(brief, session.get("questions", []),
                               interview.flat_answers(session))
        session["coverage_score"] = cov["score"]

        session["coverage"] = cov
        await repo.save_question_session(session)
        await repo.update_project(project_id, {"coverage_score": cov["score"],
                                               "coverage": cov,
                                               "status": "planning"})
        await bus.emit(project_id, "InterviewAgent",
                       f"That is everything we need — coverage {cov['score']}%.",
                       channel="agent_events", level="success", data={"coverage": cov})
        return {"question": None, "done": True, "coverage_score": cov["score"]}

    await repo.save_question_session(session)
    return {"question": question, "done": False}


async def get_questions(project_id: str) -> dict:
    """The transcript so far, in the shape the rest of the API speaks."""
    session = await repo.get_question_session(project_id)
    if not session:
        return {"session": None, "questions": []}
    return {"session": session, "questions": session.get("questions", [])}


async def generate_plan(project_id: str, revision: str = "") -> dict:
    """Build plan v1, or a new version answering a change request."""
    project = await repo.get_project(project_id)
    if not project:
        raise KeyError("project not found")
    session = await repo.get_question_session(project_id) or {}
    brief = await _brief_for(project)

    latest = await repo.latest_plan(project_id)
    previous = (latest or {}).get("plan") if revision else None
    version = int((latest or {}).get("version", 0)) + 1

    coverage = session.get("coverage")
    if not coverage:
        try:
            coverage = compute_coverage(brief, session.get("questions", []),
                                        interview.flat_answers(session))
        except Exception:  # noqa: BLE001 — a missing audit must not stop the plan
            coverage = None

    plan = await plan_generator.generate_plan(
        project=project, session=session, brief=brief,
        previous=previous, revision=revision, coverage=coverage,
    )
    # Keep an approved rename in the interview state.
    asked = str((session.get("answers", {}).get("app_name", {}) or {}).get("value")
                or "").strip()
    named = str(plan.get("app_name") or "").strip()
    app_name = named or asked or str(project.get("title", ""))

    if named and named != asked:
        answers = dict(session.get("answers") or {})
        entry = dict(answers.get("app_name") or {})
        entry["value"] = named
        answers["app_name"] = entry
        session["answers"] = answers
        await repo.save_question_session(session)
        await repo.update_project(project_id, {"title": named})

    markdown = plan_generator.render_plan_markdown(plan, app_name=app_name)

    doc = {
        "id": repo.new_id("pln_"), "project_id": project_id, "version": version,
        "plan": plan, "markdown": markdown,
        "content_hash": plan_approval.content_hash(plan),
        "revision_of": (latest or {}).get("version") if revision else None,
        "change_request": revision,
        "approved": False, "approved_at": None,
        "created_at": now_iso(),
    }
    await repo.save_plan(doc)
    await repo.update_project(project_id, {"status": "planning"})
    return await plan_approval.state(project_id)


async def plan_state(project_id: str) -> dict:
    return await plan_approval.state(project_id)


async def approve_plan(project_id: str, version: int | None = None) -> dict:
    verdict = await plan_approval.approve(project_id, version)
    if verdict.get("approved"):
        await bus.emit(project_id, "PlanGeneratorAgent",
                       f"Plan v{verdict['version']} approved — this is now the "
                       "record of what gets specified.",
                       channel="agent_events", level="success")
    return verdict


async def generate_srs(project_id: str) -> dict:
    project = await repo.get_project(project_id)
    if not project:
        raise KeyError("project not found")
    brief = await _brief_for(project)
    session = await repo.get_question_session(project_id) or {"questions": []}
    answers = await repo.get_answers(project_id)

    plan_doc = await plan_approval.approved_plan(project_id) or await repo.latest_plan(project_id)

    state = {
        "project_id": project_id, "project": project, "raw_idea": project.get("raw_idea", ""),
        "brief": brief, "language": project.get("language", "English"),
        "classification": project.get("classification") or {},
        "questions": session.get("questions", []), "answers": answers, "session": session,

        "plan": (plan_doc or {}).get("plan"),
        "plan_markdown": (plan_doc or {}).get("markdown", ""),
    }
    result = await run_generation(state)
    srs = result["srs"]

    existing = await repo.latest_version(project_id)
    version = _bump_minor(existing["version"]) if existing else "1.0.0"
    srs["srs_document"]["version"] = version

    storage.save_srs_json(project_id, srs, version)

    snapshot = copy.deepcopy(srs)
    snapshot["srs_document"]["diagrams"] = storage.snapshot_diagrams(
        project_id, version, srs["srs_document"].get("diagrams", []))
    await repo.save_version({"id": repo.new_id("ver_"), "project_id": project_id, "version": version,
                             "label": "Initial generation" if not existing
                                      else "Rewritten from a changed plan",
                             "srs": snapshot, "diff_summary": [],
                             "created_at": now_iso()})
    await repo.save_diagrams(project_id, srs["srs_document"].get("diagrams", []))
    await repo.update_project(project_id, {"status": "generated", "current_version": version})

    summary = summarize_srs(srs)
    project = await repo.get_project(project_id)
    return {"project": project, "version": version, "summary": summary, "srs": srs}


async def customize(project_id: str, prompt: str) -> dict:
    project = await repo.get_project(project_id)
    if not project:
        raise KeyError("project not found")
    latest = await repo.latest_version(project_id)
    if not latest:
        raise ValueError("no SRS to customize yet; generate first")

    state = {"project_id": project_id, "project": project, "srs": latest["srs"],
             "brief": await _brief_for(project),
             "session": await repo.get_question_session(project_id) or {},
             "customization_prompt": prompt}
    result = await run_customization(state)
    srs = result["srs"]
    diff = result.get("diff_summary", [])
    version = _bump_minor(latest["version"])
    srs["srs_document"]["version"] = version

    plan_doc = await plan_approval.approved_plan(project_id) or await repo.latest_plan(project_id)
    plan = (plan_doc or {}).get("plan") or {}
    if plan:
        pack = (await repo.get_question_session(project_id) or {}).get("pack") or {}
        attach_handoff(srs, plan, pack, auth=_auth_on(pack, plan))

    storage.save_srs_json(project_id, srs, version)

    snapshot = copy.deepcopy(srs)
    snapshot["srs_document"]["diagrams"] = storage.snapshot_diagrams(
        project_id, version, srs["srs_document"].get("diagrams", []))
    await repo.save_version({"id": repo.new_id("ver_"), "project_id": project_id, "version": version,
                             "label": f"Customized: {prompt[:60]}", "srs": snapshot, "diff_summary": diff,
                             "created_at": now_iso()})
    await repo.save_diagrams(project_id, srs["srs_document"].get("diagrams", []))
    await repo.update_project(project_id, {"status": "customized", "current_version": version})

    summary = summarize_srs(srs)
    project = await repo.get_project(project_id)
    return {"project": project, "version": version, "diff_summary": diff, "summary": summary, "srs": srs}


async def project_detail(project_id: str) -> dict:
    project = await repo.get_project(project_id)
    if not project:
        raise KeyError("project not found")
    latest = await repo.latest_version(project_id)
    versions = await repo.list_versions(project_id)
    srs = latest["srs"] if latest else None
    return {"project": project, "srs": srs,
            "summary": summarize_srs(srs) if srs else None,
            "versions": versions}


async def latest_srs(project_id: str) -> dict | None:
    latest = await repo.latest_version(project_id)
    return latest["srs"] if latest else None
