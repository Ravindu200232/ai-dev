"""Compose the SRS only from the approved plan."""
from __future__ import annotations

import copy
import re
from typing import Any

from .app_types import PALETTES
from .domains import universal_tables
from .plan_srs_access import (
    _api_from_tables, _public_reads_table, _role_actions_for_table, _role_matrix, _rtm,
)
from .plan_srs_auth import account_page_specs, account_requirement_specs


_AUTH_TABLE_NAMES = ("users", "roles")


_ANONYMOUS_ROLES = {"visitor", "guest", "public", "anonymous", "everyone", "user"}


def _snake(text: str) -> str:

    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(text or ""))
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


def _table_name(text: str) -> str:
    """`Sale Item` -> `sale_items`."""
    name = _snake(text)
    if not name:
        return "record"
    if name.endswith("y") and not name.endswith(("ay", "ey", "oy", "uy")):
        return name[:-1] + "ies"
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return name + "es"
    return name + "s"


def _title(text: str) -> str:
    text = str(text or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else text


def _sentence(text: str) -> str:
    text = str(text or "").strip().rstrip(".")
    return f"{text}." if text else ""


def _auth_on(pack: dict, plan: dict) -> bool:
    """Does this app have accounts at all?"""
    if str((pack or {}).get("auth_policy")) == "disabled":
        return False
    named = [str(u.get("role", "")).strip().lower()
             for u in (plan or {}).get("users") or []]
    return any(r and r not in _ANONYMOUS_ROLES for r in named)


def _roles_from_plan(plan: dict) -> list[dict]:
    """One role per plan user."""
    out: list[dict] = []
    seen: set[str] = set()
    for user in plan.get("users") or []:
        name = str(user.get("role", "")).strip()
        key = _snake(name)
        if not key or key in seen:
            continue
        seen.add(key)
        duties = [d for d in (user.get("can_do") or []) if str(d).strip()]
        description = ("; ".join(str(d).strip() for d in duties)
                       or f"Uses {name.lower()} features of the system.")
        out.append({"role_key": key, "role_name": _title(name),
                    "description": _sentence(description)})
    if not out:

        out.append({"role_key": "user", "role_name": "User",
                    "description": "Uses the application."})
    return out


_TYPE_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("price", "amount", "total", "cost", "rate", "salary", "fee", "discount",
      "balance", "subtotal"), "decimal"),
    (("quantity", "qty", "stock", "count", "level", "number of", "age"), "integer"),
    (("date of", "birth", "expiry", "due date", "preferred date"), "date"),
    (("created", "updated", "at", "time", "timestamp"), "datetime"),
    (("is ", "has ", "active", "enabled", "paid", "confirmed"), "boolean"),
    (("status", "state", "type", "category level"), "enum"),
    (("description", "notes", "message", "body", "address", "comment"), "text"),
    (("email",), "string"),
    (("photo", "image", "picture", "file", "attachment", "logo"), "string"),
]


def _field_type(label: str) -> str:
    low = f" {str(label).lower().strip()} "
    for needles, kind in _TYPE_HINTS:
        if any(n in low for n in needles):
            return kind
    return "string"


def _tables_from_plan(plan: dict, auth: bool) -> tuple[list[dict], list[dict]]:
    """Tables and relationships, from `plan["records"]` alone."""
    tables: list[dict] = []
    record_names: list[str] = []

    for rec in plan.get("records") or []:
        name = str(rec.get("name", "")).strip()
        if not name:
            continue
        table = _table_name(name)
        if any(t["table_name"] == table for t in tables):
            continue
        record_names.append(table)
        fields = [{"name": "id", "type": "uuid", "primary_key": True, "nullable": False}]
        for keep in rec.get("keeps") or []:
            label = str(keep).strip()
            if not label:
                continue
            fname = _snake(label)
            if not fname or any(f["name"] == fname for f in fields):
                continue
            entry: dict[str, Any] = {"name": fname, "type": _field_type(label),
                                     "nullable": False}
            if entry["type"] == "enum":
                entry["values"] = ["active", "inactive"]
                entry["default"] = "active"
            fields.append(entry)
        fields.append({"name": "created_at", "type": "datetime", "nullable": False})
        tables.append({"table_name": table,
                       "description": f"{_title(name)} records.",
                       "fields": fields})

    relationships: list[dict] = []
    if auth:

        auth_tables = [t for t in universal_tables()
                       if t["table_name"] in _AUTH_TABLE_NAMES]
        tables = auth_tables + tables
        relationships.append({
            "from": "users.role_id", "to": "roles.id", "type": "many_to_one",
            "description": "Each user belongs to one role.",
        })

    by_name = {t["table_name"] for t in tables}
    seen = {(r["from"], r["to"]) for r in relationships}
    for t in tables:
        if t["table_name"] in _AUTH_TABLE_NAMES:
            continue
        for f in t["fields"]:
            if not f["name"].endswith("_id") or f.get("primary_key"):
                continue
            target = _table_name(f["name"][:-3])
            if target not in by_name or target == t["table_name"]:
                continue
            f["type"] = "foreign_key"
            f["references"] = f"{target}.id"
            edge = (f"{t['table_name']}.{f['name']}", f"{target}.id")
            if edge in seen:
                continue
            seen.add(edge)
            relationships.append({
                "from": edge[0], "to": edge[1], "type": "many_to_one",
                "description": f"Each {_singular(t['table_name'])} refers to "
                               f"one {_singular(target)}.",
            })
    return tables, relationships


