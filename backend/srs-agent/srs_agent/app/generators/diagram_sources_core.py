"""Diagram generation from the SRS JSON."""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Callable

from ..config import settings
from ..services import storage

_CARD = {
    "one_to_many": "||--o{",
    "many_to_one": "}o--||",
    "one_to_one": "||--||",
    "many_to_many": "}o--o{",
}


_LABEL = 60
_EDGE_LABEL = 40


def _san(text: str, limit: int = _LABEL) -> str:
    """A label safe to put inside a Mermaid quoted string."""
    text = re.sub(r"[\"'\[\]{}()|<>#;`]", "", str(text)).replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return (cut or text[:limit]) + "…"


def _br(text: str, limit: int = _LABEL) -> str:
    """Mermaid does not read `/n` inside a label — it needs `<br/>`."""
    return _san(text, limit).replace(" — ", "<br/>")


def _ent(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name)) or "entity"


def _plan(doc: dict) -> dict:
    """The plan these diagrams are drawn from."""
    return doc.get("effective_plan") or doc.get("approved_plan") or {}


def _verb(text: str) -> str:
    """A use case reads as an action."""
    text = str(text or "").strip().rstrip(".")
    return re.sub(r"^(?:the|a|an)\s+", "", text, flags=re.IGNORECASE)


MAX_ACTORS = 7
MAX_USE_CASES = 14


def actors_and_use_cases(doc: dict) -> tuple[list[dict], list[dict]]:
    """Who uses the system, and what each of them does."""
    plan = _plan(doc)
    use_cases: list[dict] = []
    by_text: dict[str, str] = {}

    def use_case(text: str) -> str | None:
        label = _san(_verb(text), _LABEL)
        if not label:
            return None
        key = label.casefold()
        if key in by_text:
            return by_text[key]
        if len(use_cases) >= MAX_USE_CASES:
            return None
        uid = f"U{len(use_cases)}"
        by_text[key] = uid
        use_cases.append({"id": uid, "label": label})
        return uid

    people = plan.get("users") or []
    if not people:

        allowed = {m.get("role"): m.get("allowed_functions") or []
                   for m in (doc.get("role_access_matrix") or [])}
        people = [{"role": r.get("role_name") or r.get("role_key"),
                   "can_do": allowed.get(r.get("role_key")) or []}
                  for r in (doc.get("roles") or [])]

    actors: list[dict] = []
    for person in people[:MAX_ACTORS]:
        label = _san(person.get("role") or "User", 28)
        if not label:
            continue
        does = [uid for uid in (use_case(d) for d in (person.get("can_do") or [])) if uid]
        actors.append({"id": f"A{len(actors)}", "label": label, "does": does})

    if not actors:
        actors = [{"id": "A0", "label": "User", "does": []}]

    # Product features are not always actor goals.
    if not any(a["does"] for a in actors):
        fallback = [uid for uid in (use_case(f) for f in (plan.get("features") or [])) if uid]
        if not fallback:
            fallback = [uid for uid in (use_case(m) for m in (doc.get("main_modules") or [])) if uid]
        for a in actors:
            a["does"] = list(fallback)

    return actors, use_cases


def use_case_diagram(doc: dict) -> str:
    actors, use_cases = actors_and_use_cases(doc)
    lines = ["flowchart LR",
             "  classDef actor fill:#EEF2FF,stroke:#6366F1,color:#3730A3;",
             "  classDef uc fill:#F8FAFC,stroke:#94A3B8,color:#0F172A;"]
    lines.append(f'  subgraph SYSTEM["{_san(doc.get("project_name", "System"), 40)}"]')
    lines.append("    direction TB")
    for uc in use_cases:
        lines.append(f'    {uc["id"]}(["{uc["label"]}"]):::uc')
    lines.append("  end")

    for a in actors:
        lines.append(f'  {a["id"]}["{a["label"]}"]:::actor')
        for uid in a["does"]:
            lines.append(f'  {a["id"]} --- {uid}')
    return "\n".join(lines)


def _is_decision(step: str) -> bool:
    text = str(step or "")
    return (bool(re.search(r"\b(if|whether|when)\b|\?", text, re.I))
            and bool(re.search(r"\b(else|otherwise|if not|on failure|on rejection)\b", text, re.I)))


