"""Architecture and process Mermaid sources."""
from __future__ import annotations

import re

from .diagram_sources_core import (
    _ent, _plan, _san, actors_and_use_cases, activity_diagram, erd_diagram,
    sequence_diagram, use_case_diagram,
)

def system_context_diagram(doc: dict) -> str:
    name = _san(doc.get("project_name", "System"), 32)
    kind = _san((doc.get("app_type") or {}).get("primary_type", "Web application"), 28)
    integrations = (doc.get("integration_requirements") or [])[:6]
    tables = (doc.get("database_design", {}) or {}).get("tables") or []

    lines = ["flowchart TB",
             "  classDef sys fill:#EEF2FF,stroke:#6366F1,color:#3730A3,stroke-width:2px;",
             "  classDef actor fill:#F0FDF4,stroke:#16A34A,color:#166534;",
             "  classDef data fill:#F1F5F9,stroke:#64748B,color:#0F172A;"]
    if integrations:
        lines.append("  classDef ext fill:#FFF7ED,stroke:#EA580C,color:#9A3412;")

    lines.append(f'  SYS["{name}<br/>{kind}"]:::sys')

    actors, _ = actors_and_use_cases(doc)
    for a in actors:
        lines.append(f'  {a["id"]}["{a["label"]}"]:::actor')
        lines.append(f'  {a["id"]} -- uses --> SYS')
    for i, integ in enumerate(integrations):
        lines.append(f'  X{i}["{_san(integ.get("name", "External service"), 28)}"]:::ext')
        lines.append(f"  SYS -- integrates with --> X{i}")
    if tables:
        lines.append('  DB[("Application data")]:::data')
        lines.append("  SYS -- reads and writes --> DB")
    return "\n".join(lines)


def component_diagram(doc: dict) -> str:
    """Screens, the application, and what it stores."""
    plan = _plan(doc)
    screens = [s.get("name") for s in (plan.get("screens") or []) if s.get("name")][:8]
    if not screens:
        screens = [p.get("page_name") for p in
                   (doc.get("public_pages") or []) + (doc.get("protected_pages") or [])][:8]
    screens = [s for s in screens if s] or ["Main screen"]

    tables = [t.get("table_name") for t in
              ((doc.get("database_design", {}) or {}).get("tables") or [])][:8]
    auth = bool((doc.get("authentication_requirement") or {}).get("login_required"))
    notifies = bool(doc.get("notification_rules"))
    integrations = doc.get("integration_requirements") or []

    lines = ["flowchart TB",
             "  classDef ui fill:#EEF2FF,stroke:#6366F1,color:#3730A3;",
             "  classDef svc fill:#F8FAFC,stroke:#94A3B8,color:#0F172A;",
             "  classDef data fill:#F1F5F9,stroke:#64748B,color:#0F172A;",
             '  subgraph CLIENT["Screens"]']
    for i, s in enumerate(screens):
        lines.append(f'    P{i}["{_san(s, 32)}"]:::ui')
    lines.append("  end")

    lines.append('  subgraph APP["Application"]')
    lines.append('    GW["Request handling"]:::svc')
    if auth:
        lines.append('    AUTH["Sign-in and permissions"]:::svc')
    lines.append("  end")

    if tables:
        lines.append('  subgraph DATA["Stored data"]')
        for i, t in enumerate(tables):
            lines.append(f'    D{i}[("{_san(str(t).replace("_", " "), 28)}")]:::data')
        lines.append("  end")

    for i, _ in enumerate(screens):
        lines.append(f"  P{i} --> GW")
    if auth:
        lines.append("  GW --> AUTH")
    for i, _ in enumerate(tables):
        lines.append(f"  GW --> D{i}")

    if notifies:
        lines.append('  NOTIF["Notifications"]:::svc')
        lines.append("  GW --> NOTIF")
    for i, integ in enumerate(integrations[:4]):
        lines.append(f'  X{i}["{_san(integ.get("name", "External service"), 28)}"]:::svc')
        lines.append(f"  GW --> X{i}")
    return "\n".join(lines)


