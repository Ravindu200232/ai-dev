"""Deterministic SRS composer."""
from __future__ import annotations

import re
from typing import Any

from .domains import GUEST_ROLE, SUPER_ADMIN, get_domain, universal_tables

_UNIVERSAL_TABLE_NAMES = {"users", "roles", "permissions", "audit_logs", "notifications"}


def _project_name(title: str, domain: dict) -> str:
    title = (title or "").strip()
    if not title:
        return f"AgentForge {domain['label']} Platform"

    return title if len(title) <= 60 else title[:57] + "..."


def _real_details(answers: list[dict], session: dict | None) -> dict:
    """The customer's own name, contacts and links, read from their answer."""
    from .real_details import parse_details
    blocks = []
    for a in answers or []:
        if str(a.get("question_key") or a.get("key") or "") != "real_details":
            continue
        value = a.get("value")
        blocks.append(" \n".join(str(x) for x in value) if isinstance(value, list)
                      else str(value or ""))
        if a.get("raw_text"):
            blocks.append(str(a["raw_text"]))
    stored = ((session or {}).get("answers", {}).get("real_details") or {})
    if stored.get("value"):
        blocks.append(str(stored["value"]))
    try:
        return parse_details("\n".join(b for b in blocks if b.strip()))
    except Exception:
        return {}


def _answer_texts(answers: list[dict], session: dict | None) -> list[str]:
    out: list[str] = []
    for a in answers or []:
        v = a.get("value")
        if isinstance(v, list):
            out.extend(str(x) for x in v)
        elif v is not None:
            out.append(str(v))
        if a.get("raw_text"):
            out.append(str(a["raw_text"]))
    return out


def _flags_from(brief: str, answer_texts: list[str]) -> dict[str, bool]:
    blob = (brief + " " + " ".join(answer_texts)).lower()

    def has(*words: str) -> bool:
        return any(w in blob for w in words)

    return {
        "payments": has("pay", "payment", "billing", "invoice", "checkout", "gateway"),
        "reports": has("report", "analytics", "dashboard", "insight", "kpi"),
        "notifications": has("notif", "email", "sms", "alert", "remind"),
        "uploads": has("upload", "file", "document", "image", "photo", "attachment"),
        "multilang": has("language", "multilingual", "sinhala", "tamil", "spanish", "bilingual", "i18n"),
        "mobile": has("mobile", "pwa", "android", "ios", "phone", "app"),
        "public_site": has("website", "landing", "public", "marketing", "seo"),
        "loyalty": has("loyalty", "points", "rewards", "membership"),
    }


