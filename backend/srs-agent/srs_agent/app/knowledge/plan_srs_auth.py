"""Account pages, APIs and requirements derived from the approved policy."""
from __future__ import annotations

import re


def _snake(text: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(text or ""))
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


def account_page_specs(policy: dict) -> tuple[list[dict], list[dict]]:
    mode = str(policy.get("registration_mode") or "none")
    role = _snake(policy.get("provisioning_role") or "admin")
    public: list[dict] = []
    protected: list[dict] = []

    if mode == "admin_created":
        protected.append({
            "page_name": "User Management",
            "route": str(policy.get("account_management_route") or "/admin/users"),
            "page_type": "account_management",
            "login_required": True,
            "allowed_roles": [role],
            "sections": [],
            "functions": ["List user accounts.", "Create user accounts.", "Set an approved role on each account."],
        })
    elif mode == "invite":
        protected.append({
            "page_name": "Invitations",
            "route": str(policy.get("invitation_management_route") or "/admin/invitations"),
            "page_type": "account_management",
            "login_required": True,
            "allowed_roles": [role],
            "sections": [],
            "functions": ["Send account invitations.", "View invitation status."],
        })
        public.append({
            "page_name": "Accept Invitation",
            "route": str(policy.get("invitation_accept_route") or "/accept-invite"),
            "page_type": "auth",
            "login_required": False,
            "sections": [],
            "functions": ["Accept a valid invitation and create the invited account."],
        })
    elif mode == "request":
        public.append({
            "page_name": "Request Access",
            "route": str(policy.get("request_access_route") or "/request-access"),
            "page_type": "auth",
            "login_required": False,
            "sections": [],
            "functions": ["Submit an access request."],
        })
        protected.append({
            "page_name": "Access Requests",
            "route": str(policy.get("access_review_route") or "/admin/access-requests"),
            "page_type": "account_management",
            "login_required": True,
            "allowed_roles": [role],
            "sections": [],
            "functions": ["Review access requests.", "Approve or reject a request."],
        })
    return public, protected


def account_requirement_specs(policy: dict) -> list[tuple[str, str, str, list[str]]]:
    mode = str(policy.get("registration_mode") or "none")
    role = _snake(policy.get("provisioning_role") or "admin")
    if mode == "admin_created":
        return [("Authentication", "The system shall let the provisioning role create user accounts", "high", [role])]
    if mode == "invite":
        return [
            ("Authentication", "The system shall let the provisioning role send account invitations", "high", [role]),
            ("Authentication", "The system shall let an invited person accept a valid invitation and create the invited account", "high", []),
        ]
    if mode == "request":
        return [
            ("Authentication", "The system shall let a visitor request access", "high", []),
            ("Authentication", "The system shall let the provisioning role approve or reject access requests", "high", [role]),
        ]
    return []


def account_api_specs(policy: dict) -> list[dict]:
    mode = str(policy.get("registration_mode") or "none")
    role = _snake(policy.get("provisioning_role") or "admin")
    if mode == "admin_created":
        return [
            {"method": "GET", "path": "/api/users", "description": "List user accounts.", "auth_required": True, "allowed_roles": [role]},
            {"method": "POST", "path": "/api/users", "description": "Create a user account with an approved role.", "auth_required": True, "allowed_roles": [role]},
        ]
    if mode == "invite":
        return [
            {"method": "GET", "path": "/api/invitations", "description": "List account invitations.", "auth_required": True, "allowed_roles": [role]},
            {"method": "POST", "path": "/api/invitations", "description": "Send an account invitation.", "auth_required": True, "allowed_roles": [role]},
            {"method": "POST", "path": "/api/auth/accept-invite", "description": "Accept a valid invitation and create the invited account.", "auth_required": False},
        ]
    if mode == "request":
        return [
            {"method": "POST", "path": "/api/access-requests", "description": "Submit an access request.", "auth_required": False},
            {"method": "GET", "path": "/api/access-requests", "description": "List access requests.", "auth_required": True, "allowed_roles": [role]},
            {"method": "PUT", "path": "/api/access-requests/{id}", "description": "Approve or reject an access request.", "auth_required": True, "allowed_roles": [role]},
        ]
    return []

_PRIVILEGED = ("admin", "manager", "owner", "staff", "cashier", "operator", "moderator")


def normalize_account_policy(policy: dict, roles: list[dict]) -> dict:
    """Fill account-policy mechanics without widening the selected mode."""
    out = dict(policy or {})
    if not out.get("accounts_required"):
        return {"accounts_required": False, "registration_mode": "none"}

    mode = str(out.get("registration_mode") or "admin_created")
    out["registration_mode"] = mode
    out.setdefault("sign_in_route", "/login")
    out.setdefault("sign_in_fields", ["email", "password"])
    out.setdefault("registration_fields", list(out.get("sign_in_fields") or ["email", "password"]))
    out.setdefault("password_reset_required", False)

    def role_name(row: dict) -> str:
        return str(row.get("role_name") or row.get("role_key") or "").strip()

    if mode == "open":
        chosen = str(out.get("registration_role") or "").strip()
        key = _snake(chosen)
        if not chosen or any(word in key for word in _PRIVILEGED):
            chosen = next((role_name(r) for r in roles
                           if role_name(r) and not any(word in _snake(role_name(r)) for word in _PRIVILEGED)), "")
        if chosen:
            out["registration_role"] = chosen
            out.setdefault("sign_up_route", "/register")
        else:
            out["registration_mode"] = "admin_created"
            out["registration_role"] = None
            out["sign_up_route"] = None
            mode = "admin_created"

    if mode in {"admin_created", "invite", "request"}:
        provisioning = str(out.get("provisioning_role") or "").strip()
        if not provisioning:
            provisioning = next((role_name(r) for r in roles
                                 if any(word in _snake(role_name(r)) for word in _PRIVILEGED)), "")
        if not provisioning:
            provisioning = next((role_name(r) for r in roles if role_name(r)), "")
        out["provisioning_role"] = provisioning or None

    if mode == "admin_created":
        out.setdefault("account_management_route", "/admin/users")
    elif mode == "invite":
        out.setdefault("invitation_management_route", "/admin/invitations")
        out.setdefault("invitation_accept_route", "/accept-invite")
    elif mode == "request":
        out.setdefault("request_access_route", "/request-access")
        out.setdefault("access_review_route", "/admin/access-requests")
    return out
