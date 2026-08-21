"""Merge customization back into the approved-plan SRS."""
from __future__ import annotations

import copy

from .plan_srs_base import _rtm, _sentence, _snake, _table_name, _title

def merge_pack_planned(skeleton: dict, pack: dict, plan: dict) -> dict:
    """Merge model enrichment without expanding the plan."""
    doc = copy.deepcopy(skeleton)["srs_document"]
    pack = pack or {}
    plan = plan or {}

    allowed_tables = {t["table_name"] for t in doc["database_design"]["tables"]}
    allowed_roles = {r["role_key"] for r in doc["roles"]}

    summary = pack.get("app_summary")
    if isinstance(summary, dict):
        for key in ("business_goal", "short_description"):
            value = str(summary.get(key) or "").strip()
            if value:
                doc["app_summary"][key] = value

    by_name = {t["table_name"]: t for t in doc["database_design"]["tables"]}
    for t in pack.get("tables") or []:
        if not isinstance(t, dict):
            continue
        name = _snake(str(t.get("table_name", "")))
        target = by_name.get(name) or by_name.get(_table_name(name))
        if not target or name not in allowed_tables and _table_name(name) not in allowed_tables:
            continue
        if str(t.get("description") or "").strip():
            target["description"] = str(t["description"]).strip()
        existing = {f["name"] for f in target["fields"]}
        for f in t.get("fields") or []:
            if not isinstance(f, dict):
                continue
            fname = _snake(str(f.get("name", "")))
            if not fname or fname in existing:
                continue
            existing.add(fname)
            target["fields"].append({"name": fname,
                                     "type": str(f.get("type") or "string"),
                                     "nullable": bool(f.get("nullable", True))})

    seen = {r["requirement"].strip().lower() for r in doc["functional_requirements"]}
    for fr in pack.get("functional_requirements") or []:
        if not isinstance(fr, dict):
            continue
        text = _sentence(fr.get("requirement", ""))
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        doc["functional_requirements"].append({
            "id": f"FR-{len(doc['functional_requirements']) + 1:03d}",
            "module": str(fr.get("module") or "Core Features"),
            "requirement": text,
            "priority": str(fr.get("priority") or "medium"),
            "allowed_roles": [str(r) for r in (fr.get("allowed_roles") or []) if str(r).strip()],
        })

    for nfr in pack.get("non_functional_requirements") or []:
        if not isinstance(nfr, dict):
            continue
        text = _sentence(nfr.get("requirement", ""))
        if text:
            doc["non_functional_requirements"].append({
                "id": f"NFR-{len(doc['non_functional_requirements']) + 1:03d}",
                "category": str(nfr.get("category") or "Quality"),
                "requirement": text,
            })

    for key in ("validation_rules", "notification_rules",
                "reporting_requirements", "integration_requirements"):
        value = pack.get(key)
        if isinstance(value, list) and value:
            doc[key] = value

    for key, prefix, required in (("risk_priority", "RISK", ("area", "risk", "severity",
                                                             "reason", "mitigation")),
                                  ("ambiguities", "AMB", ("area", "description",
                                                          "assumption_made"))):
        value = pack.get(key)
        if not isinstance(value, list) or not value:
            continue
        rows = []
        for item in value:
            if not isinstance(item, dict) or not all(str(item.get(f, "")).strip()
                                                     for f in required):
                continue
            rows.append({**item, "id": f"{prefix}-{len(rows) + 1:03d}"})
        if rows:
            doc[key] = rows

    if isinstance(pack.get("business_workflows"), list) and pack["business_workflows"]:
        doc["business_workflows"] = [
            {"workflow_name": str(w.get("workflow_name") or w.get("name") or "Workflow"),
             "who": str(w.get("who") or "") or None,
             "steps": [str(s) for s in (w.get("steps") or [])]}
            for w in pack["business_workflows"] if isinstance(w, dict) and w.get("steps")
        ]

    prompt = str((pack.get("branding") or {}).get("logo_image_prompt") or "").strip()
    if prompt and doc.get("branding", {}).get("logo_required"):
        doc["branding"]["logo_image_prompt"] = prompt

    doc["roles"] = [r for r in doc["roles"] if r["role_key"] in allowed_roles]
    doc["requirement_traceability_matrix"] = _rtm(
        doc["functional_requirements"],
        doc["database_design"]["tables"],
        doc["public_pages"] + doc["protected_pages"],
    )
    return {"srs_document": doc}