def build_offline_srs(
    *,
    project: dict,
    answers: list[dict] | None = None,
    session: dict | None = None,
    brief: str = "",
) -> dict:
    domain_key = project.get("domain_key", "custom")
    domain = get_domain(domain_key)
    language = project.get("language", "English")
    answer_texts = _answer_texts(answers or [], session)
    flags = _flags_from(brief or project.get("raw_idea", ""), answer_texts)

    project_name = _project_name(project.get("title", ""), domain)
    roles = [dict(r) for r in domain["roles"]]
    modules = list(domain["modules"])
    tables = universal_tables() + [dict(t) for t in domain["tables"]]
    relationships = [
        {"from": "users.role_id", "to": "roles.id", "type": "many_to_one",
         "description": "Each user belongs to one role."},
        *domain["relationships"],
    ]

    public_pages = [
        {**p, "page_type": p.get("page_type", "public"), "login_required": False, "functions": p.get("functions", [])}
        for p in domain["public_pages"]
    ]
    protected_pages = [
        {**p, "login_required": True} for p in domain["protected_pages"]
    ]

    frs: list[dict] = []

    def add_fr(module: str, requirement: str, priority: str = "high") -> str:
        fid = f"FR-{len(frs) + 1:03d}"
        frs.append({"id": fid, "module": module, "requirement": requirement, "priority": priority})
        return fid

    add_fr("Public Website", "The system shall provide a public website describing the product and capturing inquiries.", "high")
    add_fr("Authentication", "The system shall authenticate users via email and password with password reset.", "high")
    add_fr("Authorization", "The system shall enforce role-based access control on every protected page and API endpoint.", "high")
    for mod in domain["modules"]:
        if mod in ("Public Website", "Authentication"):
            continue
        add_fr(mod, f"The system shall provide {mod} capabilities appropriate to the {domain['label']} domain, "
                    f"available to authorized roles.", "high" if mod not in ("Settings", "Notifications") else "medium")
    if flags["payments"]:
        add_fr("Payments", "The system shall accept online payments through a secure hosted payment gateway and record transactions.", "high")
    if flags["reports"]:
        add_fr("Reports", "The system shall generate filterable reports with PDF and Excel export.", "high")
    if flags["notifications"]:
        add_fr("Notifications", "The system shall send event-driven notifications via in-app, email, and optional SMS channels.", "medium")
    if flags["uploads"]:
        add_fr("File Management", "The system shall allow authorized users to upload, store, and download files/attachments.", "medium")
    if flags["multilang"]:
        add_fr("Localization", f"The system shall support multiple display languages (including {language}).", "medium")
    if flags["mobile"]:
        add_fr("PWA", "The system should support responsive, mobile-friendly PWA behaviour.", "medium")
    add_fr("Audit Logs", "The system shall record critical create/update/delete, login, and permission actions in audit logs.", "high")

    nfrs = [
        {"id": "NFR-001", "category": "Performance", "requirement": "Primary pages should load within 3 seconds (P95) under normal conditions."},
        {"id": "NFR-002", "category": "Security", "requirement": "Passwords must be hashed with a strong adaptive algorithm (bcrypt/argon2)."},
        {"id": "NFR-003", "category": "Security", "requirement": "All protected APIs must validate authentication and role permissions."},
        {"id": "NFR-004", "category": "Reliability", "requirement": "The system shall target 99.5% monthly availability."},
        {"id": "NFR-005", "category": "Usability", "requirement": "The UI must be responsive across desktop, tablet, and mobile."},
        {"id": "NFR-006", "category": "Maintainability", "requirement": "Code shall be modular by feature with documented APIs."},
        {"id": "NFR-007", "category": "Scalability", "requirement": "The system shall scale horizontally behind a load balancer."},
        {"id": "NFR-008", "category": "Auditability", "requirement": "Critical data changes must be traceable through audit logs."},
    ]
    for i, focus in enumerate(domain.get("nfr_focus", []), start=len(nfrs) + 1):
        cat = "Security" if any(w in focus.lower() for w in ("encrypt", "access", "pci", "audit", "compliance")) else "Reliability"
        nfrs.append({"id": f"NFR-{i:03d}", "category": cat, "requirement": focus})

    role_access = []
    for r in roles:
        rk = r["role_key"]
        if rk in ("super_admin",):
            allowed_pages, allowed_funcs, restricted = ["All Pages"], ["Full system access", "Manage users & permissions", "View audit logs", "System configuration"], []
        elif rk == "guest":
            allowed_pages = [p["page_name"] for p in public_pages]
            allowed_funcs = ["Browse public site", "Submit inquiry/contact form"]
            restricted = ["Access dashboards", "View private records"]
        else:
            allowed_pages = [p["page_name"] for p in protected_pages if rk in (p.get("allowed_roles") or [])] or [p["page_name"] for p in protected_pages[:1]]
            allowed_funcs = sorted({fn for p in protected_pages if rk in (p.get("allowed_roles") or []) for fn in p.get("functions", [])})[:8]
            restricted = ["Access super-admin settings", "Delete audit logs"]
        role_access.append({"role": rk, "allowed_pages": allowed_pages, "allowed_functions": allowed_funcs, "restricted_functions": restricted})

    api_design = [
        {"method": "POST", "path": "/api/auth/login", "description": "Authenticate and start a session.", "auth_required": False},
        {"method": "POST", "path": "/api/auth/register", "description": "Register a new account.", "auth_required": False},
        {"method": "GET", "path": "/api/auth/me", "description": "Current authenticated user.", "auth_required": True},
    ]
    for t in domain["tables"]:
        res = t["table_name"].replace("_", "-")
        api_design.append({"method": "GET", "path": f"/api/{res}", "description": f"List {t['table_name']}.", "auth_required": True})
        api_design.append({"method": "POST", "path": f"/api/{res}", "description": f"Create {t['table_name']} record.", "auth_required": True})
        api_design.append({"method": "GET", "path": f"/api/{res}/{{id}}", "description": f"Get a {t['table_name']} record.", "auth_required": True})
        api_design.append({"method": "PUT", "path": f"/api/{res}/{{id}}", "description": f"Update a {t['table_name']} record.", "auth_required": True})

    rtm = []
    for fr in frs:
        rel_tables = [t["table_name"] for t in domain["tables"] if fr["module"].split()[0].lower() in t["table_name"].lower()][:3]
        rtm.append({
            "requirement_id": fr["id"],
            "module": fr["module"],
            "pages": [p["page_name"] for p in protected_pages if fr["module"] in p.get("functions", []) or True][:2],
            "tables": rel_tables or ["users"],
            "test_case": f"TC-{fr['id'][3:]}",
        })

    confidence = float((project.get("classification") or {}).get("confidence", 0.6))
    ambiguities = []

    def add_amb(area, description, assumption, needs=True):
        ambiguities.append({"id": f"AMB-{len(ambiguities) + 1:03d}", "area": area, "description": description,
                            "assumption_made": assumption, "needs_clarification": needs})

    if confidence < 0.55:
        add_amb("Domain", "The business domain was inferred with low confidence from the idea.",
                f"Assumed a {domain['label']} system; confirm via questions.", True)
    if not flags["payments"]:
        add_amb("Payments", "It is unclear whether online payments are required.",
                "Assumed no online payment in v1; can be added via customization.", True)
    if not flags["multilang"]:
        add_amb("Localization", "Number of supported languages is unspecified.",
                f"Assumed a single language ({language}).", False)
    add_amb("Scale", "Expected concurrent users / data volume not provided.",
            "Assumed small-to-medium scale for v1 sizing.", False)

    assumptions = [
        f"The application is a {domain['app_type_primary']}.",
        "Standard email/password authentication with role-based access is acceptable.",
        "A relational or document database is acceptable for persistence.",
        "Deployment targets a modern cloud or container environment.",
    ]
    constraints = [
        "Must run on commodity cloud infrastructure.",
        "Must comply with applicable data-protection regulations for stored personal data.",
        "Initial release (v1.0) targets the core workflows listed above.",
    ]

    risks = []
    for i, rk in enumerate(domain.get("risks", []), start=1):
        risks.append({"id": f"RISK-{i:03d}", **rk})
    base = len(risks)
    if flags["payments"]:
        risks.append({"id": f"RISK-{base + 1:03d}", "area": "Payments", "risk": "Payment Processing",
                      "severity": "High", "reason": "Handling card data carries PCI-DSS obligations.",
                      "mitigation": "Use a hosted/tokenised gateway; never store raw card data."})
    risks.append({"id": f"RISK-{len(risks) + 1:03d}", "area": "Notifications", "risk": "Notification Reliability",
                  "severity": "Medium", "reason": "Email/SMS providers can fail or rate-limit.",
                  "mitigation": "Outbox pattern + retry queue + provider fallback."})

    notification_rules = list(domain.get("notifications", []))
    reporting = list(domain.get("reports", []))
    integrations = [dict(i) for i in domain.get("integrations", [])]
    if flags["payments"] and not any(i["type"] == "payment" for i in integrations):
        integrations.append({"name": "Payment Gateway", "type": "payment", "description": "Online payments.", "required": True})

    acceptance = [{"id": f"AC-{i:03d}", "criterion": c} for i, c in enumerate([
        "A user can register, log in, and is routed by role.",
        "Unauthorized users cannot access protected pages or APIs.",
        f"Core {domain['label']} workflows complete end-to-end.",
        "All critical actions are written to audit logs.",
        "Reports can be filtered and exported." if flags["reports"] else "Administrators can view operational dashboards.",
        "The application is usable on desktop and mobile screens.",
    ], start=1)]

    workflows = list(domain["workflows"])

    validation_rules = [
        {"field": "email", "rule": "Must be a valid, unique email address."},
        {"field": "password", "rule": "Minimum 8 characters with letters and numbers."},
        {"field": "amount", "rule": "Monetary amounts cannot be negative."},
        {"field": "status", "rule": "Status transitions must follow the defined state machine."},
    ]

    security = [
        "Enforce role-based access control on all protected pages and APIs.",
        "Hash passwords with a strong adaptive algorithm.",
        "Validate and sanitise all input on client and server.",
        "Protect against SQL/NoSQL injection, XSS, and CSRF.",
        "Use HTTPS/TLS in production.",
        "Record critical actions in audit logs.",
    ]
    for focus in domain.get("nfr_focus", []):
        security.append(focus)

    ui_ux = {
        "design_style": "Modern, clean, professional",
        "theme": "Light theme, rounded cards, soft borders, blue primary actions",
        "dashboard_layout": "Sidebar navigation, top bar, summary cards, data tables, filters, charts",
        "mobile_support": flags["mobile"] or True,
        "pwa_support": flags["mobile"],
        "required_components": ["Data tables with search/filter", "Modal forms", "Confirmation dialogs",
                                "Status badges", "Charts", "File upload", "Role-based sidebar", "Export buttons"],
    }

    srs_document = {
        "document_title": "Software Requirements Specification",
        "project_name": project_name,
        "version": project.get("current_version") or "1.0.0",
        "document_language": language,
        "system_category": domain["system_category"],
        "prepared_for": "Full App Build Requirement",
        "app_summary": {
            "app_name": project_name,
            "short_description": (project.get("raw_idea") or "").strip()[:400] or f"A {domain['label']} platform.",
            "business_goal": f"To digitise and streamline {domain['label']} operations end-to-end.",
            "target_users": [r["role_name"] for r in roles],
        },
        "app_type": {
            "primary_type": domain["app_type_primary"],
            "supported_types": ["Public Landing Website", "SPA Web Application"] + (["PWA"] if flags["mobile"] else []),
            "frontend_type": "Responsive SPA",
            "backend_type": "REST API",
            "database_type": "Relational or document database",
            "example_stack": {"frontend": "Next.js / React", "backend": "Node.js / FastAPI", "database": "PostgreSQL / MongoDB", "auth": "JWT / Session"},
        },
        "authentication_requirement": {
            "login_required": True,
            "public_landing_pages_required": True,
            "multi_role_access_required": True,
            "password_reset_required": True,
            "email_verification_required": False,
            "two_factor_authentication_optional": True,
        },
        "roles": roles,
        "role_access_matrix": role_access,
        "public_pages": public_pages,
        "protected_pages": protected_pages,
        "main_modules": modules,
        "database_design": {
            "data_type_standards": {
                "uuid": "Unique identifier", "string": "Short text", "text": "Long text",
                "integer": "Whole number", "decimal": "Money/decimal", "boolean": "True/false",
                "date": "Date only", "datetime": "Date and time", "enum": "Fixed allowed values",
                "json": "Structured object", "foreign_key": "Reference to another table id",
            },
            "tables": tables,
            "relationships": relationships,
        },
        "api_design": api_design,
        "functional_requirements": frs,
        "non_functional_requirements": nfrs,
        "business_workflows": workflows,
        "validation_rules": validation_rules,
        "notification_rules": notification_rules,
        "security_requirements": security,
        "ui_ux_requirements": ui_ux,
        "reporting_requirements": reporting,
        "integration_requirements": integrations,
        "assumptions": assumptions,
        "constraints": constraints,
        "ambiguities": ambiguities,
        "risk_priority": risks,
        "acceptance_criteria": acceptance,
        "requirement_traceability_matrix": rtm,
        "diagrams": [],
    }
    apply_real_details(srs_document, answers or [], session)
    return {"srs_document": srs_document}


