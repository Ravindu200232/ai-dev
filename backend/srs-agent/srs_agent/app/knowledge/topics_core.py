"""The interview backbone."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from . import app_types as catalog


KINDS = ("single", "multi", "yes_no", "number", "text", "upload")


@dataclass
class Topic:
    key: str
    kind: str
    label: str
    intent: str
    applies_to: Callable[[dict], bool] = lambda s: True

    options_from: Callable[[dict, dict], list] | None = None
    fallback_options: list = field(default_factory=list)

    options_locked: bool = False

    repeats_over: Callable[[dict], list] | None = None
    placeholder: str = ""

    profiles: tuple[str, ...] = ()

    srs_fields: tuple[str, ...] = ()
    coverage: tuple[str, ...] = ()

    fixed: str = ""

    optional: bool = False

    multiline: bool = False


def ans(s: dict, key: str, default=None):
    entry = s.get("answers", {}).get(key)
    return default if entry is None else entry.get("value", default)


def has_auth(s: dict) -> bool:
    v = ans(s, "auth")
    if v is None:
        return bool(_pack(s).get("auth_default", True))
    return v in (True, "yes", "true", 1)


def wants_images(s: dict) -> bool:
    v = ans(s, "images")
    return v in (True, "yes", "true", 1)


def roles_list(s: dict) -> list[str]:
    v = ans(s, "auth_roles") or []
    if isinstance(v, str):
        v = [v]
    return [str(x) for x in v]


_PRIVILEGED_SIGNUP = ("admin", "manager", "owner", "staff", "cashier", "operator", "moderator")


def self_signup_roles(s: dict) -> list[str]:
    """Roles safe for public self-registration."""
    out = []
    for role in roles_list(s):
        key = re.sub(r"[^a-z0-9]+", "_", role.lower()).strip("_")
        if key and not any(word in key for word in _PRIVILEGED_SIGNUP):
            out.append(role)
    return out


def open_registration(s: dict) -> bool:
    mode = str(ans(s, "account_creation") or ans(s, "saas_signup") or "").lower()
    return mode == "open"


def tables_list(s: dict) -> list[str]:
    v = ans(s, "data_tables") or []
    if isinstance(v, str):
        v = [v]
    return [str(x) for x in v]


def _pack(s: dict) -> dict:
    return s.get("pack") or {}


def _yes_no() -> list:
    return [{"label": "Yes", "value": True}, {"label": "No", "value": False}]


CRUD = "fullstack-crud"
LANDING = "landing-single-page"
TOOL = "single-tool"


def archetype(s: dict) -> str:
    return str(_pack(s).get("archetype") or CRUD)


def question_set(s: dict) -> str:
    """Which profile's questions this session asks."""
    pack = _pack(s)
    return str(pack.get("question_set") or pack.get("app_type") or "saas")


RECORD_PROFILES = ("pos", "saas", "ecommerce", "dashboard", "blog", "other")


def only_for(*kinds: str) -> Any:
    """Gate a topic to the archetypes it actually makes sense for."""
    return lambda s: archetype(s) in kinds


def _and(*conditions) -> Any:
    return lambda s: all(cond(s) for cond in conditions)


def wants_leads(s: dict) -> bool:
    v = ans(s, "lead_capture")
    if v is None:
        return True
    return v in (True, "yes", "true", 1)


def _sections_options(s: dict, item: dict | None = None) -> list:
    """The bands this kind of page is built from, per the app type's own plan."""
    pages = _pack(s).get("pages") or []
    sections: list[str] = []
    for page in pages:
        for name in page.get("sections") or []:
            if name not in sections:
                sections.append(name)
    if not sections:
        sections = ["Hero", "Services", "About", "Testimonials", "Contact"]
    return [{"label": name, "value": _snake_value(name)} for name in sections]


def _offering_options(s: dict, item: dict | None = None) -> list:
    """Deliberately empty, so the model reads the customer's own idea."""
    return []