def _singular(table: str) -> str:
    """`categories` -> `category`, `sales` -> `sale`."""
    if table.endswith("ies"):
        return table[:-3] + "y"
    if table.endswith(("sses", "xes", "zes", "ches", "shes")):
        return table[:-2]
    return table[:-1] if table.endswith("s") and not table.endswith("ss") else table


def _route_for(name: str, index: int) -> str:
    slug = _snake(name).replace("_", "-")
    return "/" if index == 0 else f"/{slug or f'page-{index}'}"


def _pages_from_plan(plan: dict, auth: bool, account_policy: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Turn approved screens into exact public/protected routes."""
    account_policy = account_policy or {}
    public: list[dict] = []
    protected: list[dict] = []
    seen_routes: set[str] = set()
    for i, screen in enumerate(plan.get("screens") or []):
        name = str(screen.get("name", "")).strip()
        if not name:
            continue
        route = str(screen.get("route") or _route_for(name, i)).strip() or "/"
        if not route.startswith("/"):
            route = "/" + route
        if route in seen_routes:
            continue
        seen_routes.add(route)
        who = [str(w).strip() for w in (screen.get("who") or []) if str(w).strip()]
        page = {
            "page_name": _title(name),
            "route": route,
            "page_type": "page",
            "sections": [],
            "functions": [_sentence(screen.get("purpose", ""))] if screen.get("purpose") else [],
        }
        anonymous = not who or all(w.lower() in _ANONYMOUS_ROLES for w in who)
        if not auth or anonymous:
            public.append({**page, "login_required": False})
        else:
            protected.append({**page, "login_required": True,
                              "allowed_roles": [_snake(w) for w in who]})

    if auth:
        sign_in = str(account_policy.get("sign_in_route") or "/login")
        if sign_in not in seen_routes:
            public.append({
                "page_name": "Sign In", "route": sign_in, "page_type": "auth",
                "login_required": False, "sections": [],
                "functions": ["Sign in with the approved identity fields."],
            })
            seen_routes.add(sign_in)
        if account_policy.get("registration_mode") == "open":
            sign_up = str(account_policy.get("sign_up_route") or "/register")
            if sign_up not in seen_routes:
                public.append({
                    "page_name": "Sign Up", "route": sign_up, "page_type": "auth",
                    "login_required": False, "sections": [],
                    "functions": [f"Create the approved {account_policy.get('registration_role') or 'user'} account."],
                })
                seen_routes.add(sign_up)

        extra_public, extra_protected = account_page_specs(account_policy)
        for page in extra_public:
            if page["route"] not in seen_routes:
                public.append(page)
                seen_routes.add(page["route"] )
        for page in extra_protected:
            if page["route"] not in seen_routes:
                protected.append(page)
                seen_routes.add(page["route"] )
    return public, protected


def _requirements_from_plan(plan: dict, tables: list[dict], auth: bool,
                            archetype: str, account_policy: dict | None = None,
                            public_pages: list[dict] | None = None) -> list[dict]:
    """Functional requirements, every one of them traceable to the plan."""
    account_policy = account_policy or {}
    public_pages = public_pages or []
    out: list[dict] = []
    seen: set[str] = set()

    def add(module: str, text: str, priority: str = "high",
            allowed_roles: list[str] | None = None) -> None:
        text = _sentence(text)
        key = text.lower()
        if not text or key in seen:
            return
        seen.add(key)
        out.append({"id": f"FR-{len(out) + 1:03d}", "module": module,
                    "requirement": text, "priority": priority,
                    "allowed_roles": list(allowed_roles or [])})

    if auth:
        add("Authentication",
            "The system shall let a person sign in with an email address and password")
        add("Authorization",
            "The system shall show each signed-in person only the screens their role allows")
        if account_policy.get("registration_mode") == "open":
            role = account_policy.get("registration_role") or "approved default user"
            add("Authentication",
                f"The system shall let a visitor create only a {role} account from public sign-up")
        else:
            add("Authentication",
                "The system shall not expose public self-registration")
        for module, text, priority, roles in account_requirement_specs(account_policy):
            add(module, text, priority, roles)

    for user in plan.get("users") or []:
        role = _title(user.get("role", "user"))
        for duty in user.get("can_do") or []:
            duty = str(duty).strip().rstrip(".")
            if duty:
                add(role, f"The system shall allow {role} users to {duty[:1].lower()}{duty[1:]}",
                    allowed_roles=[_snake(role)])

    for feature in plan.get("features") or []:
        feature = str(feature).strip().rstrip(".")
        if feature:
            add("Core Features", f"The system shall provide {feature[:1].lower()}{feature[1:]}"
                if not feature.lower().startswith("the system") else feature)

    if archetype == "fullstack-crud":
        for table in tables:
            if table["table_name"] in _AUTH_TABLE_NAMES:
                continue
            name = table["table_name"]
            label = name.replace("_", " ")
            singular = label[:-1] if label.endswith("s") else label
            module = _title(label)
            actions = _role_actions_for_table(plan, name)
            public_read = _public_reads_table(public_pages, name)
            specs = (
                ("read", (f"The system shall let visitors view {label}" if public_read
                          else f"The system shall let approved users view {label}")),
                ("create", f"The system shall let approved users create one {singular} record"),
                ("update", f"The system shall let approved users update one {singular} record"),
                ("delete", f"The system shall let approved users delete one {singular} record"),
            )
            for action, text in specs:
                roles = [] if action == "read" and public_read else actions[action]
                if actions[action] or (action == "read" and public_read):
                    add(module, text, "high" if action in {"read", "create"} else "medium", roles)

    for wf in plan.get("workflows") or []:
        name = str(wf.get("name", "")).strip()
        if name and wf.get("steps"):
            who = _snake(wf.get("who", ""))
            add("Workflows", f"The system shall support the {name.lower()} workflow "
                             f"end to end", "medium", [who] if who else None)

    while len(out) < 3:

        add("Core Features", f"The system shall do what the approved plan describes "
                             f"({len(out) + 1})", "medium")
    return out


def _nfrs(auth: bool, devices: list[str]) -> list[dict]:
    """Quality attributes. Universal ones are not scope; auth ones are gated."""
    items = [
        ("Performance", "Primary screens should load within 3 seconds (P95) under normal conditions."),
        ("Usability", "A first-time user should be able to complete the main task without training."),
        ("Compatibility", "The application must work in current versions of Chrome, Edge, Firefox and Safari."),
        ("Maintainability", "Code shall be organised by feature, so one change touches one place."),
    ]
    if "desktop" not in devices or len(devices) > 1:
        items.append(("Usability",
                      "The interface must be usable on a phone screen without horizontal scrolling."))
    if auth:
        items += [
            ("Security", "Passwords must be hashed with a strong adaptive algorithm (bcrypt or argon2)."),
            ("Security", "Every protected screen and endpoint must check both sign-in and role."),
        ]
    return [{"id": f"NFR-{i:03d}", "category": cat, "requirement": text}
            for i, (cat, text) in enumerate(items, start=1)]


def build_branding(session: dict, pack: dict, app_name: str) -> dict:
    """What the app looks like, and whether a logo has to be drawn."""
    answers = (session or {}).get("answers") or {}

    def ans(key, default=None):
        entry = answers.get(key)
        return default if entry is None else entry.get("value", default)

    artwork = ans("image_kinds") or []
    if isinstance(artwork, str):
        artwork = [artwork]
    logo_requested = any(str(item).strip().lower() in {"logo", "logo_mark", "logo mark"}
                         for item in artwork)
    source = "generate" if logo_requested else "none"

    palette_name = _resolve_palette(ans("color_palette"), pack)
    palette = PALETTES[palette_name]
    theme = str(ans("theme_type") or "light")

    branding = {
        "logo_required": source == "generate",
        "logo_source": source,
        "logo_image_prompt": "",
        "theme": theme,
        "palette": palette_name,
        "primary_color": palette.get("primary", "#6366F1"),
    }
    if source == "generate":
        branding["logo_image_prompt"] = _logo_prompt(app_name, pack, branding)
    return branding


_PALETTE_ALIASES = {
    "corporate_blue": "business", "corporate": "business", "blue": "business",
    "fresh_mint": "mint", "green": "mint",
    "deep_navy_gold": "navy", "deep_navy": "navy",
    "black_gold": "gold", "black": "gold",
    "modern_indigo": "indigo", "purple": "indigo",
}


def _resolve_palette(answer, pack: dict) -> str:
    """The palette key whose colour we will actually print."""
    key = _snake(answer if not isinstance(answer, list) else (answer[0] if answer else ""))
    if key in PALETTES:
        return key
    if key in _PALETTE_ALIASES:
        return _PALETTE_ALIASES[key]
    fallback = str(pack.get("palette_name") or "indigo")
    return fallback if fallback in PALETTES else "indigo"


def _logo_prompt(app_name: str, pack: dict, branding: dict) -> str:
    """A text-to-image prompt for the logo mark."""
    label = str(pack.get("app_label") or "web application").lower()

    domain = str(pack.get("domain_label") or "").strip()
    named = domain and float(pack.get("domain_confidence") or 0) >= 0.6
    subject = f"a {label}" + (f" for {domain.lower()}" if named else "")
    dark = branding.get("theme") == "dark"
    return (
        f"A flat vector logo mark for \"{app_name}\", {subject}. "
        f"One simple, memorable symbol that reads clearly at 32 pixels. "
        f"Primary colour {branding['primary_color']} on a "
        f"{'dark charcoal' if dark else 'white'} background. "
        f"No text, no lettering, no gradients, no photorealism, no drop shadows. "
        f"Square composition, generous margin, transparent background, SVG-like clean edges."
    )