import copy as _copy
import re as _re


def _clean_field(fld: Any) -> dict | None:
    if not isinstance(fld, dict) or not fld.get("name"):
        return None
    out: dict[str, Any] = {"name": str(fld["name"]), "type": str(fld.get("type", "string"))}
    for k in ("primary_key", "nullable", "unique"):
        if k in fld:
            out[k] = bool(fld[k])
    if fld.get("references"):
        out["references"] = str(fld["references"])
    if isinstance(fld.get("values"), list) and fld["values"]:
        out["values"] = [str(v) for v in fld["values"]]
    if "default" in fld and fld["default"] is not None:
        out["default"] = fld["default"]
    return out


def _clean_table(t: Any) -> dict | None:
    if not isinstance(t, dict) or not t.get("table_name"):
        return None
    name = _re.sub(r"[^a-z0-9_]", "_", str(t["table_name"]).lower().strip())
    if not name:
        return None
    fields = [c for c in (_clean_field(f) for f in (t.get("fields") or [])) if c]
    if not any(f.get("primary_key") for f in fields):
        fields.insert(0, {"name": "id", "type": "uuid", "primary_key": True, "nullable": False})
    return {"table_name": name, "description": str(t.get("description", "")), "fields": fields}


def _derive_api_design(domain_tables: list[dict]) -> list[dict]:
    api = [
        {"method": "POST", "path": "/api/auth/login", "description": "Authenticate and start a session.", "auth_required": False},
        {"method": "POST", "path": "/api/auth/register", "description": "Register a new account.", "auth_required": False},
        {"method": "GET", "path": "/api/auth/me", "description": "Current authenticated user.", "auth_required": True},
    ]
    for t in domain_tables:
        res = t["table_name"].replace("_", "-")
        api += [
            {"method": "GET", "path": f"/api/{res}", "description": f"List {t['table_name']}.", "auth_required": True},
            {"method": "POST", "path": f"/api/{res}", "description": f"Create {t['table_name']} record.", "auth_required": True},
            {"method": "GET", "path": f"/api/{res}/{{id}}", "description": f"Get a {t['table_name']} record.", "auth_required": True},
            {"method": "PUT", "path": f"/api/{res}/{{id}}", "description": f"Update a {t['table_name']} record.", "auth_required": True},
        ]
    return api


