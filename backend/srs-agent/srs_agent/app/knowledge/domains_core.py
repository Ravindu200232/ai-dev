"""Domain knowledge library."""
from __future__ import annotations

import re
from typing import Any

GENERIC_KEY = "custom"


def pk() -> dict:
    return {"name": "id", "type": "uuid", "primary_key": True, "nullable": False}


def fk(name: str, ref: str, nullable: bool = False) -> dict:
    return {"name": name, "type": "foreign_key", "references": ref, "nullable": nullable}


def f(name: str, type_: str, **kw: Any) -> dict:
    return {"name": name, "type": type_, **kw}


def timestamps() -> list[dict]:
    return [
        f("created_at", "datetime", nullable=False),
        f("updated_at", "datetime", nullable=False),
    ]


def status(values: list[str], default: str) -> dict:
    return f("status", "enum", values=values, default=default)


def universal_tables() -> list[dict]:
    return [
        {
            "table_name": "users",
            "description": "Login accounts for all system users.",
            "fields": [
                pk(),
                f("full_name", "string", nullable=False),
                f("email", "string", unique=True, nullable=False),
                f("phone", "string", nullable=True),
                f("password_hash", "string", nullable=False),
                fk("role_id", "roles.id"),
                status(["active", "inactive", "blocked"], "active"),
                f("last_login_at", "datetime", nullable=True),
                *timestamps(),
            ],
        },
        {
            "table_name": "roles",
            "description": "System roles for RBAC.",
            "fields": [pk(), f("role_key", "string", unique=True), f("role_name", "string"),
                       f("description", "text", nullable=True)],
        },
        {
            "table_name": "permissions",
            "description": "Granular permissions assigned to roles.",
            "fields": [pk(), f("permission_key", "string", unique=True), f("module", "string"),
                       f("action", "enum", values=["view", "create", "edit", "delete", "approve", "export"])],
        },
        {
            "table_name": "audit_logs",
            "description": "Security audit trail of critical actions.",
            "fields": [
                pk(), fk("user_id", "users.id", nullable=True), f("action", "string"),
                f("module", "string"), f("record_id", "string", nullable=True),
                f("old_values", "json", nullable=True), f("new_values", "json", nullable=True),
                f("ip_address", "string", nullable=True), f("created_at", "datetime"),
            ],
        },
        {
            "table_name": "notifications",
            "description": "In-app + outbound notifications.",
            "fields": [
                pk(), fk("user_id", "users.id"), f("title", "string"), f("body", "text"),
                f("channel", "enum", values=["in_app", "email", "sms"]),
                f("read", "boolean", default=False), f("created_at", "datetime"),
            ],
        },
    ]


GUEST_ROLE = {"role_key": "guest", "role_name": "Public Guest",
              "description": "Browses public pages and submits inquiries without login."}
SUPER_ADMIN = {"role_key": "super_admin", "role_name": "Super Admin",
               "description": "Full system owner with access to all settings, users, and audit logs."}