def deployment_diagram(doc: dict) -> str:
    stack = (doc.get("app_type") or {}).get("example_stack") or {}
    frontend = _san(stack.get("frontend", "Web application"), 26)
    backend = _san(stack.get("backend", "API"), 26)
    database = _san(stack.get("database", "Database"), 26)
    auth = bool((doc.get("authentication_requirement") or {}).get("login_required"))
    integrations = (doc.get("integration_requirements") or [])[:3]
    uploads = any("upload" in str(f).lower() or "image" in str(f).lower()
                  for f in (_plan(doc).get("features") or []))

    lines = ["flowchart LR",
             "  classDef node fill:#F8FAFC,stroke:#94A3B8,color:#0F172A;",
             '  subgraph EDGE["User device"]',
             '    Browser["Browser"]:::node',
             "  end",
             '  subgraph HOST["Hosting"]',
             f'    Web["{frontend}"]:::node',
             f'    Api["{backend}"]:::node',
             "  end",
             '  subgraph STORE["Data"]',
             f'    DB[("{database}")]:::node']
    if uploads:
        lines.append('    OBJ[("Uploaded files")]:::node')
    lines.append("  end")
    lines += ["  Browser --> Web", "  Web --> Api", "  Api --> DB"]
    if uploads:
        lines.append("  Api --> OBJ")
    if auth:
        lines.append('  Api --> SESS["Session store"]:::node')
    for i, integ in enumerate(integrations):
        lines.append(f'  Api --> X{i}["{_san(integ.get("name", "External service"), 26)}"]:::node')
    return "\n".join(lines)


def _first_lifecycle(doc: dict) -> dict:
    """Return the lifecycle whose states are best supported by explicit text."""
    candidates = []
    for table in ((doc.get("database_design") or {}).get("tables") or []):
        for fld in table.get("fields") or []:
            name = str(fld.get("name") or "")
            vals = [str(v) for v in (fld.get("values") or []) if str(v).strip()]
            if len(vals) >= 2 and re.search(r"(?:status|state|stage|phase|lifecycle)", name, re.I):
                candidates.append({"entity": table.get("table_name", "Entity"), "field": name, "states": vals[:8]})
    if not candidates:
        return {}
    texts = [str(r.get("requirement") or "") for r in (doc.get("functional_requirements") or []) if isinstance(r, dict)]
    for wf in doc.get("business_workflows") or []:
        texts += [str(x) for x in (wf.get("steps") or [])]
    blob = "\n".join(texts)
    for c in candidates:
        states = c["states"]
        for a in states:
            for b in states:
                if a != b and (re.search(rf"\bfrom\s+{re.escape(a)}\s+to\s+{re.escape(b)}\b", blob, re.I)
                               or re.search(rf"\b{re.escape(a)}\s*(?:->|→)\s*{re.escape(b)}\b", blob, re.I)):
                    return c
    return candidates[0]


def _state_transitions(doc: dict, life: dict) -> list[tuple[str, str, str]]:
    """Only transitions explicitly stated as `from X to Y` or `X -> Y`."""
    states = [str(s) for s in (life.get("states") or [])]
    if not states:
        return []
    text_rows = []
    text_rows += [str(r.get("requirement") or "") for r in (doc.get("functional_requirements") or []) if isinstance(r, dict)]
    for wf in doc.get("business_workflows") or []:
        text_rows += [str(x) for x in (wf.get("steps") or [])]
    found = []
    for text in text_rows:
        for a in states:
            for b in states:
                if a == b:
                    continue
                pat1 = rf"\bfrom\s+{re.escape(a)}\s+to\s+{re.escape(b)}\b"
                pat2 = rf"\b{re.escape(a)}\s*(?:->|→)\s*{re.escape(b)}\b"
                if re.search(pat1, text, re.I) or re.search(pat2, text, re.I):
                    item = (a, b, _san(text, 42) or "transition")
                    if item not in found:
                        found.append(item)
    return found[:12]


def class_object_diagram(doc: dict) -> str:
    """UML class diagram source; the native renderer also shows an object instance."""
    tables = ((doc.get("database_design") or {}).get("tables") or [])[:10]
    lines = ["classDiagram"]
    for t in tables:
        cname = _ent(str(t.get("table_name") or "Entity").title().replace("_", ""))
        lines.append(f"  class {cname} {{")
        for fld in (t.get("fields") or [])[:10]:
            vis = "+" if fld.get("primary_key") else "-"
            typ = re.sub(r"[^A-Za-z0-9_<>,]", "", str(fld.get("type") or "String")) or "String"
            name = re.sub(r"[^A-Za-z0-9_]", "_", str(fld.get("name") or "field"))
            lines.append(f"    {vis}{typ} {name}")
        lines.append("  }")
    names = {str(t.get("table_name")): _ent(str(t.get("table_name") or "Entity").title().replace("_", "")) for t in tables}
    for rel in ((doc.get("database_design") or {}).get("relationships") or [])[:16]:
        a = str(rel.get("from") or "").split(".")[0]
        b = str(rel.get("to") or "").split(".")[0]
        if a not in names or b not in names or a == b:
            continue
        mult = {
            "one_to_many": '"1" --> "0..*"',
            "many_to_one": '"0..*" --> "1"',
            "one_to_one": '"1" --> "1"',
            "many_to_many": '"0..*" --> "0..*"',
        }.get(str(rel.get("type") or ""), '"1" --> "0..*"')
        label = _san(rel.get("description") or "relates to", 32)
        lines.append(f"  {names[a]} {mult} {names[b]} : {label}")
    return "\n".join(lines)


