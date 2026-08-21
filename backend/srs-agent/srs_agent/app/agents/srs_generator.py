"""SrsJsonGeneratorAgent — produce the SRS JSON."""
from __future__ import annotations

import re

from ..generators.builder_brief import attach_handoff
from ..knowledge.offline_srs import build_offline_srs, merge_pack
from ..knowledge.plan_srs import _auth_on, build_srs_from_plan, merge_pack_planned
from ..llm import LLMRepairFailed, LLMUnavailable, get_llm
from ..schemas.srs import summarize_srs, validate_srs
from ..generators.standards import apply_international_profile
from ..services.events import bus
from .state import AgentState

_SYS = (
    "You are a senior requirements engineer. Given a non-technical user's app "
    "idea and their answers, produce the DOMAIN-SPECIFIC content for a software "
    "requirements specification as STRICT JSON (no prose, no markdown). Tailor "
    "everything to THIS idea — real tables, real modules, real requirements. "
    "Return ONLY a JSON object with these keys:\n"
    "{\n"
    '  "system_category": str,\n'
    '  "app_summary": {"business_goal": str, "short_description": str, "target_users": [str]},\n'
    '  "roles": [{"role_key": snake_case, "role_name": str, "description": str}],\n'
    '  "main_modules": [str],\n'
    '  "tables": [{"table_name": snake_case, "description": str, "fields": ['
    '{"name": str, "type": "uuid|string|text|integer|decimal|boolean|date|datetime|enum|json|foreign_key", '
    '"primary_key": bool, "nullable": bool, "unique": bool, "references": "table.id", "values": [str], "default": any}]}],\n'
    '  "relationships": [{"from": "table.col", "to": "table.col", "type": "one_to_many|many_to_one|one_to_one|many_to_many", "description": str}],\n'
    '  "functional_requirements": [{"module": str, "requirement": "The system shall …", "priority": "high|medium|low"}],\n'
    '  "non_functional_requirements": [{"category": str, "requirement": str}],\n'
    '  "business_workflows": [{"workflow_name": str, "steps": [str]}],\n'
    '  "validation_rules": [{"field": str, "rule": str}],\n'
    '  "notification_rules": [{"event": str, "recipients": [str], "channels": [str]}],\n'
    '  "reporting_requirements": [{"report_name": str, "filters": [str], "exports": ["PDF","Excel"]}],\n'
    '  "integration_requirements": [{"name": str, "type": str, "description": str, "required": bool}],\n'
    '  "security_requirements": [str],\n'
    '  "risk_priority": [{"area": str, "risk": str, "severity": "High|Medium|Low", "reason": str, "mitigation": str}],\n'
    '  "ambiguities": [{"area": str, "description": str, "assumption_made": str, "needs_clarification": bool}]\n'
    "}\n"
    "Reference foreign keys with the exact 'table.id' form.\n"
    "\n"
    "THE APPROVED PLAN IS THE BOUNDARY. It is what the customer read and signed "
    "off on, and it is the whole of what you are specifying.\n"
    "- Do NOT introduce a record, role, screen or capability the plan does not "
    "contain. Detail and sharpen what is there; never widen it. Anything you add "
    "beyond the plan is discarded, so it only costs you attention.\n"
    "- Give NO minimum counts any thought. Three tables is the right answer when "
    "the plan has three records, and one is the right answer when it has one.\n"
    "- If the plan has no login, the app has no accounts. Do not mention users, "
    "roles, permissions, admins, sign-in or audit logs anywhere — not in a "
    "requirement, not in a table, not in a risk.\n"
    "- Each `table_name` is the PLAIN PLURAL of the thing it holds: books, "
    "students, loans, products, sales. Never pluralise a name that is already "
    "plural — `bookses`, `studentses`, `loanses`, `productses`, `customerses` "
    "are not words. Nothing downstream corrects this: the builder creates the "
    "collection under exactly the name you write, so `bookses` ships as a "
    "real collection in a real app. It has happened in 4 specifications."
)


def _answers_digest(questions: list[dict], answers: list[dict]) -> str:
    by_id = {q["id"]: q for q in questions}
    lines = []
    for a in answers:
        q = by_id.get(a.get("question_id"))
        if not q:
            continue
        val = a.get("raw_text") or a.get("value")
        if isinstance(val, list):
            val = ", ".join(str(x) for x in val)
        lines.append(f"- {q['question']} → {val}")
    return "\n".join(lines) or "(no explicit answers; use sensible defaults)"


def _validate_pack(d: dict) -> None:
    """Accept precise enrichment without forcing extra scope."""
    if not isinstance(d, dict):
        raise ValueError("pack must be a JSON object")
    tables = d.get("tables", [])
    if not isinstance(tables, list):
        raise ValueError("'tables' must be a list when supplied")
    unnamed = [t for t in tables if not isinstance(t, dict) or not t.get("table_name")]
    if unnamed:
        raise ValueError("every supplied table needs a table_name")
    frs = d.get("functional_requirements", [])
    if not isinstance(frs, list):
        raise ValueError("'functional_requirements' must be a list when supplied")
    malformed = [f for f in frs if not isinstance(f, dict) or not f.get("requirement")]
    if malformed:
        raise ValueError("every supplied functional requirement needs requirement text")


