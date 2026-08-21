"""Deterministic plan assembled from interview answers."""
from __future__ import annotations

import json
import re

from ..knowledge import app_types as catalog
from ..knowledge import topics

def _ans(session: dict, key: str, default=None):
    return topics.ans(session, key, default)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _title(text: str) -> str:
    text = str(text or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else text


_PAGE_PURPOSE = {
    "dashboard": "The first thing staff see, with the day's figures",
    "entity_crud": "Where these records are listed, added and edited",
    "report": "Where the numbers are pulled together for printing or export",
    "settings": "Where the app is configured",
    "marketing": "The public page a visitor lands on",
    "auth": "Where people sign in",
    "catalog": "A browsable list",
    "detail": "The full view of one item",
    "custom": "The main working screen",
}


def _role_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


_PRIVILEGED_ROLE_WORDS = ("admin", "manager", "owner", "staff", "cashier", "operator", "moderator")


def _identity_contract(session: dict) -> tuple[list[str], list[str]]:
    """Split sign-in fields from registration-only profile fields."""
    raw = _as_list(_ans(session, "auth_identity"))
    if not raw:
        return ["email", "password"], ["full_name", "email", "password"]

    sign_in = []
    register = []
    for field in raw:
        key = _role_key(field)
        if key in {"full_name", "name"}:
            if "full_name" not in register:
                register.append("full_name")
            continue
        normalized = "phone" if key in {"phone_number", "mobile", "mobile_number"} else key
        if normalized and normalized not in sign_in:
            sign_in.append(normalized)
        if normalized and normalized not in register:
            register.append(normalized)
    if "password" not in sign_in:
        sign_in.append("password")
    if "password" not in register:
        register.append("password")
    return sign_in, register


def _safe_signup_role(role: str) -> bool:
    key = _role_key(role)
    return bool(key and not any(word in key for word in _PRIVILEGED_ROLE_WORDS))


def _account_policy(session: dict, pack: dict, users: list[dict]) -> dict:
    auth = str(pack.get("auth_policy") or "") != "disabled" and any(
        _role_key(u.get("role")) not in {"visitor", "guest", "public", "anonymous", "everyone"}
        for u in users
    )
    if not auth:
        return {"accounts_required": False, "registration_mode": "none"}

    sign_fields, registration_fields = _identity_contract(session)
    mode = str(_ans(session, "account_creation") or _ans(session, "saas_signup") or "").strip()
    if not mode:
        has_register = any(
            re.search(r"register|sign[ -]?up", str(p.get("name", "")) + " " + str(p.get("route", "")), re.I)
            for p in (pack.get("pages") or []) if isinstance(p, dict)
        )
        mode = "open" if has_register else "admin_created"

    role = str(_ans(session, "signup_role") or "").strip()
    if mode == "open" and role and not _safe_signup_role(role):
        role = ""
    if mode == "open" and not role:
        privileged = ("admin", "manager", "owner", "staff", "cashier", "operator")
        for user in users:
            key = _role_key(user.get("role"))
            if key and not any(x in key for x in privileged):
                role = str(user.get("role") or "").strip()
                break
        if not role:
            mode = "admin_created"

    sign_in_route = next((str(p.get("route")) for p in (pack.get("pages") or [])
                          if isinstance(p, dict) and re.search(r"login|sign[ -]?in", str(p.get("name", "")), re.I)), "/login")
    sign_up_route = None
    if mode == "open":
        sign_up_route = next((str(p.get("route")) for p in (pack.get("pages") or [])
                              if isinstance(p, dict) and re.search(r"register|sign[ -]?up", str(p.get("name", "")), re.I)), "/register")

    provisioning_role = None
    if mode in {"admin_created", "invite", "request"}:
        for user in users:
            candidate = str(user.get("role") or "").strip()
            if any(word in _role_key(candidate) for word in _PRIVILEGED_ROLE_WORDS):
                provisioning_role = candidate
                break
        if not provisioning_role:
            provisioning_role = next((str(u.get("role") or "").strip() for u in users
                                      if _role_key(u.get("role")) not in {"visitor", "guest", "public"}), None)

    return {
        "accounts_required": True,
        "sign_in_fields": sign_fields,
        "registration_fields": registration_fields,
        "registration_mode": mode,
        "registration_role": _title(role) if role else None,
        "provisioning_role": _title(provisioning_role) if provisioning_role else None,
        "sign_in_route": sign_in_route,
        "sign_up_route": sign_up_route,
        "account_management_route": "/admin/users" if mode == "admin_created" else None,
        "invitation_management_route": "/admin/invitations" if mode == "invite" else None,
        "invitation_accept_route": "/accept-invite" if mode == "invite" else None,
        "request_access_route": "/request-access" if mode == "request" else None,
        "access_review_route": "/admin/access-requests" if mode == "request" else None,
        "password_reset_required": False,
    }


def _screen_audience(page: dict, users: list[dict], pack: dict) -> list[str]:
    """Use domain/page evidence before falling back to all signed-in roles."""
    name = str(page.get("name") or "").strip()
    route = str(page.get("route") or "").strip()
    ptype = str(page.get("type") or "").lower()
    selected = {_role_key(u.get("role")): str(u.get("role")) for u in users}

    for src in pack.get("domain_public_pages") or []:
        if not isinstance(src, dict):
            continue
        if route == str(src.get("route") or "") or name.casefold() == str(src.get("page_name") or "").casefold():
            return ["Visitor"]
    for src in pack.get("domain_protected_pages") or []:
        if not isinstance(src, dict):
            continue
        if route != str(src.get("route") or "") and name.casefold() != str(src.get("page_name") or "").casefold():
            continue
        roles = [selected[k] for k in (_role_key(r) for r in src.get("allowed_roles") or []) if k in selected]
        if roles:
            return roles

    if ptype in {"marketing", "catalog", "detail", "auth"}:
        return ["Visitor"]

    words = {w for w in re.findall(r"[a-z]{4,}", (name + " " + str(page.get("purpose") or "")).lower())}
    matched = []
    for user in users:
        duties = " ".join(str(x) for x in user.get("can_do") or []).lower()
        if any(w in duties for w in words):
            matched.append(str(user.get("role")))
    if matched:
        return matched
    return [str(u.get("role")) for u in users if _role_key(u.get("role")) not in {"visitor", "guest", "public"}] or ["Visitor"]


def build_offline_plan(*, project: dict, session: dict, brief: str = "") -> dict:
    """A complete plan from the interview answers alone."""
    pack = session.get("pack") or catalog.build_pack(
        session.get("app_type") or catalog.DEFAULT_APP_TYPE,
        brief or project.get("raw_idea", ""),
    )
    answers = session.get("answers") or {}
    app_name = str(_ans(session, "app_name") or project.get("title") or "The app").strip()
    domain_label = str(pack.get("domain_label") or "").strip()

    idea = str(project.get("raw_idea") or "").strip()
    opening = f"{app_name} is a {pack.get('app_label', 'web application')}"

    if domain_label and pack.get("domain_confidence", 0) >= 0.6:
        opening += f" for {domain_label.lower()}"
    outcome = str(_ans(session, "core_outcome") or "").strip()
    intent = f"{opening}. {idea}".strip()
    if outcome:
        intent = f"{intent} Primary success outcome: {outcome}"

    users: list[dict] = []
    for role in topics.roles_list(session):
        can_do = _as_list(_ans(session, f"role_functions:{role}"))
        users.append({"role": _title(role), "can_do": can_do})
    if not users:
        page_funcs = _as_list(_ans(session, "page_functions"))
        users.append({"role": "Visitor",
                      "can_do": page_funcs or ["Browse the site", "Get in touch"]})

    screens: list[dict] = []
    for page in pack.get("pages") or []:
        screens.append({
            "name": page.get("name", "Page"),
            "route": page.get("route", "/"),
            "purpose": _PAGE_PURPOSE.get(str(page.get("type", "")),
                                         "A screen in the app")
                       + f" ({page.get('route', '/')}).",
            "who": _screen_audience(page, users, pack),
        })
    for section in _as_list(_ans(session, "sections")):
        screens.append({"name": _title(section),
                        "route": "/",
                        "purpose": "A section of the single page.",
                        "who": ["Visitor"]})

    records: list[dict] = []
    for table in topics.tables_list(session):
        keeps = [_title(f) for f in _as_list(_ans(session, f"table_entities:{table}"))]
        records.append({"name": _title(table), "keeps": keeps})
    for field_group in ("lead_fields", "tool_inputs"):
        fields = _as_list(_ans(session, field_group))
        if fields:
            records.append({"name": "Enquiry" if field_group == "lead_fields" else "Entry",
                            "keeps": [_title(f) for f in fields]})

    by_name = {str(t.get("table_name") or "").lower(): t
               for t in (pack.get("domain_tables") or []) if isinstance(t, dict)}
    for record in records:
        if record["keeps"]:
            continue
        known = by_name.get(record["name"].lower().replace(" ", "_"))
        fields = [f.get("name") if isinstance(f, dict) else str(f)
                  for f in ((known or {}).get("fields") or [])]
        record["keeps"] = [_title(f) for f in fields
                           if f and f not in ("id", "created_at", "updated_at")]

    workflows = [
        {"name": w.get("workflow_name", "Workflow"), "steps": list(w.get("steps") or [])}
        for w in (pack.get("domain_workflows") or [])
    ]
    if outcome:
        workflows.insert(0, {"name": "Primary success path", "steps": [outcome]})

    feature_topics = (
        "role_functions", "page_functions", "offerings", "pos_payments",
        "pos_receipt", "pos_stock_rules", "saas_plans", "store_payments",
        "store_fulfilment", "dash_metrics", "dash_exports", "blog_workflow",
        "blog_organisation", "blog_engagement", "tool_outputs",
        "contact_channels", "social_proof",
    )
    features: list[str] = []
    for key, entry in answers.items():
        if key.split(":", 1)[0] not in feature_topics:
            continue
        for item in _as_list(entry.get("value")):
            label = _title(item)
            if label not in features:
                features.append(label)
    for feat in pack.get("features") or []:
        if feat not in features:
            features.append(feat)

    theme = _ans(session, "theme_type") or "light"
    palette = _ans(session, "color_palette") or pack.get("palette_name", "indigo")
    devices = _as_list(_ans(session, "responsive_pwa")) or ["responsive"]
    look = (f"A {theme} theme on a {str(palette).replace('_', ' ')} palette, "
            f"built for {', '.join(devices)}.")

    assumptions: list[str] = []
    if pack.get("auth_policy") == "required" and not topics.roles_list(session):
        assumptions.append("People will need to log in, with a single admin role.")
    if pack.get("auth_policy") == "disabled":
        assumptions.append("There is no login — everything on the site is public.")
    if not records:
        assumptions.append("Nothing is stored between visits beyond enquiries.")

    notes = customer_notes(session)
    for item in split_notes(notes):
        if item not in features:
            features.append(item)

    account_policy = _account_policy(session, pack, users)

    return {

        "app_name": app_name,
        "product_intent": intent,
        "users": users,
        "screens": screens,
        "records": records,
        "workflows": workflows,
        "features": features,
        "account_policy": account_policy,
        "look_and_feel": look,
        "assumptions": assumptions,
        "open_questions": [],
        "customer_notes": notes,
    }


def customer_notes(session: dict) -> str:
    """Whatever they wrote in the free-text box at the end of the interview."""
    entry = (session.get("answers") or {}).get("extra_notes") or {}
    for field in ("custom_text", "value", "text"):
        text = entry.get(field)
        if isinstance(text, list):
            text = " ".join(str(t) for t in text)
        text = str(text or "").strip()
        if text:
            return text
    return ""


def split_notes(notes: str) -> list[str]:
    """One free-text paragraph as separate things to build."""
    if not notes.strip():
        return []
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", notes):
        for part in re.split(r",\s*(?:and\s+)?|\s+and\s+(?=[a-z])", sentence):
            item = _title(part.strip(" .;,"))
            if len(item) > 3 and item not in out:
                out.append(item)
    return out


def _answers_digest(session: dict) -> str:
    """What they were asked, and what they said back."""
    by_key = {q.get("id"): q for q in (session.get("questions") or [])
              if isinstance(q, dict) and q.get("id")}
    lines = []
    for key, entry in (session.get("answers") or {}).items():
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)

        question = (by_key.get(key) or {}).get("question") or key
        lines.append(f"- {question} → {value}")

        typed = str(entry.get("custom_text") or entry.get("text") or "").strip()
        if typed and typed not in str(value):
            lines.append(f"    they typed: {typed}")
        for att in (entry.get("attachments") or []):
            text = str((att or {}).get("text") or "").strip()
            if text:
                name = str((att or {}).get("filename") or "a file")
                lines.append(f"    they attached {name}: {text}")

        why = str((by_key.get(key) or {}).get("why_needed") or "").strip()
        if why and not typed:
            lines.append(f"    (asked because: {why})")
    return "\n".join(lines) or "(nothing recorded)"