def _snake_value(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "item"


def _domain_tables(s: dict) -> list[dict]:
    return _pack(s).get("domain_tables") or []


_ES_PLURAL = ("sses", "xes", "zes", "ches", "shes")


def _norm(name: str) -> str:
    """Loose singular key, so 'Sales Orders' and 'sales_order' compare equal."""
    key = re.sub(r"[^a-z0-9]+", "", str(name).lower())
    if key.endswith("ies"):
        return key[:-3] + "y"
    if key.endswith(_ES_PLURAL):
        return key[:-2]
    if key.endswith("s") and not key.endswith("ss"):
        return key[:-1]
    return key


def _match_domain_table(subject: str, s: dict) -> dict | None:
    want = _norm(subject)
    if not want:
        return None
    tables = _domain_tables(s)
    for t in tables:
        if _norm(t.get("table_name", "")) == want:
            return t

    if len(want) >= 5:
        for t in tables:
            other = _norm(t.get("table_name", ""))
            if other and (want in other or other in want):
                return t
    return None


_BORING_FIELDS = {"id", "created_at", "updated_at", "deleted_at",
                  "password_hash", "old_values", "new_values", "ip_address"}


def _field_options(s: dict, item: dict) -> list:
    """Real fields for THIS record type, drawn from the matched domain table."""
    table = _match_domain_table((item or {}).get("subject") or "", s)
    if not table:
        return []

    out = []
    for fld in table.get("fields") or []:
        name = str(fld.get("name") or "")
        if not name or name in _BORING_FIELDS or fld.get("primary_key"):
            continue
        label = re.sub(r"_(id|at)$", "", name).replace("_", " ").strip().title()
        opt = {"label": label or name, "value": name}
        if fld.get("type") == "foreign_key":
            opt["hint"] = "links to another record"
        elif fld.get("values"):
            opt["hint"] = " / ".join(str(v) for v in fld["values"])
        out.append(opt)
    return out


_WHAT_HAPPENS = {
    "pos": "The app IS the till — ring up what someone is buying and take "
           "payment at the counter",
    "ecommerce": "Customers order and pay on the site themselves, and "
                 "something gets shipped or collected",
    "saas": "Customers sign up for their own account and use it on their own, "
            "usually paying a subscription",
    "dashboard": "Your own people keep the records — bookings, jobs, members, "
                 "stock — and work from them all day",
    "blog": "Writing is the point: pieces get published and readers come to "
            "read them",
    "landing": "One page that explains one thing and collects sign-ups",
    "portfolio": "Finished work put on show — there is nothing to run or "
                 "keep track of",
    "utility": "One small job done on the spot, and nothing is kept afterwards",
    "other": "None of these describe it — tell us what happens in it",
}


def _mark_auth(options: list) -> list:
    """Say which categories mean "no login", because three of them do."""
    out = []
    for opt in options:
        opt = dict(opt)
        key = opt.get("value")
        spec = catalog.APP_TYPES.get(key) or {}
        opt["hint"] = _WHAT_HAPPENS.get(key) or opt.get("hint") or ""
        if str(spec.get("auth_policy")) == "disabled":
            hint = str(opt.get("hint") or "").rstrip(" .")
            opt["hint"] = f"{hint} — nobody logs in" if hint else "Nobody logs in"
        out.append(opt)
    return out


def _app_type_options(s: dict) -> list:
    """The nine build categories, best guess first."""
    options = _mark_auth(catalog.app_type_options())
    guess = str((s or {}).get("guessed_app_type") or "").strip()
    if not guess:
        return options

    try:
        confidence = float((s or {}).get("guessed_app_type_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    if confidence < 0.4:
        return options

    why = str((s or {}).get("guessed_app_type_why") or "").strip()
    ordered, chosen = [], None
    for opt in options:
        if opt.get("value") == guess and chosen is None:
            chosen = dict(opt)
        else:
            ordered.append(opt)
    if chosen is None:
        return options

    reason = f"Suggested — {why}" if why else "Closest match to what you wrote"

    if str((catalog.APP_TYPES.get(guess) or {}).get("auth_policy")) == "disabled":
        reason += " · nobody logs in"
    chosen["hint"] = reason
    chosen["suggested"] = True
    return [chosen] + ordered