def _plan_scope_guard(plan: dict, auth: bool):
    """A validator that fails the pack for going outside the plan."""
    allowed = {_norm(r.get("name", "")) for r in (plan.get("records") or [])}
    forbidden = ("login", "sign in", "sign-in", "password", "role", "permission",
                 "admin", "authenticat")

    def check(d: dict) -> None:
        if not isinstance(d, dict):
            raise ValueError("pack must be a JSON object")
        strays = sorted({
            str(t.get("table_name")) for t in (d.get("tables") or [])
            if isinstance(t, dict) and t.get("table_name")
            and _norm(t["table_name"]) not in allowed
        })
        if strays:
            raise ValueError(
                "these records are not in the approved plan and must be removed: "
                + ", ".join(strays)
                + ". The plan's records are: "
                + (", ".join(sorted(allowed)) or "(none)")
            )
        if not auth:
            blob = " ".join(
                str(f.get("requirement", "")) for f in (d.get("functional_requirements") or [])
                if isinstance(f, dict)
            ).lower()
            hits = sorted({w for w in forbidden if w in blob})
            if hits:
                raise ValueError(
                    "this app has no login, so no requirement may mention "
                    + ", ".join(hits) + ". Remove those requirements."
                )

    return check


def _norm(name: str) -> str:
    """Loose key so `Sale`, `sales` and `sale_items`/`Sale Item` compare sanely."""
    key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    if key.endswith("ies"):
        return key[:-3] + "y"
    if key.endswith("s") and not key.endswith("ss"):
        return key[:-1]
    return key


async def generate_srs_node(state: AgentState) -> AgentState:
    pid = state["project_id"]
    project = state.get("project", {})
    brief = state.get("brief", "")
    questions = state.get("questions", [])
    answers = state.get("answers", [])

    session = state.get("session") or {}
    plan = state.get("plan") or {}
    pack_profile = session.get("pack") or {}
    auth = _auth_on(pack_profile, plan) if plan else True

    if plan:
        await bus.log(pid, "SrsJsonGeneratorAgent",
                      "Composing the SRS from the approved plan…", progress=10)
        skeleton = build_srs_from_plan(project=project, plan=plan, pack=pack_profile,
                                       session=session, brief=brief)
        validator = _plan_scope_guard(plan, auth)
    else:
        await bus.log(pid, "SrsJsonGeneratorAgent",
                      "No approved plan — composing from the domain template…", progress=10)
        skeleton = build_offline_srs(project=project, answers=answers,
                                     session=session, brief=brief)
        validator = _validate_pack
    try:
        validate_srs(skeleton)
    except Exception as exc:  # noqa: BLE001 - should not happen; log if it does
        await bus.error(pid, "SrsJsonGeneratorAgent", f"Skeleton failed validation: {exc}")

    srs = skeleton
    try:
        llm = get_llm()
        await bus.emit(pid, "SrsJsonGeneratorAgent", "Asking the LLM for domain-specific content…", progress=35)
        digest = _answers_digest(questions, answers)
        plan_md = str(state.get("plan_markdown") or "").strip()
        user = (
            f"DETECTED DOMAIN: {state.get('classification', {}).get('detected_domain')}\n\n"
            f"USER IDEA / BRIEF:\n{brief[:3000]}\n\n"
            + (f"THE APPROVED PLAN — THIS IS THE BOUNDARY:\n{plan_md[:6000]}\n\n" if plan_md else "")
            + (("This app has NO login and NO user accounts. Say nothing about "
                "users, roles, permissions or admins.\n\n") if plan and not auth else "")
            + f"USER ANSWERS:\n{digest}\n\n"
            "Now produce the enrichment JSON described in the system message, "
            "tailored precisely to this idea."
        )
        pack = await llm.complete_json(
            system=_SYS, user=user, validator=validator,
            label="srs_enrich", trace_sink=lambda p: bus.trace(pid, p),
        )
        merged = (merge_pack_planned(skeleton, pack, plan) if plan
                  else merge_pack(skeleton, pack))
        validate_srs(merged)
        srs = merged
        doc = srs["srs_document"]
        await bus.emit(pid, "SrsJsonGeneratorAgent",
                       f"LLM enrichment merged: {len(doc['database_design']['tables'])} tables, "
                       f"{len(doc['functional_requirements'])} FR.", level="success", progress=60)
    except LLMUnavailable as exc:
        await bus.emit(pid, "SrsJsonGeneratorAgent",
                       f"LLM not used ({str(exc)[:140]}) — using the deterministic domain SRS.",
                       level="warn", progress=60)
    except LLMRepairFailed as exc:
        await bus.error(pid, "SrsJsonGeneratorAgent",
                        f"LLM enrichment didn't validate after retries; kept deterministic SRS. ({exc.label})",
                        data={"raw_preview": (exc.raw or "")[:500]})
    except Exception as exc:  # noqa: BLE001
        await bus.error(pid, "SrsJsonGeneratorAgent", f"SRS enrichment error: {exc}; kept deterministic SRS.")

    apply_international_profile(srs)

    if plan:
        srs["srs_document"]["approved_plan_markdown"] = str(state.get("plan_markdown") or "")
        attach_handoff(srs, plan, pack_profile, auth=auth)
        handoff = srs["srs_document"]["builder_handoff"]
        await bus.emit(pid, "SrsJsonGeneratorAgent",
                       f"Builder handoff ready: {len(handoff['requirements'])} requirements, "
                       f"{len(handoff['prompt'].split())} words.",
                       level="success", progress=65)

    summary = summarize_srs(srs)
    await bus.emit(
        pid, "SrsJsonGeneratorAgent",
        f"SRS ready: {summary['functional']} FR, {summary['non_functional']} NFR, "
        f"{summary['tables']} tables, {summary['roles']} roles"
        + ("" if auth else ", no login") + ".",
        level="success", progress=70, data={"summary": summary},
    )
    return {**state, "srs": srs}
