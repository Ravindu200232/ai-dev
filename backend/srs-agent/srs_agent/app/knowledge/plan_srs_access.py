"""Role, API and traceability rules derived from the approved plan."""
from __future__ import annotations

import re

from .plan_srs_auth import account_api_specs

_AUTH_TABLE_NAMES = ("users", "roles")


def _snake(text: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(text or ""))
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


def _role_actions_for_table(plan: dict, table_name: str) -> dict[str, list[str]]:
    """Read CRUD-like permissions from role duties and workflows."""
    label = table_name.replace("_", " ").lower()
    singular = label[:-1] if label.endswith("s") else label
    terms = {label, singular}
    verbs = {
        "read": ("view", "see", "read", "browse", "list", "search", "filter", "review", "track"),
        "create": ("add", "create", "new", "place", "submit", "book", "purchase", "record"),
        "update": ("edit", "update", "change", "adjust", "set", "approve", "reject", "manage"),
        "delete": ("delete", "remove"),
    }
    out = {key: [] for key in verbs}

    def add(role: str, text: str) -> None:
        low = text.lower()
        if not any(term and term in low for term in terms):
            return
        role_key = _snake(role)
        if not role_key:
            return
        managed = "manage" in low
        for action, words in verbs.items():
            if managed or any(re.search(rf"\b{re.escape(word)}\w*\b", low) for word in words):
                if role_key not in out[action]:
                    out[action].append(role_key)

    for user in plan.get("users") or []:
        for duty in user.get("can_do") or []:
            add(str(user.get("role") or ""), str(duty))
    for flow in plan.get("workflows") or []:
        role = str(flow.get("who") or "")
        for step in flow.get("steps") or []:
            add(role, str(step))
    return out


def _public_reads_table(public_pages: list[dict], table_name: str) -> bool:
    label = table_name.replace("_", " ").lower()
    singular = label[:-1] if label.endswith("s") else label
    for page in public_pages:
        blob = " ".join([
            str(page.get("page_name") or ""),
            str(page.get("route") or ""),
            " ".join(str(x) for x in (page.get("functions") or [])),
            " ".join(str(x) for x in (page.get("sections") or [])),
        ]).lower()
        if label in blob or singular in blob:
            return True
    return False


def _api_from_tables(tables: list[dict], auth: bool, account_policy: dict | None = None,
                     plan: dict | None = None, public_pages: list[dict] | None = None) -> list[dict]:
    account_policy = account_policy or {}
    plan = plan or {}
    public_pages = public_pages or []
    api: list[dict] = []
    if auth:
        api.extend(account_api_specs(account_policy))

    for table in tables:
        if table["table_name"] in _AUTH_TABLE_NAMES:
            continue
        name = table["table_name"]
        resource = name.replace("_", "-")
        singular = name[:-1] if name.endswith("s") else name
        actions = _role_actions_for_table(plan, name)
        public_read = _public_reads_table(public_pages, name)
        supported = {action for action, roles in actions.items() if roles}
        if public_read:
            supported.add("read")
        if not supported:
            continue

        methods = {
            "read": [
                ("GET", f"/api/{resource}", f"List {name}."),
                ("GET", f"/api/{resource}/{{id}}", f"Read one {singular} record."),
            ],
            "create": [("POST", f"/api/{resource}", f"Create one {singular} record.")],
            "update": [("PUT", f"/api/{resource}/{{id}}", f"Update one {singular} record.")],
            "delete": [("DELETE", f"/api/{resource}/{{id}}", f"Delete one {singular} record.")],
        }
        for action in ("read", "create", "update", "delete"):
            if action not in supported:
                continue
            for method, path, description in methods[action]:
                is_public = action == "read" and public_read
                row = {"method": method, "path": path, "description": description,
                       "auth_required": bool(auth and not is_public)}
                roles = actions[action]
                if roles and not is_public:
                    row["allowed_roles"] = roles
                api.append(row)
    return api


def _role_matrix(roles: list[dict], plan: dict, public: list[dict],
                 protected: list[dict]) -> list[dict]:
    """What each role may do, read off the plan rather than assumed."""
    duties = {_snake(u.get("role", "")): [str(d) for d in (u.get("can_do") or [])]
              for u in plan.get("users") or []}
    out = []
    for r in roles:
        key = r["role_key"]
        pages = [p["page_name"] for p in protected if key in (p.get("allowed_roles") or [])]
        denied_pages = [p["page_name"] for p in protected
                        if key not in (p.get("allowed_roles") or [])]
        out.append({
            "role": key,
            "allowed_pages": pages + [p["page_name"] for p in public],
            "restricted_pages": denied_pages,
            "allowed_functions": duties.get(key, []),
            "restricted_functions": [],
        })
    return out


def _rtm(frs: list[dict], tables: list[dict], pages: list[dict]) -> list[dict]:
    """Map each requirement to its pages and records."""
    table_names = [t["table_name"] for t in tables]
    out = []
    for fr in frs:
        text = f"{fr.get('module', '')} {fr.get('requirement', '')}".lower()
        roles = {_snake(r) for r in (fr.get("allowed_roles") or []) if _snake(r)}

        matched_tables = []
        for table in table_names:
            label = table.replace("_", " ").lower()
            singular = label[:-1] if label.endswith("s") else label
            if label in text or singular in text:
                matched_tables.append(table)

        matched_pages = []
        for page in pages:
            name = str(page.get("page_name") or "")
            route = str(page.get("route") or "")
            allowed = {_snake(r) for r in (page.get("allowed_roles") or []) if _snake(r)}
            page_words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) >= 4]
            name_match = any(word in text for word in page_words)
            role_match = bool(roles and allowed and roles.intersection(allowed))
            if name_match or role_match:
                label = name or route
                if label and label not in matched_pages:
                    matched_pages.append(label)

        out.append({
            "requirement_id": fr["id"],
            "module": fr["module"],
            "pages": matched_pages,
            "tables": matched_tables,
            "test_case": f"TC-{fr['id'][3:]}",
        })
    return out