def _derive_rtm(frs: list[dict], domain_tables: list[dict], protected_pages: list[dict]) -> list[dict]:
    table_names = [t["table_name"] for t in domain_tables]
    page_names = [p["page_name"] for p in protected_pages]
    rtm = []
    for fr in frs:
        words = {w.lower() for w in _re.split(r"\W+", fr.get("module", ""))}
        tbls = [tn for tn in table_names if any(w and w in tn for w in words)][:3] or (table_names[:1] or ["users"])
        rtm.append({
            "requirement_id": fr["id"], "module": fr["module"],
            "pages": page_names[:2], "tables": tbls, "test_case": f"TC-{fr['id'][3:]}",
        })
    return rtm



def apply_real_details(doc: dict, answers=None, session=None) -> dict:
    """Apply customer details without inventing values."""
    if not isinstance(doc, dict):
        return doc
    real = _real_details(answers or [], session)
    if not real:
        return doc
    doc.setdefault("app_summary", {})["organisation"] = real
    if real.get("name"):
        doc["app_summary"].setdefault("app_name", real["name"])
    contact = []
    if real.get("email"):
        contact.append(f"email {real['email']}")
    if real.get("phone"):
        contact.append(f"phone {real['phone']}")
    if real.get("address"):
        contact.append(f"address {real['address']}")
    for kind, url in (real.get("links") or {}).items():
        contact.append(f"{kind} {url[0] if isinstance(url, list) else url}")
    if contact:
        doc.setdefault("integration_requirements", []).append({
            "name": "Real contact details",
            "type": "content",
            "description": ("Show these exactly as given, and do not "
                            "substitute placeholders: " + "; ".join(contact)),
        })
    return doc


