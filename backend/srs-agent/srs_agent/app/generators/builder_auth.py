"""Explicit account and role rules for the builder handoff."""
from __future__ import annotations

import re

from .builder_utils import _clean, _normal_route_pattern

_PRIVILEGED = ("admin", "manager", "owner", "staff", "cashier", "operator", "moderator")


def _role_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _clean(value).lower()).strip("_")


def _auth_route(pages: list[dict], pattern: str) -> str | None:
    rx = re.compile(pattern, re.I)
    for page in pages:
        blob = f"{page.get('name', '')} {page.get('route', '')}"
        if rx.search(blob):
            return str(page.get("route") or "") or None
    return None


def _safe_registration_role(roles: list[dict]) -> str | None:
    for role in roles:
        key = _role_key(role.get("key") or role.get("name") or "")
        if key and not any(word in key for word in _PRIVILEGED):
            return str(role.get("key") or role.get("name") or "")
    return None


def build_auth_contract(*, doc: dict, plan: dict, pages: list[dict],
                        roles: list[dict], access: list[dict], auth: bool) -> dict:
    """Return the account contract the builder must implement literally."""
    if not auth:
        return {
            "enabled": False,
            "sign_in": False,
            "self_registration": False,
            "public_behavior": "all pages are public",
        }

    req = doc.get("authentication_requirement") or {}
    policy = plan.get("account_policy") or {}
    sign_in_route = (_clean(req.get("sign_in_route") or policy.get("sign_in_route"))
                     or _auth_route(pages, r"login|sign[ -]?in"))
    sign_up_route = (_clean(req.get("sign_up_route") or policy.get("sign_up_route"))
                     or _auth_route(pages, r"register|sign[ -]?up"))

    mode = _clean(req.get("registration_mode") or policy.get("registration_mode"))
    if not mode:
        mode = "open" if sign_up_route else "admin_created"
    self_registration = bool(req.get("self_registration")) if "self_registration" in req else mode == "open"
    if not self_registration:
        sign_up_route = None

    registration_roles = [
        _clean(x) for x in (req.get("registration_roles") or policy.get("registration_roles") or [])
        if _clean(x)
    ]
    one_role = _clean(req.get("registration_role") or policy.get("registration_role"))
    if one_role and one_role not in registration_roles:
        registration_roles.append(one_role)
    if self_registration and not registration_roles:
        safe = _safe_registration_role(roles)
        if safe:
            registration_roles.append(safe)

    identity = [
        _clean(x) for x in (req.get("identity_fields") or policy.get("sign_in_fields") or [])
        if _clean(x)
    ] or ["email", "password"]
    registration_fields = [
        _clean(x) for x in (req.get("registration_fields") or policy.get("registration_fields") or identity)
        if _clean(x)
    ]

    if self_registration:
        registration_roles = [r for r in registration_roles
                              if not any(word in _role_key(r) for word in _PRIVILEGED)]
        if not registration_roles:
            safe = _safe_registration_role(roles)
            if safe:
                registration_roles = [safe]
            else:
                self_registration = False
                mode = "admin_created"
                sign_up_route = None

    provisioning_role = _clean(req.get("provisioning_role") or policy.get("provisioning_role"))
    provisioning = {
        "mode": mode,
        "role": provisioning_role or None,
        "account_management_route": _clean(req.get("account_management_route") or policy.get("account_management_route")) or None,
        "invitation_management_route": _clean(req.get("invitation_management_route") or policy.get("invitation_management_route")) or None,
        "invitation_accept_route": _clean(req.get("invitation_accept_route") or policy.get("invitation_accept_route")) or None,
        "request_access_route": _clean(req.get("request_access_route") or policy.get("request_access_route")) or None,
        "access_review_route": _clean(req.get("access_review_route") or policy.get("access_review_route")) or None,
    }

    role_home: dict[str, str] = {}
    for role in roles:
        key = _clean(role.get("key") or role.get("name") or "")
        if not key:
            continue
        for page in pages:
            allowed = [_role_key(x) for x in (page.get("allowed_roles") or [])]
            if page.get("public"):
                continue
            if not allowed or _role_key(key) in allowed:
                role_home[key] = str(page.get("route") or "/")
                break

    role_rules = []
    page_by_name = {str(p.get("name") or "").casefold(): p for p in pages}
    for row in access:
        role = _clean(row.get("role") or "")
        if not role:
            continue
        routes = []
        for name in row.get("allowed_pages") or []:
            page = page_by_name.get(str(name).casefold())
            if page and page.get("route") not in routes:
                routes.append(page.get("route"))
        explicit = [
            p.get("route") for p in pages
            if not p.get("public") and _role_key(role) in {
                _role_key(r) for r in (p.get("allowed_roles") or [])
            }
        ]
        for route in explicit:
            if route and route not in routes:
                routes.append(route)
        denied_routes = [
            p.get("route") for p in pages
            if not p.get("public") and p.get("route") not in routes
        ]
        role_rules.append({
            "role": role,
            "allowed_routes": routes,
            "denied_routes": denied_routes,
            "allowed_functions": list(row.get("allowed_functions") or []),
            "restricted_functions": list(row.get("restricted_functions") or []),
            "home_route": role_home.get(role),
        })

    return {
        "enabled": True,
        "sign_in": True,
        "sign_in_route": sign_in_route,
        "sign_out_required": True,
        "identity_fields": identity,
        "registration_fields": registration_fields,
        "password_reset_required": bool(req.get("password_reset_required", False)),
        "registration_mode": mode,
        "self_registration": self_registration,
        "sign_up_route": sign_up_route,
        "registration_roles": registration_roles,
        "provisioning": provisioning,
        "auth_transport": _clean(req.get("auth_transport") or "provider_managed"),
        "role_source": "server-side session user record",
        "signed_out_protected_access": "redirect_to_sign_in",
        "wrong_role_access": "forbidden",
        "role_home": role_home,
        "roles": role_rules,
        "rules": [
            "Never accept a role from a self-registration form unless the SRS explicitly allows that role.",
            "Check session and role on the server before protected reads or writes.",
            "Hiding navigation is not authorization.",
        ],
    }