def _what_nobody_asked(coverage: dict | None) -> str:
    """The areas the interview never covered, named rather than scored."""
    if not isinstance(coverage, dict):
        return ""
    critical = [str(a) for a in (coverage.get("critical_missing") or [])]
    optional = [str(a) for a in (coverage.get("optional_missing") or [])]
    other = [str(a) for a in (coverage.get("missing") or [])
             if a not in critical and a not in optional]
    if not (critical or optional or other):
        return ""

    parts = ["NOBODY ASKED ABOUT THESE — the interview never covered them:"]
    if critical:
        parts.append("  important: " + ", ".join(critical))
    if optional or other:
        parts.append("  minor: " + ", ".join(optional + other))
    parts.append(
        "For each one, either state plainly in `assumptions` what you decided "
        "on their behalf, or raise it in `open_questions`. Do not leave it "
        "silently settled — an assumption nobody can see is one nobody can "
        "correct.")
    return "\n".join(parts)


def _what_we_worked_out(project: dict) -> str:
    """What the classifier already decided, which the planner never saw."""
    cls = project.get("classification") or {}
    complexity = project.get("complexity") or {}
    stack = project.get("suggested_stack") or {}
    rows = []
    if project.get("detected_domain") or cls.get("detected_domain"):
        rows.append(f"  domain: {project.get('detected_domain') or cls.get('detected_domain')}")
    if cls.get("app_type"):
        rows.append(f"  kind of app: {cls['app_type']}")
    if cls.get("reasoning"):
        rows.append(f"  why we think so: {str(cls['reasoning'])[:300]}")
    if complexity.get("overall"):
        rows.append(f"  complexity: {complexity['overall']}")
    if stack:
        rows.append("  stack already fixed: "
                    + ", ".join(f"{k}={v}" for k, v in stack.items()
                                if k != "locked" and v))
    if not rows:
        return ""
    return "WHAT WE HAVE ALREADY WORKED OUT ABOUT THIS BUSINESS:\n" + "\n".join(rows)