def state_machine_diagram(doc: dict) -> str:
    life = _first_lifecycle(doc)
    transitions = _state_transitions(doc, life) if life else []
    lines = ["stateDiagram-v2"]
    if not life or not transitions:
        lines += ["  [*] --> NotApplicable",
                  "  NotApplicable : No explicit legal state transitions specified",
                  "  NotApplicable --> [*]"]
        return "\n".join(lines)
    ids = {state: f"S{i}" for i, state in enumerate(life["states"])}
    for state, sid in ids.items():
        lines.append(f"  {sid} : {_san(state, 36)}")
    first = transitions[0][0]
    lines.append(f"  [*] --> {ids[first]}")
    for a,b,label in transitions:
        lines.append(f"  {ids[a]} --> {ids[b]} : {_san(label, 32)}")
    return "\n".join(lines)


def dfd_diagram(doc: dict) -> str:
    """Level-1 data-flow view: external entities, processes, stores, labelled data."""
    actors, _ = actors_and_use_cases(doc)
    modules = [str(m) for m in (doc.get("main_modules") or []) if str(m).strip()][:5] or ["Core processing"]
    stores = [str(t.get("table_name")) for t in (((doc.get("database_design") or {}).get("tables") or [])[:5]) if t.get("table_name")]
    lines = ["flowchart LR",
             "  classDef ext fill:#fff,stroke:#334155,color:#0f172a;",
             "  classDef proc fill:#f0fdf4,stroke:#2f855a,color:#0f172a;",
             "  classDef store fill:#fff7ed,stroke:#c2410c,color:#0f172a;"]
    for i,a in enumerate(actors[:4]):
        lines.append(f'  E{i}["{_san(a["label"],28)}"]:::ext')
    for i,m in enumerate(modules):
        lines.append(f'  P{i}(["{i+1}.0 {_san(m,30)}"]):::proc')
    for i,t in enumerate(stores):
        lines.append(f'  D{i}["D{i+1}: {_san(t.replace("_"," ").title(),28)}"]:::store')
    if actors and modules:
        for i,_ in enumerate(actors[:4]):
            lines.append(f"  E{i} -->|request data| P0")
        for i in range(len(modules)-1):
            lines.append(f"  P{i} -->|processed data| P{i+1}")
        for i,_ in enumerate(stores):
            lines.append(f"  P{i % len(modules)} -->|records| D{i}")
            lines.append(f"  D{i} -->|stored data| P{i % len(modules)}")
        for i,_ in enumerate(actors[:4]):
            lines.append(f"  P{len(modules)-1} -->|result| E{i}")
    return "\n".join(lines)


def bpmn_diagram(doc: dict) -> str:
    """Mermaid preview of BPMN semantics; native SVG is the main BPMN rendering."""
    workflows = doc.get("business_workflows") or []
    steps = [str(s) for s in ((workflows[0].get("steps") if workflows else []) or []) if str(s).strip()][:8]
    if not steps:
        steps = ["Receive request", "Process request", "Return result"]
    lines = ["flowchart TD", "  START((Start))"]
    prev = "START"
    for i, step in enumerate(steps):
        nid = f"T{i}"
        if re.search(r"\b(if|whether|approve|reject|valid|complete)\b", step, re.I):
            lines.append(f'  {nid}{{"{_san(step,40)}"}}')
        else:
            lines.append(f'  {nid}(["{_san(step,40)}"])')
        lines.append(f"  {prev} --> {nid}")
        prev = nid
    lines.append("  END(((End)))")
    lines.append(f"  {prev} --> END")
    return "\n".join(lines)


_BUILDERS = [
    ("use_case", "Use Case Diagram", use_case_diagram),
    ("sequence", "Sequence Diagram", sequence_diagram),
    ("erd", "Entity-Relationship (ER) Diagram", erd_diagram),
    ("activity", "Activity Diagram", activity_diagram),
    ("class_object", "Class & Object Diagram", class_object_diagram),
    ("state_machine", "State Machine Diagram", state_machine_diagram),
    ("dfd", "Data Flow Diagram (DFD)", dfd_diagram),
    ("bpmn", "BPMN Process Diagram", bpmn_diagram),
    ("system_context", "System Context Diagram", system_context_diagram),
    ("component", "Component Diagram", component_diagram),
    ("deployment", "Deployment Diagram", deployment_diagram),
]