def _derive_role_matrix(roles: list[dict], public_pages: list[dict], protected_pages: list[dict]) -> list[dict]:
    out = []
    for r in roles:
        rk = r["role_key"]
        if rk == "super_admin":
            out.append({"role": rk, "allowed_pages": ["All Pages"],
                        "allowed_functions": ["Full system access", "Manage users & permissions", "View audit logs", "System configuration"],
                        "restricted_functions": []})
        elif rk == "guest":
            out.append({"role": rk, "allowed_pages": [p["page_name"] for p in public_pages],
                        "allowed_functions": ["Browse public site", "Submit inquiry/contact form"],
                        "restricted_functions": ["Access dashboards", "View private records"]})
        else:
            pages = [p["page_name"] for p in protected_pages if rk in (p.get("allowed_roles") or [])]
            funcs = sorted({fn for p in protected_pages if rk in (p.get("allowed_roles") or []) for fn in p.get("functions", [])})[:8]
            if not pages:
                pages = [protected_pages[0]["page_name"]] if protected_pages else []
            out.append({"role": rk, "allowed_pages": pages, "allowed_functions": funcs,
                        "restricted_functions": ["Access super-admin settings", "Delete audit logs"]})
    return out


def _list_or(default: list, value: Any) -> list:
    return value if isinstance(value, list) and value else default


