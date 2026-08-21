"""Build a schema-ready SRS from the approved plan."""
from __future__ import annotations

import copy
from typing import Any

from .plan_srs_auth import normalize_account_policy
from .plan_srs_base import (
    _AUTH_TABLE_NAMES, _api_from_tables, _auth_on, _nfrs, _pages_from_plan, _requirements_from_plan,
    _role_matrix, _roles_from_plan, _rtm, _sentence, _tables_from_plan, _title,
    build_branding,
)

def build_srs_from_plan(*, project: dict, plan: dict, pack: dict | None = None,
                        session: dict | None = None, brief: str = "") -> dict:
    """A complete, schema-valid SRS containing only what the plan contains."""
    plan = plan or {}
    pack = pack or {}
    session = session or {}

    auth = _auth_on(pack, plan)
    archetype = str(pack.get("archetype") or "fullstack-crud")

    app_name = (str((session.get("answers", {}).get("app_name", {}) or {}).get("value") or "").strip()
                or str(project.get("title") or "").strip()
                or "Application")

    roles = _roles_from_plan(plan)
    account_policy = dict(plan.get("account_policy") or {})
    if auth:
        account_policy.setdefault("accounts_required", True)
        account_policy = normalize_account_policy(account_policy, roles)
    else:
        account_policy = {"accounts_required": False, "registration_mode": "none"}
    tables, relationships = _tables_from_plan(plan, auth)
    public_pages, protected_pages = _pages_from_plan(plan, auth, account_policy)
    frs = _requirements_from_plan(plan, tables, auth, archetype, account_policy, public_pages)

    devices = []
    entry = (session.get("answers") or {}).get("responsive_pwa")
    if entry:
        value = entry.get("value")
        devices = [str(v) for v in value] if isinstance(value, list) else [str(value)]

    branding = build_branding(session, pack, app_name)
    intent = str(plan.get("product_intent") or "").strip()

    modules = []
    for screen in plan.get("screens") or []:
        name = _title(screen.get("name", ""))
        if name and name not in modules:
            modules.append(name)
    for t in tables:
        name = _title(t["table_name"].replace("_", " "))
        if name not in modules:
            modules.append(name)

    ambiguities = [
        {"id": f"AMB-{i:03d}", "area": "Approved plan",
         "description": str(q.get("question", "")),
         "assumption_made": "Left open; to be settled before build.",
         "needs_clarification": bool(q.get("required"))}
        for i, q in enumerate(plan.get("open_questions") or [], start=1)
        if str(q.get("question", "")).strip()
    ]

    security = ["All traffic must be served over HTTPS.",
                "Input from the browser must be validated on the server before it is stored."]
    if auth:
        security += [
            "Passwords must never be stored or logged in plain text.",
            "A session must expire and be re-authenticated after a period of inactivity.",
            "Each request to a protected endpoint must be checked against the caller's role.",
        ]

    srs_document: dict[str, Any] = {
        "document_title": "Software Requirements Specification",
        "project_name": app_name,
        "version": project.get("current_version") or "1.0.0",
        "document_language": project.get("language", "English"),
        "system_category": str(pack.get("app_label") or "Web Application"),
        "prepared_for": "Approved plan build",
        "app_summary": {
            "app_name": app_name,
            "short_description": intent or (brief or project.get("raw_idea", ""))[:400],
            "business_goal": intent or f"To deliver {app_name} as described in the approved plan.",
            "target_users": [r["role_name"] for r in roles],
        },
        "app_type": {

            "key": str(pack.get("app_type") or "other"),
            "primary_type": str(pack.get("app_label") or "Web Application"),
            "archetype": archetype,
            "frontend_type": "Responsive web application",
            "backend_type": "REST API",
            "database_type": "Document database",
            "example_stack": {"frontend": "Next.js / React", "backend": "Next.js API routes",
                              "database": "MongoDB",
                              "auth": "Email + password session" if auth else "None"},
        },
        "authentication_requirement": (
            {
                "login_required": True,
                "sign_in_route": account_policy.get("sign_in_route") or "/login",
                "identity_fields": list(account_policy.get("sign_in_fields") or ["email", "password"]),
                "registration_fields": list(account_policy.get("registration_fields") or account_policy.get("sign_in_fields") or ["email", "password"]),
                "registration_mode": account_policy.get("registration_mode") or "admin_created",
                "self_registration": account_policy.get("registration_mode") == "open",
                "sign_up_route": account_policy.get("sign_up_route") if account_policy.get("registration_mode") == "open" else None,
                "registration_role": account_policy.get("registration_role") if account_policy.get("registration_mode") == "open" else None,
                "registration_roles": ([account_policy.get("registration_role")]
                                       if account_policy.get("registration_mode") == "open" and account_policy.get("registration_role") else []),
                "provisioning_role": account_policy.get("provisioning_role"),
                "account_management_route": account_policy.get("account_management_route"),
                "invitation_management_route": account_policy.get("invitation_management_route"),
                "invitation_accept_route": account_policy.get("invitation_accept_route"),
                "request_access_route": account_policy.get("request_access_route"),
                "access_review_route": account_policy.get("access_review_route"),
                "public_landing_pages_required": bool(public_pages),
                "multi_role_access_required": len(roles) > 1,
                "password_reset_required": bool(account_policy.get("password_reset_required", True)),
                "signed_out_protected_access": "redirect_to_sign_in",
                "wrong_role_access": "forbidden",
                "auth_transport": "provider_managed",
            }
            if auth else {"login_required": False, "self_registration": False}
        ),
        "roles": roles,
        "role_access_matrix": _role_matrix(roles, plan, public_pages, protected_pages) if auth else [],
        "public_pages": public_pages,
        "protected_pages": protected_pages,
        "main_modules": modules,
        "database_design": {"tables": tables, "relationships": relationships},
        "api_design": _api_from_tables(tables, auth, account_policy, plan, public_pages),
        "functional_requirements": frs,
        "non_functional_requirements": _nfrs(auth, devices),
        "business_workflows": [
            {"workflow_name": str(w.get("name", "Workflow")),
             "who": str(w.get("who") or "") or None,
             "steps": [str(s) for s in (w.get("steps") or [])]}
            for w in plan.get("workflows") or [] if w.get("steps")
        ],
        "validation_rules": [],
        "notification_rules": [],
        "security_requirements": security,
        "ui_ux_requirements": {
            "design_style": str(plan.get("look_and_feel") or "").strip(),
            "theme": branding["theme"],
            "mobile_support": "desktop" not in devices or len(devices) > 1,
            "pwa_support": "pwa" in devices,
            "required_components": _components_for(tables, auth, archetype),
        },
        "reporting_requirements": [],
        "integration_requirements": [],
        "assumptions": [str(a) for a in plan.get("assumptions") or []],
        "constraints": ["The system is built only from the approved plan; "
                        "anything outside it is a change request."],
        "ambiguities": ambiguities,
        "risk_priority": [],
        "acceptance_criteria": [
            {"id": f"AC-{i:03d}", "criterion": _sentence(f)}
            for i, f in enumerate(plan.get("features") or [], start=1)
        ],
        "requirement_traceability_matrix": _rtm(frs, tables, public_pages + protected_pages),
        "diagrams": [],

        "branding": branding,
        "approved_plan": copy.deepcopy(plan),

        "customer_notes": str(plan.get("customer_notes") or "").strip(),
    }
    return {"srs_document": srs_document}


def _components_for(tables: list[dict], auth: bool, archetype: str) -> list[str]:
    """UI parts this app actually needs, not the standard admin kit."""
    out = ["Navigation bar"]
    domain_tables = [t for t in tables if t["table_name"] not in _AUTH_TABLE_NAMES]
    if domain_tables and archetype == "fullstack-crud":
        out += ["Data table with search and filter", "Create / edit form",
                "Confirmation dialog", "Empty state"]
    if auth:
        out += ["Sign-in form", "Signed-in user menu"]
    if archetype == "landing-single-page":
        out += ["Hero section", "Section headings", "Enquiry form"]
    if archetype == "single-tool":
        out += ["Input panel", "Result display"]
    return out