def activity_diagram(doc: dict) -> str:
    workflows = doc.get("business_workflows") or []
    plan_flows = _plan(doc).get("workflows") or []
    if workflows:
        wf, steps = workflows[0], workflows[0].get("steps") or []
        title = wf.get("workflow_name", "Main Workflow")
    elif plan_flows:
        wf, steps = plan_flows[0], plan_flows[0].get("steps") or []
        title = wf.get("name", "Main Workflow")
    else:

        steps = [f for f in (_plan(doc).get("features") or [])][:6]
        title = "Using the application"

    steps = [s for s in steps if str(s).strip()][:9] or ["Open the application",
                                                         "Do the main task",
                                                         "See the result"]
    lines = ["flowchart TD",
             "  classDef dec fill:#FEF3C7,stroke:#F59E0B,color:#92400E;",
             f'  START(["Start: {_san(title, 48)}"])']
    prev = "START"
    for i, step in enumerate(steps):
        nid = f"s{i}"
        if _is_decision(str(step)):
            lines.append(f'  {nid}{{"{_san(step)}?"}}:::dec')
            lines.append(f"  {prev} --> {nid}")
            ok, bad = f"{nid}_ok", f"{nid}_no"
            lines.append(f'  {nid} -->|yes| {ok}["Continue"]')
            lines.append(f'  {nid} -->|no| {bad}["Correct and retry"]')
            lines.append(f"  {bad} --> {nid}")
            prev = ok
        else:
            lines.append(f'  {nid}["{_san(step)}"]')
            lines.append(f"  {prev} --> {nid}")
            prev = nid
    lines.append('  DONE(["End"])')
    lines.append(f"  {prev} --> DONE")
    return "\n".join(lines)


def _endpoints(doc: dict) -> list[tuple[str, str]]:
    """Real API calls, so the diagram stops saying `POST /api` for everything."""
    out = []
    for a in doc.get("api_design") or []:
        method, path = str(a.get("method", "")), str(a.get("path", ""))
        if method and path and "/auth/" not in path:
            out.append((method, path))
    return out


def sequence_diagram(doc: dict) -> str:
    workflows = doc.get("business_workflows") or []
    steps = (workflows[0].get("steps") if workflows else None) or             (_plan(doc).get("features") or []) or ["Perform the primary interaction"]
    steps = [str(s) for s in steps if str(s).strip()][:6]

    role = next((str(r.get("role_name") or r.get("role_key"))
                 for r in (doc.get("roles") or [])
                 if r.get("role_name") or r.get("role_key")), "User")
    system_name = _san(doc.get("project_name") or "System", 28)
    tables = (doc.get("database_design", {}) or {}).get("tables") or []
    endpoints = _endpoints(doc)
    integrations = (doc.get("integration_requirements") or [])

    lines = ["sequenceDiagram", "  autonumber",
             f"  actor U as {_san(role, 24)}", f"  participant S as {system_name}"]
    if endpoints:
        lines.append("  participant A as Application API")
    if tables:
        lines.append("  participant DB as Database")
    if integrations:
        lines.append(f"  participant X as {_san(integrations[0].get('name','External System'), 24)}")

    for i, step in enumerate(steps):
        label = _san(step, 48)
        lines.append(f"  U->>S: {label}")
        lines.append("  activate S")
        if endpoints:
            method, path = endpoints[i % len(endpoints)]
            lines.append(f"  S->>A: {method} {path}")
            lines.append("  activate A")
            if tables:
                table = _san(tables[i % len(tables)].get("table_name", "data"), 24)
                lines.append(f"  A->>DB: access {table}")
                lines.append("  DB-->>A: data / result")
            if integrations and any(str(integrations[0].get("name", "")).lower() in step.lower()
                                    or str(integrations[0].get("type", "")).lower() in step.lower()
                                    for _ in [0]):
                lines.append("  A->>X: integration request")
                lines.append("  X-->>A: integration response")
            lines.append("  A-->>S: result")
            lines.append("  deactivate A")
        elif tables:
            table = _san(tables[i % len(tables)].get("table_name", "data"), 24)
            lines.append(f"  S->>DB: access {table}")
            lines.append("  DB-->>S: data / result")
        lines.append("  S-->>U: present outcome")
        lines.append("  deactivate S")
    return "\n".join(lines)


def erd_diagram(doc: dict) -> str:
    db = doc.get("database_design", {}) or {}
    tables = (db.get("tables") or [])[:22]
    lines = ["erDiagram"]
    names = {t.get("table_name") for t in tables}

    for t in tables:
        lines.append(f"  {_ent(t.get('table_name', 'entity'))} {{")
        for fld in (t.get("fields") or [])[:14]:
            name = re.sub(r"[^A-Za-z0-9_]", "_", str(fld.get("name", "field")))
            raw = str(fld.get("type", "string"))

            typ = "uuid" if raw == "foreign_key" else re.sub(r"[^A-Za-z0-9_]", "_", raw)
            if fld.get("primary_key"):
                key = " PK"
            elif raw == "foreign_key" or fld.get("references"):
                key = " FK"
            elif fld.get("unique"):
                key = " UK"
            else:
                key = ""
            lines.append(f"    {typ} {name}{key}")
        lines.append("  }")

    seen: set[tuple[str, str, str]] = set()
    for rel in db.get("relationships") or []:
        a = str(rel.get("from", "")).split(".")[0]
        b = str(rel.get("to", "")).split(".")[0]
        if a not in names or b not in names or a == b:
            continue
        card = _CARD.get(rel.get("type", "one_to_many"), "||--o{")
        edge = (a, b, card)
        if edge in seen:
            continue
        seen.add(edge)
        label = _san(rel.get("description") or "", _EDGE_LABEL)

        if "…" in label:
            label = "relates to"
        lines.append(f'  {_ent(a)} {card} {_ent(b)} : "{label or "relates to"}"')
    return "\n".join(lines)