def auth_prompt_lines(contract: dict) -> list[str]:
    if not contract.get("enabled"):
        return ["AUTH CONTRACT:", "- No accounts. No login, sign-up or protected routes.", ""]

    lines = ["AUTH CONTRACT — IMPLEMENT BEFORE ROLE FEATURES:"]
    lines.append("- Auth transport is provider-managed. Use the project's approved auth stack and its existing session/server APIs; do not build a parallel auth system or invent generic auth endpoints.")
    lines.append(f"- Sign in: {contract.get('sign_in_route') or 'required page from the SRS'} using "
                 + ", ".join(contract.get("identity_fields") or ["email", "password"]))
    if contract.get("self_registration"):
        roles = ", ".join(contract.get("registration_roles") or []) or "the explicitly approved default role"
        fields = ", ".join(contract.get("registration_fields") or [])
        lines.append(f"- Sign up: {contract.get('sign_up_route') or 'required'}; new accounts may become only: {roles}.")
        if fields:
            lines.append(f"- Registration fields: {fields}. The role is assigned by the server, never chosen by the visitor.")
    else:
        lines.append("- No public sign-up. Do not invent a register page or role picker.")
        provision = contract.get("provisioning") or {}
        mode = provision.get("mode")
        role = provision.get("role") or "the approved provisioning role"
        if mode == "admin_created":
            lines.append(f"- Account provisioning: {role} creates accounts at {provision.get('account_management_route') or '/admin/users'}.")
        elif mode == "invite":
            lines.append(f"- Account provisioning: {role} sends invitations at {provision.get('invitation_management_route') or '/admin/invitations'}; recipients finish at {provision.get('invitation_accept_route') or '/accept-invite'}.")
        elif mode == "request":
            lines.append(f"- Account provisioning: visitors request access at {provision.get('request_access_route') or '/request-access'}; {role} reviews requests at {provision.get('access_review_route') or '/admin/access-requests'}.")
    lines.append("- Signed-out access to a protected route redirects to sign in; a signed-in user with the wrong role is refused.")
    lines.append("- The session user record is the role source. Never trust role values from forms, query strings or writable cookies.")
    for row in contract.get("roles") or []:
        role = row.get("role")
        allow = ", ".join(row.get("allowed_routes") or []) or "no protected route"
        deny = ", ".join(row.get("denied_routes") or [])
        lines.append(f"- {role}: allowed routes = {allow}" + (f"; denied = {deny}" if deny else ""))
        funcs = row.get("allowed_functions") or []
        if funcs:
            lines.append("    can: " + "; ".join(str(x) for x in funcs))
        blocked = row.get("restricted_functions") or []
        if blocked:
            lines.append("    cannot: " + "; ".join(str(x) for x in blocked))
    lines.append("")
    return lines