def effective_plan(doc: dict) -> dict:
    """A plan-shaped view of the specification as it stands NOW."""
    doc = doc or {}
    approved = doc.get("approved_plan") or {}

    allowed = {m.get("role"): (m.get("allowed_functions") or [])
               for m in (doc.get("role_access_matrix") or [])
               if isinstance(m, dict)}

    approved_can = {}
    for user in (approved.get("users") or []):
        if isinstance(user, dict) and user.get("role"):
            approved_can[str(user["role"]).casefold()] = [
                str(c) for c in (user.get("can_do") or []) if str(c).strip()]

    users = []
    for role in (doc.get("roles") or []):
        if not isinstance(role, dict):
            continue
        key = role.get("role_key")
        name = role.get("role_name") or key
        if not name:
            continue
        can = [str(f) for f in (allowed.get(key) or allowed.get(name) or [])]
        if not can:
            can = approved_can.get(str(name).casefold(), [])
        if not can and role.get("description"):

            can = [d.strip() for d in str(role["description"]).split(";")
                   if d.strip()]
        users.append({"role": str(name), "can_do": can})

    screens = []
    for page in ((doc.get("public_pages") or []) + (doc.get("protected_pages") or [])):
        if not isinstance(page, dict):
            continue
        name = page.get("page_name") or page.get("name")
        if not name:
            continue
        fns = [str(f) for f in (page.get("functions") or []) if str(f).strip()]
        allowed_roles = [str(r) for r in (page.get("allowed_roles") or [])]
        screens.append({
            "name": str(name),
            "route": str(page.get("route") or "/"),
            "purpose": fns[0] if fns else "",
            "who": allowed_roles or (["Visitor"] if page.get("login_required") is False else []),
        })

    records = []
    for table in ((doc.get("database_design") or {}).get("tables") or []):
        if not isinstance(table, dict):
            continue
        name = table.get("table_name") or table.get("name")
        if not name:
            continue
        keeps = [f.get("name") if isinstance(f, dict) else str(f)
                 for f in (table.get("fields") or [])]
        records.append({"name": str(name),
                        "keeps": [k for k in keeps if k
                                  and k not in ("id", "created_at", "updated_at")]})

    workflows = [
        {"name": str(w.get("workflow_name") or w.get("name") or "Workflow"),
         "who": str(w.get("who") or "") or None,
         "steps": [str(s) for s in (w.get("steps") or [])]}
        for w in (doc.get("business_workflows") or [])
        if isinstance(w, dict) and w.get("steps")
    ]

    features = [str(f) for f in (approved.get("features") or []) if str(f).strip()]

    auth = doc.get("authentication_requirement") or {}
    account_policy = dict(approved.get("account_policy") or {})
    if auth.get("login_required"):
        account_policy.update({
            "accounts_required": True,
            "sign_in_fields": list(auth.get("identity_fields") or []),
            "registration_fields": list(auth.get("registration_fields") or []),
            "registration_mode": auth.get("registration_mode") or "admin_created",
            "registration_role": auth.get("registration_role"),
            "provisioning_role": auth.get("provisioning_role"),
            "sign_in_route": auth.get("sign_in_route"),
            "sign_up_route": auth.get("sign_up_route"),
            "account_management_route": auth.get("account_management_route"),
            "invitation_management_route": auth.get("invitation_management_route"),
            "invitation_accept_route": auth.get("invitation_accept_route"),
            "request_access_route": auth.get("request_access_route"),
            "access_review_route": auth.get("access_review_route"),
            "password_reset_required": bool(auth.get("password_reset_required")),
        })
    else:
        account_policy = {"accounts_required": False, "registration_mode": "none"}

    return {
        "product_intent": approved.get("product_intent", ""),
        "look_and_feel": approved.get("look_and_feel", ""),
        "users": users or approved.get("users") or [],
        "screens": screens or approved.get("screens") or [],
        "records": records or approved.get("records") or [],
        "workflows": workflows or approved.get("workflows") or [],
        "features": features or approved.get("features") or [],
        "account_policy": account_policy,
    }


__all__ = ["build_srs_from_plan", "merge_pack_planned", "build_branding",
           "effective_plan", "_auth_on"]