def _domain_library(pack: dict) -> str:
    """What apps in this domain usually hold — offered, never imposed."""
    rows = []

    thin = str(pack.get("archetype") or "") in ("landing-single-page", "single-tool")

    tables = [] if thin else [t for t in (pack.get("domain_tables") or [])
                              if isinstance(t, dict)]
    if tables:
        shaped = []
        for t in tables:
            name = t.get("table_name") or t.get("name")
            fields = [f.get("name") if isinstance(f, dict) else str(f)
                      for f in (t.get("fields") or [])]
            fields = [f for f in fields if f]
            if name:
                shaped.append(f"  {name}: {', '.join(fields) or '—'}")
        if shaped:
            rows.append("Records this trade usually keeps:\n" + "\n".join(shaped))

    offered = (("Modules", "domain_modules"),) if thin else (
        ("Roles", "roles"), ("Modules", "domain_modules"),
        ("Integrations", "domain_integrations"),
        ("Things it usually does", "operations"))
    for label, key in offered:
        items = pack.get(key) or []
        flat = []
        for item in items:
            if isinstance(item, dict):
                flat.append(str(item.get("role_name") or item.get("name")
                                or item.get("label") or ""))
            else:
                flat.append(str(item))
        flat = [f for f in flat if f.strip()]
        if flat:
            rows.append(f"{label}: {', '.join(flat)}")

    required = [str(p) for p in (pack.get("required_pages") or [])]
    if required:
        rows.append("Pages this kind of app cannot do without: "
                    + ", ".join(required))
    banned = [str(p) for p in (pack.get("prohibited_page_types") or [])]
    if banned:
        rows.append("Pages this kind of app must NOT have: " + ", ".join(banned))

    if not rows:
        return ""
    return ("WHAT APPS IN THIS TRADE USUALLY HAVE — a menu, not a checklist. "
            "Use an item only where the answers above support it; the rule "
            "against adding a record because the domain usually has one still "
            "holds.\n" + "\n\n".join(rows))