def merge_pack(skeleton: dict, pack: dict, answers=None, session=None) -> dict:
    """Overlay an LLM enrichment pack onto the fixed skeleton."""
    doc = _copy.deepcopy(skeleton)["srs_document"]
    pack = pack or {}

    if isinstance(pack.get("system_category"), str) and pack["system_category"].strip():
        doc["system_category"] = pack["system_category"].strip()

    aps = pack.get("app_summary")
    if isinstance(aps, dict):
        for k in ("business_goal", "short_description"):
            if isinstance(aps.get(k), str) and aps[k].strip():
                doc["app_summary"][k] = aps[k].strip()
        if isinstance(aps.get("target_users"), list) and aps["target_users"]:
            doc["app_summary"]["target_users"] = [str(u) for u in aps["target_users"]][:14]

    raw_roles = pack.get("roles")
    if isinstance(raw_roles, list):
        clean_roles = [
            {"role_key": _re.sub(r"[^a-z0-9_]", "_", str(r["role_key"]).lower()),
             "role_name": str(r.get("role_name", r["role_key"])), "description": str(r.get("description", ""))}
            for r in raw_roles if isinstance(r, dict) and r.get("role_key")
        ]
        if len(clean_roles) >= 2:
            keys = {r["role_key"] for r in clean_roles}
            if "super_admin" not in keys:
                clean_roles.insert(0, dict(SUPER_ADMIN))
            if "guest" not in keys:
                clean_roles.append(dict(GUEST_ROLE))
            doc["roles"] = clean_roles
    roles = doc["roles"]
    doc["app_summary"].setdefault("target_users", [r["role_name"] for r in roles])

    # Whatever real details the customer gave, kept verbatim.
    real = _real_details(answers or [], session)
    if real:
        doc["app_summary"]["organisation"] = real
        if real.get("name"):
            doc["app_summary"].setdefault("app_name", real["name"])
        contact = []
        if real.get("email"):
            contact.append(f"email {real['email']}")
        if real.get("phone"):
            contact.append(f"phone {real['phone']}")
        if real.get("address"):
            contact.append(f"address {real['address']}")
        for kind, url in (real.get("links") or {}).items():
            first = url[0] if isinstance(url, list) else url
            contact.append(f"{kind} {first}")
        if contact:
            reqs = doc.setdefault("integration_requirements", [])
            reqs.append({
                "name": "Real contact details",
                "type": "content",
                "description": ("Show these exactly as given, and do not "
                                "substitute placeholders: " + "; ".join(contact)),
            })

    mods = pack.get("main_modules")
    if isinstance(mods, list) and len(mods) >= 3:
        seen, clean = set(), []
        for m in mods:
            m = str(m).strip()
            if m and m.lower() not in seen:
                seen.add(m.lower())
                clean.append(m)
        doc["main_modules"] = clean

    pack_tables = [c for c in (_clean_table(t) for t in (pack.get("tables") or [])) if c]
    if len(pack_tables) >= 2:
        universal = universal_tables()
        univ_names = {t["table_name"] for t in universal}
        seen_t, domain_tables = set(), []
        for t in pack_tables:
            if t["table_name"] in univ_names or t["table_name"] in seen_t:
                continue
            seen_t.add(t["table_name"])
            domain_tables.append(t)
        doc["database_design"]["tables"] = universal + domain_tables
        rels = [{"from": "users.role_id", "to": "roles.id", "type": "many_to_one",
                 "description": "Each user belongs to one role."}]
        for r in (pack.get("relationships") or []):
            if isinstance(r, dict) and r.get("from") and r.get("to"):
                rels.append({"from": str(r["from"]), "to": str(r["to"]),
                             "type": str(r.get("type", "one_to_many")), "description": str(r.get("description", ""))})
        doc["database_design"]["relationships"] = rels

    tables = doc["database_design"]["tables"]
    domain_tables = [t for t in tables if t["table_name"] not in _UNIVERSAL_TABLE_NAMES]

    frs: list[dict] = []
    def _add_fr(module, requirement, priority="high"):
        frs.append({"id": f"FR-{len(frs) + 1:03d}", "module": module, "requirement": requirement, "priority": priority})
    _add_fr("Public Website", "The system shall provide a public website describing the product and capturing inquiries.")
    _add_fr("Authentication", "The system shall authenticate users via email and password with password reset.")
    _add_fr("Authorization", "The system shall enforce role-based access control on every protected page and API endpoint.")
    seen_req = {f["requirement"].lower() for f in frs}
    for fr in (pack.get("functional_requirements") or []):
        if not isinstance(fr, dict):
            continue
        req = str(fr.get("requirement", "")).strip()
        if not req or req.lower() in seen_req:
            continue
        seen_req.add(req.lower())
        pr = fr.get("priority")
        _add_fr(str(fr.get("module", "General")), req, pr if pr in ("high", "medium", "low") else "medium")
    _add_fr("Audit Logs", "The system shall record critical create/update/delete, login, and permission actions in audit logs.", "high")
    doc["functional_requirements"] = frs

    nfrs = list(doc.get("non_functional_requirements", []))
    for nf in (pack.get("non_functional_requirements") or []):
        if isinstance(nf, dict) and str(nf.get("requirement", "")).strip():
            nfrs.append({"category": str(nf.get("category", "Quality")), "requirement": str(nf["requirement"]).strip()})
    for i, n in enumerate(nfrs, start=1):
        n["id"] = f"NFR-{i:03d}"
    doc["non_functional_requirements"] = nfrs

    wfs = pack.get("business_workflows")
    if isinstance(wfs, list) and wfs:
        doc["business_workflows"] = [
            {"workflow_name": str(w.get("workflow_name", "Workflow")), "steps": [str(s) for s in (w.get("steps") or [])]}
            for w in wfs if isinstance(w, dict) and w.get("steps")
        ] or doc["business_workflows"]

    doc["validation_rules"] = _list_or(doc["validation_rules"], [
        {"field": str(v.get("field", "")), "rule": str(v.get("rule", ""))}
        for v in (pack.get("validation_rules") or []) if isinstance(v, dict) and v.get("rule")])
    doc["notification_rules"] = _list_or(doc["notification_rules"], [
        {"event": str(n.get("event", "")), "recipients": [str(x) for x in (n.get("recipients") or [])],
         "channels": [str(x) for x in (n.get("channels") or [])]}
        for n in (pack.get("notification_rules") or []) if isinstance(n, dict) and n.get("event")])
    doc["reporting_requirements"] = _list_or(doc["reporting_requirements"], [
        {"report_name": str(r.get("report_name", "")), "filters": [str(x) for x in (r.get("filters") or [])],
         "exports": [str(x) for x in (r.get("exports") or ["PDF", "Excel"])]}
        for r in (pack.get("reporting_requirements") or []) if isinstance(r, dict) and r.get("report_name")])
    ints = [
        {"name": str(i.get("name", "")), "type": str(i.get("type", "integration")),
         "description": str(i.get("description", "")), "required": bool(i.get("required", False))}
        for i in (pack.get("integration_requirements") or []) if isinstance(i, dict) and i.get("name")]
    doc["integration_requirements"] = _list_or(doc["integration_requirements"], ints)
    secs = [str(s) for s in (pack.get("security_requirements") or []) if isinstance(s, str) and s.strip()]
    if secs:
        doc["security_requirements"] = secs

    risks = pack.get("risk_priority")
    if isinstance(risks, list) and risks:
        doc["risk_priority"] = [
            {"id": f"RISK-{i:03d}", "area": str(r.get("area", "General")), "risk": str(r.get("risk", "Risk")),
             "severity": str(r.get("severity", "Medium")), "reason": str(r.get("reason", "")), "mitigation": str(r.get("mitigation", ""))}
            for i, r in enumerate(risks, start=1) if isinstance(r, dict)]

    ambs = pack.get("ambiguities")
    if isinstance(ambs, list) and ambs:
        doc["ambiguities"] = [
            {"id": f"AMB-{i:03d}", "area": str(a.get("area", "General")), "description": str(a.get("description", "")),
             "assumption_made": str(a.get("assumption_made", "")), "needs_clarification": bool(a.get("needs_clarification", True))}
            for i, a in enumerate(ambs, start=1) if isinstance(a, dict)]

    doc["role_access_matrix"] = _derive_role_matrix(roles, doc.get("public_pages", []), doc.get("protected_pages", []))
    doc["api_design"] = _derive_api_design(domain_tables)
    doc["requirement_traceability_matrix"] = _derive_rtm(frs, domain_tables, doc.get("protected_pages", []))
    doc["diagrams"] = []
    apply_real_details(doc, answers, session)
    return {"srs_document": doc}
