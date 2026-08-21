"""Build the structured handoff consumed by AgentForge."""
from __future__ import annotations

import re

from .builder_access import _routes_for
from .builder_auth import build_auth_contract
from .builder_features import build_feature_contracts
from .builder_constants import HANDOFF_VERSION, KINDS
from .builder_prompt import render_prompt
from .builder_utils import (
    _api_file, _clean, _entity, _field_spec, _kind_for, _normal_route_pattern,
    _page_file, _roles_for_route, _strip_forbidden, _trace_map,
)

def build_handoff(*, plan: dict, srs_document: dict, pack: dict | None = None,
                  auth: bool = False) -> dict:
    """Return a backward-compatible, lossless handoff for AgentForge."""
    plan = plan or {}
    pack = pack or {}
    doc = srs_document or {}
    trace = _trace_map(doc)

    requirements: list[dict] = []
    seen: set[str] = set()

    def add(text: str, kind: str = "feature", entity: str | None = None, *,
            source_id: str = "", module: str = "", priority: str = "",
            source: str = "plan", proof: dict | None = None,
            roles: list[str] | None = None) -> None:
        text = _clean(_strip_forbidden(text))
        if not text or text.lower() in seen or kind not in KINDS:
            return
        seen.add(text.lower())
        requirements.append({
            "id": f"R{len(requirements) + 1}",
            "text": text,
            "kind": kind,
            "entity": entity,
            "source_id": source_id or None,
            "module": module or None,
            "priority": priority or None,
            "source": source,
            "proof": proof or None,
            "roles": list(roles or []),
        })

    # Functional requirements are the product ledger.
    for fr in (doc.get("functional_requirements") or []):
        if not isinstance(fr, dict):
            continue
        rid = _clean(fr.get("id") or "")
        req = _clean(fr.get("requirement") or fr.get("description") or fr.get("text") or "")
        if not req:
            continue
        tr = trace.get(rid, {})
        tables = tr.get("tables") or []
        entity = _entity(tables[0]) if tables else None
        add(req, _kind_for(req), entity,
            source_id=rid,
            module=_clean(fr.get("module") or tr.get("module") or ""),
            priority=_clean(fr.get("priority") or "medium"),
            source="srs.functional_requirements",
            proof=tr or None,
            roles=[_clean(x) for x in (fr.get("allowed_roles") or []) if _clean(x)])

    srs_pages = [p for p in ((doc.get("public_pages") or [])
                             + (doc.get("protected_pages") or []))
                 if isinstance(p, dict) and _clean(p.get("page_name", ""))]
    public_routes = {_normal_route_pattern(_clean(p.get("route") or ""))
                     for p in (doc.get("public_pages") or []) if isinstance(p, dict)}

    pages: list[dict] = []
    if srs_pages:
        for page in srs_pages:
            name = _clean(page.get("page_name", ""))
            raw_route = _clean(page.get("route", "")) or (
                "/" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
            route = _normal_route_pattern(raw_route)
            fns = [_clean(f) for f in (page.get("functions") or []) if _clean(f)]
            sections = [_clean(s) for s in (page.get("sections") or []) if _clean(s)]
            purpose = "; ".join(fns)
            pages.append({
                "route": route,
                "srs_route": raw_route,
                "name": name,
                "primary": True,
                "purpose": purpose,
                "functions": fns,
                "sections": sections,
                "public": route in public_routes or page.get("login_required") is False,
                "login_required": bool(page.get("login_required")) if page.get("login_required") is not None else route not in public_routes,
                "allowed_roles": [_clean(r) for r in (page.get("allowed_roles") or []) if _clean(r)],
                "expected_file": _page_file(route),
            })
            # Retain page requirements for old consumers that use KINDS.
            add(f"{name} page at {route}" + (f": {purpose}" if purpose else ""),
                "page", None, source="srs.pages")
    else:
        for i, screen in enumerate(plan.get("screens") or []):
            if not isinstance(screen, dict):
                continue
            name = _clean(screen.get("name", ""))
            if not name:
                continue
            route = _normal_route_pattern(_clean(screen.get("route") or "")) or ("/" if i == 0 else "/" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
            purpose = _clean(screen.get("purpose", ""))
            pages.append({
                "route": route, "srs_route": route, "name": name, "primary": True,
                "purpose": purpose, "functions": [], "sections": [],
                "public": not auth, "login_required": bool(auth),
                "allowed_roles": [_clean(r) for r in (screen.get("who") or []) if _clean(r)],
                "expected_file": _page_file(route),
            })
            add(f"{name} page at {route}" + (f": {purpose}" if purpose else ""),
                "page", None, source="plan.screens")

    models: list[dict] = []
    archetype = str(pack.get("archetype") or "fullstack-crud")
    tables = [t for t in ((doc.get("database_design") or {}).get("tables") or [])
              if isinstance(t, dict)]
    have_user_table = any(str(t.get("table_name") or "").lower() in {"user", "users"}
                          for t in tables)

    if auth and not have_user_table:
        user_specs = [
            {"name": "name", "type": "String", "srs_type": "String", "references": None,
             "required": True, "unique": False, "primary_key": False, "default": None, "enum": []},
            {"name": "email", "type": "String", "srs_type": "String", "references": None,
             "required": True, "unique": True, "primary_key": False, "default": None, "enum": []},
            {"name": "role", "type": "String", "srs_type": "String", "references": None,
             "required": True, "unique": False, "primary_key": False, "default": None, "enum": []},
        ]
        models.append({"name": "User", "table": "users",
                       "fields": {s["name"]: s["type"] for s in user_specs},
                       "field_specs": user_specs, "readOnly": False})

    for table in tables:
        tname = _clean(table.get("table_name") or table.get("name") or "")
        if not tname:
            continue
        specs = []
        for f in (table.get("fields") or table.get("columns") or []):
            if not isinstance(f, dict):
                continue
            spec = _field_spec(f)
            if spec.get("name"):
                specs.append(spec)
        entity = _entity(tname)
        models.append({
            "name": entity,
            "table": tname,
            "description": _clean(table.get("description") or ""),
            "fields": {s["name"]: s["type"] for s in specs},
            "field_specs": specs,
            "readOnly": False,
        })

        # Derive CRUD only when the SRS has no explicit FRs.
        if archetype == "fullstack-crud" and not doc.get("functional_requirements"):
            label = tname.replace("_", " ")
            singular = label[:-1] if label.endswith("s") else label
            add(f"Users can add a new {singular}", "create", entity, source="derived.crud")
            add(f"Users can see a list of {label}", "list", entity, source="derived.crud")
            add(f"Users can edit an existing {singular}", "edit", entity, source="derived.crud")
            add(f"Users can delete a {singular}", "delete", entity, source="derived.crud")

    for user in plan.get("users") or []:
        if not isinstance(user, dict):
            continue
        role = _clean(user.get("role", ""))
        for duty in user.get("can_do") or []:
            duty = _clean(duty)
            if duty and role:
                add(f"{role}s can {duty[:1].lower()}{duty[1:]}", _kind_for(duty), None,
                    source="plan.user_duty", roles=[role])
    for feature in plan.get("features") or []:
        add(_clean(feature), _kind_for(feature), None, source="plan.feature")

    if auth:
        add("Users can sign in and sign out", "feature", "User", source="auth.contract")
        if any(re.search(r"sign[ -]?up|register", str(p.get("route") or "") + " " + str(p.get("name") or ""), re.I)
               for p in pages):
            add("Visitors can register the approved default user role with email and password",
                "feature", "User", source="auth.contract")
        add("Signed-out visitors cannot open protected pages", "feature", None,
            source="auth.contract")

    relationships = []
    for rel in ((doc.get("database_design") or {}).get("relationships") or []):
        if not isinstance(rel, dict):
            continue
        relationships.append({
            "from": _clean(rel.get("from") or rel.get("from_") or ""),
            "to": _clean(rel.get("to") or ""),
            "type": _clean(rel.get("type") or ""),
            "via": _clean(rel.get("via") or "") or None,
            "description": _clean(rel.get("description") or "") or None,
        })

    apis = []
    for ep in (doc.get("api_design") or []):
        if not isinstance(ep, dict):
            continue
        raw_path = _clean(ep.get("path") or ep.get("endpoint") or "")
        if not raw_path:
            continue
        path = _normal_route_pattern(raw_path)
        apis.append({
            "method": str(ep.get("method") or "GET").upper(),
            "path": path,
            "srs_path": raw_path,
            "description": _clean(ep.get("description") or ""),
            "auth_required": ep.get("auth_required"),
            "allowed_roles": [_clean(r) for r in (ep.get("allowed_roles") or []) if _clean(r)],
            "expected_file": _api_file(path),
        })

    roles = []
    for role in (doc.get("roles") or []):
        if not isinstance(role, dict):
            continue
        name = _clean(role.get("role_name") or role.get("role_key") or "")
        if name:
            roles.append({"name": name,
                          "key": _clean(role.get("role_key") or name.lower()),
                          "description": _clean(role.get("description") or "")})

    access = []
    for row in (doc.get("role_access_matrix") or []):
        if not isinstance(row, dict):
            continue
        who = _clean(row.get("role") or row.get("role_name") or "")
        if who:
            access.append({
                "role": who,
                "allowed_pages": [str(x) for x in (row.get("allowed_pages") or [])],
                "restricted_pages": [str(x) for x in (row.get("restricted_pages") or [])],
                "allowed_functions": [str(x) for x in (row.get("allowed_functions") or [])],
                "restricted_functions": [str(x) for x in (row.get("restricted_functions") or [])],
            })

    # Prefer role-complete journeys from the approved plan.
    roles_of = _roles_for_route(doc)
    workflows = []
    for w in (plan.get("workflows") or []):
        if not isinstance(w, dict):
            continue
        steps = [_clean(s) for s in (w.get("steps") or []) if _clean(s)]
        if not steps:
            continue
        who = _clean(w.get("who") or "")
        chain = []
        for step in steps:
            for route in _routes_for(step, pages, who, roles_of):
                if not chain or chain[-1] != route:
                    chain.append(route)
        workflows.append({
            "name": _clean(w.get("name") or "Journey"),
            "who": who or None,
            "steps": steps,
            "routes": chain,
            "source": "approved_plan",
        })
    for w in (doc.get("business_workflows") or []):
        if not isinstance(w, dict):
            continue
        name = _clean(w.get("workflow_name") or w.get("name") or "")
        steps = [_clean(s) for s in (w.get("steps") or []) if _clean(s)]
        if name and steps and not any(x["name"].lower() == name.lower() for x in workflows):
            workflows.append({"name": name, "who": None, "steps": steps,
                              "routes": [], "source": "srs.business_workflows"})

    acceptance = []
    for c in (doc.get("acceptance_criteria") or []):
        if isinstance(c, dict):
            text = _clean(c.get("criterion") or c.get("text") or "")
            if text:
                acceptance.append({"id": _clean(c.get("id") or "") or None,
                                   "criterion": text})
        elif _clean(c):
            acceptance.append({"id": None, "criterion": _clean(c)})

    validations = []
    for rule in (doc.get("validation_rules") or []):
        if isinstance(rule, dict) and _clean(rule.get("rule") or ""):
            validations.append({"field": _clean(rule.get("field") or "") or None,
                                "rule": _clean(rule.get("rule") or "")})

    page_roles = {}
    for p in pages:
        page_roles[str(p.get("name") or "").lower()] = list(p.get("allowed_roles") or [])
        page_roles[str(p.get("route") or "").lower()] = list(p.get("allowed_roles") or [])
    capability_seeds = []
    for r in [x for x in requirements if x.get("source_id")]:
        proof = r.get("proof") or {}
        role_names = []
        for pg in proof.get("pages") or []:
            for role in page_roles.get(str(pg).lower(), []):
                if role not in role_names:
                    role_names.append(role)
        capability_seeds.append({
            "id": "CAP-" + str(r["source_id"]),
            "requirement_id": r["source_id"],
            "requirement": r["text"],
            "module": r.get("module"),
            "priority": r.get("priority"),
            "roles": role_names,
            "pages": list(proof.get("pages") or []),
            "data": list(proof.get("tables") or []),
            "test_case": proof.get("test_case"),
            "e2e": bool(proof.get("pages")),
        })

    notifications = [x for x in (doc.get("notification_rules") or []) if isinstance(x, dict)]
    integrations = [x for x in (doc.get("integration_requirements") or []) if isinstance(x, dict)]
    reports = [x for x in (doc.get("reporting_requirements") or []) if isinstance(x, dict)]
    nfr = [x for x in (doc.get("non_functional_requirements") or [])]

    auth_contract = build_auth_contract(
        doc=doc, plan=plan, pages=pages, roles=roles, access=access, auth=bool(auth),
    )
    feature_contracts = build_feature_contracts(
        requirements=requirements, pages=pages, apis=apis, workflows=workflows,
        acceptance=acceptance,
    )

    handoff = {
        "handoff_version": HANDOFF_VERSION,
        "target_builder": "AgentForge",
        "appType": _clean(pack.get("app_label") or doc.get("system_category") or "Web application"),
        "app_name": _clean(((doc.get("app_summary") or {}).get("app_name")
                            if isinstance(doc.get("app_summary"), dict) else "")
                           or doc.get("project_name") or plan.get("app_name") or ""),
        "product_intent": _clean(plan.get("product_intent") or
                                 ((doc.get("app_summary") or {}).get("short_description")
                                  if isinstance(doc.get("app_summary"), dict) else "")),
        "requirements": requirements,
        "source_requirements": [r for r in requirements if r.get("source_id")],
        "capability_seeds": capability_seeds,
        "feature_contracts": feature_contracts,
        "models": models,
        "relationships": relationships,
        "pages": pages,
        "apis": apis,
        "roles": roles,
        "access": access,
        "workflows": workflows,
        "traceability": trace,
        "acceptance_criteria": acceptance,
        "validation_rules": validations,
        "notification_rules": notifications,
        "integration_requirements": integrations,
        "reporting_requirements": reports,
        "non_functional_requirements": nfr,
        "constraints": list(doc.get("constraints") or []),
        "assumptions": list(doc.get("assumptions") or []),
        "auth": bool(auth),
        "auth_contract": auth_contract,
        "invariants": [
            "Build the complete approved application; no placeholder pages, TODO features or inert controls.",
            "Every FR/source requirement must become an AgentForge capability with observable proof and exact implementing files.",
            "Every browser-visible capability must be covered by an end-to-end journey; synthesize a journey if the SRS omitted one.",
            "Every promised route must have a real page/handler and every navigation target must resolve; no orphan or dead routes.",
            "Enforce protected routes and role permissions on the server, not only by hiding UI.",
            "For Mongo ObjectId/reference fields, URL/form/session ids arrive as strings: validate and convert before querying, and serialize ObjectIds to strings at browser boundaries.",
            "A mutation is complete only when it persists the data and the next page/state visibly reflects the result.",
        ],
    }
    handoff["prompt"] = render_prompt(handoff, plan, auth=auth, srs_document=doc)
    return handoff


def refresh_handoff(handoff: dict, plan: dict, srs_document: dict) -> dict:
    """Rebuild an old stored handoff with the current V2 contract."""
    plan = dict(plan or {})
    try:
        from ..agents.plan_generator import _journeys_for_every_role
        plan["workflows"] = _journeys_for_every_role(plan)
    except Exception:
        pass
    auth = handoff.get("auth")
    if auth is None:
        auth = bool((srs_document or {}).get("protected_pages"))
    pack = {"app_label": handoff.get("appType") or
            (srs_document or {}).get("system_category") or "Web application"}
    fresh = build_handoff(plan=plan, srs_document=srs_document,
                          pack=pack, auth=bool(auth))
    return {**(handoff or {}), **fresh}


def rerender_prompt(handoff: dict, plan: dict, srs_document: dict) -> str:
    """Backward-compatible helper used by older callers."""
    return refresh_handoff(handoff, plan, srs_document).get("prompt", "")


def attach_handoff(srs: dict, plan: dict, pack: dict | None = None,
                   auth: bool = False) -> dict:
    """Put the handoff onto the SRS document, in place."""
    doc = srs.get("srs_document") or {}
    doc["builder_handoff"] = build_handoff(plan=plan, srs_document=doc,
                                           pack=pack, auth=auth)
    return srs


__all__ = ["build_handoff", "refresh_handoff", "render_prompt", "rerender_prompt",
           "attach_handoff", "KINDS", "ALLOWED_PACKAGES", "MAX_WORDS",
           "MAX_CHARS", "HANDOFF_VERSION"]
