"""International standards profile for generated requirements documents."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

SRS_STANDARD = "ISO/IEC/IEEE 29148:2018"
SRS_STANDARD_TITLE = "Systems and software engineering — Life cycle processes — Requirements engineering"
UML_STANDARD = "OMG UML 2.5.1"
BPMN_STANDARD = "OMG BPMN 2.0.2"
ERD_NOTATION = "Crow's Foot ERD"
DFD_NOTATION = "Yourdon/DeMarco-style DFD"

_VAGUE = re.compile(r"\b(user[- ]?friendly|easy|fast|quickly|appropriate|adequate|etc\.?|and so on|as needed|normally|usually)\b", re.I)
_COMPOUND = re.compile(r"\b(and|or)\b", re.I)
_TESTABLE = re.compile(r"\b(within|less than|more than|at least|at most|%|seconds?|minutes?|milliseconds?|must|shall|will)\b", re.I)


def _verification_method(text: str, category: str = "") -> str:
    low = f"{category} {text}".lower()
    if any(k in low for k in ("performance", "latency", "response time", "throughput", "load", "concurrent")):
        return "Test / Measurement"
    if any(k in low for k in ("security", "permission", "role", "encrypt", "authentication", "authorization", "privacy")):
        return "Test / Inspection"
    if any(k in low for k in ("availability", "uptime", "recovery", "backup")):
        return "Test / Analysis"
    if any(k in low for k in ("ui", "screen", "display", "show", "render", "accessible", "wcag")):
        return "Demonstration / Inspection"
    return "Functional Test"


def _quality_flags(text: str) -> list[str]:
    """Conservative requirement-quality warnings, not a certification score."""
    s = " ".join(str(text or "").split())
    flags: list[str] = []
    if not s:
        return ["empty"]
    if _VAGUE.search(s):
        flags.append("potentially ambiguous wording")
    if len(_COMPOUND.findall(s)) >= 3:
        flags.append("possibly compound requirement")
    if not _TESTABLE.search(s) and not re.search(r"\bshall\b", s, re.I):
        flags.append("verification criterion should be made explicit")
    return flags


def _source_map(doc: dict) -> dict[str, str]:
    """Best-effort provenance from approved plan features / screens."""
    plan = doc.get("effective_plan") or doc.get("approved_plan") or {}
    sources: dict[str, str] = {}
    for i, feature in enumerate(plan.get("features") or [], 1):
        text = str(feature or "").strip()
        if text:
            sources[text.casefold()] = f"Approved Plan Feature {i}"
    return sources


def apply_international_profile(srs: dict) -> dict:
    """Enrich an SRS in place with ISO/IEC/IEEE-29148-oriented metadata."""
    doc = srs.get("srs_document", srs)
    if not isinstance(doc, dict):
        return srs

    doc["standards_profile"] = {
        "requirements_standard": SRS_STANDARD,
        "requirements_standard_title": SRS_STANDARD_TITLE,
        "uml_standard": UML_STANDARD,
        "bpmn_standard": BPMN_STANDARD,
        "erd_notation": ERD_NOTATION,
        "dfd_notation": DFD_NOTATION,
        "conformance_note": (
            "Structure and notation are aligned to the named standards. Formal conformance or "
            "certification requires project-specific review and approval by the responsible organisation."
        ),
    }
    doc.setdefault("document_control", {})
    dc = doc["document_control"]
    dc.setdefault("document_id", f"SRS-{re.sub(r'[^A-Z0-9]+', '-', str(doc.get('project_name', 'PROJECT')).upper()).strip('-')[:32] or 'PROJECT'}")
    dc.setdefault("version", doc.get("version", "1.0.0"))
    dc.setdefault("status", "Draft")
    dc.setdefault("prepared_date", date.today().isoformat())
    dc.setdefault("standard", SRS_STANDARD)
    dc.setdefault("document_owner", "Project Stakeholders")
    doc.setdefault("revision_history", [{
        "version": dc.get("version", doc.get("version", "1.0.0")),
        "date": dc.get("prepared_date", date.today().isoformat()),
        "description": "Generated requirements baseline",
    }])
    doc.setdefault("approval_record", [])

    source_map = _source_map(doc)
    review: list[dict[str, Any]] = []

    for fr in doc.get("functional_requirements") or []:
        if not isinstance(fr, dict):
            continue
        text = str(fr.get("requirement") or "").strip()
        fr.setdefault("source", source_map.get(text.casefold(), "Approved SRS / stakeholder input"))
        fr.setdefault("verification_method", _verification_method(text, "functional"))
        fr.setdefault("rationale", "Required to satisfy the approved product capability represented by this requirement.")
        flags = _quality_flags(text)
        fr["quality_review"] = {"status": "review" if flags else "pass", "warnings": flags}
        if flags:
            review.append({"requirement_id": fr.get("id"), "warnings": flags})

    for nfr in doc.get("non_functional_requirements") or []:
        if not isinstance(nfr, dict):
            continue
        text = str(nfr.get("requirement") or "").strip()
        nfr.setdefault("source", "Approved SRS / stakeholder quality expectation")
        nfr.setdefault("verification_method", _verification_method(text, str(nfr.get("category") or "")))
        flags = _quality_flags(text)
        nfr["quality_review"] = {"status": "review" if flags else "pass", "warnings": flags}
        if flags:
            review.append({"requirement_id": nfr.get("id"), "warnings": flags})

    fr_by_id = {str(r.get("id")): r for r in doc.get("functional_requirements") or [] if isinstance(r, dict)}
    for row in doc.get("requirement_traceability_matrix") or []:
        if not isinstance(row, dict):
            continue
        fr = fr_by_id.get(str(row.get("requirement_id"))) or {}
        row.setdefault("source", fr.get("source", "Approved SRS"))
        row.setdefault("verification_method", fr.get("verification_method", "Functional Test"))
        if not row.get("test_case"):
            rid = str(row.get("requirement_id") or "REQ")
            row["test_case"] = f"TC-{rid.replace('FR-', '').replace('NFR-', '')}"

    doc["requirements_quality_review"] = {
        "profile": SRS_STANDARD,
        "items_needing_human_review": review,
        "note": "Warnings are conservative lint findings, not proof that a requirement is invalid.",
    }
    return srs

DIAGRAM_NOTATION = {
    "use_case": [
        "Stick figure — actor (person or external system)",
        "Oval — use case (actor goal / system behavior)",
        "System boundary — scope of the subject system",
        "Solid line — actor/use-case association",
        "Dashed «include» / «extend» relationships appear only when explicitly specified",
    ],
    "sequence": [
        "Participant box + dashed vertical line — lifeline",
        "Thin rectangle on lifeline — activation / execution occurrence",
        "Solid arrow — synchronous call/request",
        "Dashed open arrow — return/response",
        "Time progresses from top to bottom",
    ],
    "erd": [
        "Entity rectangle — entity/table with attributes",
        "PK / FK — primary and foreign keys",
        "Single bar — one; crow's foot — many",
        "Optionality markers are shown only when the SRS provides enough information",
    ],
    "activity": [
        "Solid black circle — initial node",
        "Rounded rectangle — action",
        "Diamond — decision/merge only for explicit guarded alternatives",
        "Arrow — control flow",
        "Circle-in-circle — final node",
    ],
    "class_object": [
        "Compartmented rectangle — class name and attributes",
        "+ / - — public/private visibility convention",
        "Multiplicity at association ends — 1, 0..*, etc.",
        "Underlined instance-name : Class — object diagram instance",
    ],
    "state_machine": [
        "Rounded rectangle — state",
        "Solid black circle — initial pseudo-state",
        "Circle-in-circle — final pseudo-state",
        "Labeled arrow — transition/event",
        "No transition is invented when the SRS does not state a legal lifecycle",
    ],
    "dfd": [
        "Rectangle — external entity",
        "Circle/rounded process — process that transforms data",
        "Open-ended data-store symbol — persistent data store",
        "Labeled arrow — data flow (not control flow)",
    ],
    "bpmn": [
        "Thin circle / thick circle — start/end event",
        "Rounded rectangle — task/activity",
        "Diamond — gateway only for explicit branching semantics",
        "Pool/lane — participant/responsibility boundary",
        "Solid arrow — sequence flow; message flow is not used inside one pool",
    ],
    "system_context": [
        "Central system boundary — the software system in scope",
        "Person/role box — human actor using the system",
        "External-system box — third-party dependency or integration",
        "Database cylinder — persistent application data",
        "Labeled arrow — externally visible relationship",
    ],
    "component": [
        "Component rectangle with UML component glyph — deployable/logical component",
        "Horizontal bands — presentation, application/domain, and data/external interfaces",
        "Dependency arrow — one component depends on another supported SRS element",
    ],
    "deployment": [
        "3-D node box — device or execution environment",
        "Nested text — software artifacts hosted on the node",
        "Communication path — protocol/connection between nodes",
        "External service box — separately deployed integration",
    ],
}
