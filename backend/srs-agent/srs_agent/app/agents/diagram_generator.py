"""DiagramGeneratorAgent — build, LLM-improve, and render diagrams."""
from __future__ import annotations

import asyncio

from ..generators.diagrams import (
    DIAGRAM_KINDS,
    STANDARD_DIAGRAM_KINDS,
    build_diagrams,
    mermaid_problems,
    render_diagrams,
    valid_mermaid,
)
from ..llm import LLMRepairFailed, LLMUnavailable, get_llm
from ..services.events import bus
from .customer_context import customer_context
from .state import AgentState

_SYS = (
    "You are a software architect who may enrich ONLY the supplemental architecture "
    "Mermaid views. Return ONLY JSON: {\"diagrams\":[{\"kind\":..., \"source\":\"<mermaid>\"}]} "
    "for kinds system_context, component, deployment. The eight standards diagrams "
    "(use_case, sequence, erd, activity, class_object, state_machine, dfd, bpmn) are "
    "generated deterministically and rendered natively so their notation cannot be "
    "changed by the language model. Every supplemental diagram must start with "
    "flowchart TD or flowchart LR; connect every declared node; use only real names "
    "from the supplied SRS; never invent services, queues, actors or stores."
)


def _srs_context(doc: dict) -> str:
    db = doc.get("database_design", {})
    tbls = []
    for t in db.get("tables", [])[:20]:
        fields = ", ".join(f.get("name", "") for f in (t.get("fields", []) or [])[:8])
        tbls.append(f"{t['table_name']}({fields})")
    rels = [f"{r.get('from')}->{r.get('to')} [{r.get('type')}]" for r in db.get("relationships", [])[:18]]
    roles = ", ".join(r.get("role_name", "") for r in doc.get("roles", []))
    mods = ", ".join(doc.get("main_modules", []))
    wfs = []
    for w in (doc.get("business_workflows", []) or [])[:2]:
        wfs.append(w.get("workflow_name", "") + ": " + " -> ".join(w.get("steps", [])[:8]))
    integ = ", ".join(i.get("name", "") for i in doc.get("integration_requirements", []))
    return (
        f"PROJECT: {doc.get('project_name')}\n"
        f"ROLES: {roles}\n"
        f"MODULES: {mods}\n"
        f"TABLES: {'; '.join(tbls)}\n"
        f"RELATIONSHIPS: {'; '.join(rels)}\n"
        f"WORKFLOWS: {' || '.join(wfs)}\n"
        f"INTEGRATIONS: {integ}\n"
    )


def _validate_diagram_pack(d: dict) -> None:
    if not isinstance(d, dict) or not isinstance(d.get("diagrams"), list):
        raise ValueError("return JSON {\"diagrams\":[{kind, source}]}")
    valid, faults = [], []
    for x in d["diagrams"]:
        if not isinstance(x, dict) or x.get("kind") not in DIAGRAM_KINDS:
            continue
        problems = mermaid_problems(x["kind"], x.get("source", ""))
        if problems:
            faults.append(f"{x['kind']}: {'; '.join(problems)}")
        else:
            valid.append(x)
    if len(valid) < 1:
        detail = (" Problems found — " + " | ".join(faults[:6])) if faults else ""
        raise ValueError("provide at least 1 structurally valid supplemental architecture diagram." + detail)


async def _llm_diagrams(pid: str, srs: dict, context: str = "") -> dict[str, str]:
    doc = srs.get("srs_document", srs)
    llm = get_llm()
    user = (
        (f"{context}\n\n" if context else "")
        + _srs_context(doc)
        + "\nProduce only the 3 supplemental Mermaid diagrams (system_context, component, "
        "deployment) as specified. Label everything in the customer's own words "
        "and do not add infrastructure or services that the SRS does not require."
    )
    data = await llm.complete_json(
        system=_SYS, user=user, validator=_validate_diagram_pack,
        label="diagrams", trace_sink=lambda p: bus.trace(pid, p),
    )
    out: dict[str, str] = {}
    for x in data.get("diagrams", []):
        if isinstance(x, dict) and x.get("kind") in DIAGRAM_KINDS and valid_mermaid(x["kind"], x.get("source", "")):
            out[x["kind"]] = x["source"].strip()
    return out


async def diagram_node(state: AgentState) -> AgentState:
    pid = state["project_id"]
    srs = state.get("srs", {})
    await bus.log(pid, "DiagramGeneratorAgent", "Generating diagrams from the SRS…", progress=80)

    faults: list[str] = []
    diagrams = build_diagrams(srs, on_error=faults.append)
    for d in diagrams:
        d["generated_by"] = "template"
    for fault in faults:
        await bus.error(pid, "DiagramGeneratorAgent", f"Diagram template fault — {fault}")

    try:
        await bus.emit(pid, "DiagramGeneratorAgent", "Asking the LLM to enrich editable Mermaid sources (native layout stays fixed)…", progress=84)
        llm_map = await _llm_diagrams(
            pid, srs, customer_context(brief=state.get("brief", ""),
                                       session=state.get("session"),
                                       project=state.get("project")))
        improved = 0
        for d in diagrams:
            if d["kind"] in STANDARD_DIAGRAM_KINDS:
                continue
            src = llm_map.get(d["kind"])
            if src and valid_mermaid(d["kind"], src):
                d["source"] = src
                d["generated_by"] = "llm"
                improved += 1
        await bus.emit(pid, "DiagramGeneratorAgent",
                       f"LLM enriched {improved} editable Mermaid source(s); all canonical layouts remain native/deterministic.",
                       level="success", progress=88)
    except LLMUnavailable as exc:
        await bus.emit(pid, "DiagramGeneratorAgent",
                       f"LLM not used ({str(exc)[:120]}) — using detailed template diagrams.",
                       level="warn", progress=88)
    except (LLMRepairFailed, ValueError) as exc:
        await bus.emit(pid, "DiagramGeneratorAgent",
                       f"LLM diagrams unusable ({exc}); using detailed template diagrams.",
                       level="warn", progress=88)
    except Exception as exc:  # noqa: BLE001
        await bus.error(pid, "DiagramGeneratorAgent", f"Diagram LLM step error: {exc}; using templates.")

    render_faults: list[str] = []

    diagrams = await asyncio.to_thread(render_diagrams, pid, diagrams,
                                       doc=srs.get("srs_document", srs),
                                       on_error=render_faults.append)
    srs.setdefault("srs_document", {})["diagrams"] = diagrams

    for fault in render_faults[:8]:
        await bus.error(pid, "DiagramGeneratorAgent", fault)

    rendered = sum(1 for d in diagrams if d.get("svg_path") or d.get("png_path"))
    await bus.emit(
        pid, "DiagramGeneratorAgent",
        f"Generated {len(diagrams)} diagrams"
        + (f", {rendered} rendered to image." if rendered
           else " (sources saved; see Errors for why no images were rendered)."),
        level="success", progress=95, data={"count": len(diagrams), "rendered": rendered},
    )
    return {**state, "srs": srs, "diagrams": diagrams}
