"""Fallback domain used when the brief has no confident match."""
from __future__ import annotations

from .domains_core import GUEST_ROLE, SUPER_ADMIN, f, fk, pk, status, timestamps

GENERIC_DOMAIN: dict[str, Any] = {
    "label": "Custom SaaS",
    "system_category": "Custom Web Application with Admin Dashboard",
    "app_type_primary": "Custom SaaS Web Application",
    "keywords": [],
    "roles": [SUPER_ADMIN,
              {"role_key": "admin", "role_name": "Administrator", "description": "Manages users, records, and settings."},
              {"role_key": "staff", "role_name": "Staff", "description": "Performs day-to-day operations on records."},
              {"role_key": "member", "role_name": "Member", "description": "Self-service access to their own data."},
              GUEST_ROLE],
    "modules": ["Public Website", "Authentication", "User Management", "Core Records",
                "Dashboard & Reports", "Notifications", "Settings"],
    "tables": [
        {"table_name": "records", "description": "Primary domain records managed by the app.",
         "fields": [pk(), f("title", "string"), f("description", "text", nullable=True),
                    fk("owner_id", "users.id", nullable=True), status(["draft", "active", "archived"], "active"), *timestamps()]},
        {"table_name": "categories", "description": "Categorisation for records.",
         "fields": [pk(), f("name", "string"), status(["active", "inactive"], "active")]},
        {"table_name": "comments", "description": "Comments / activity on records.",
         "fields": [pk(), fk("record_id", "records.id"), fk("user_id", "users.id"), f("body", "text"), f("created_at", "datetime")]},
    ],
    "relationships": [
        {"from": "categories.id", "to": "records.category_id", "type": "one_to_many", "description": "A category groups many records."},
        {"from": "records.id", "to": "comments.record_id", "type": "one_to_many", "description": "A record has many comments."},
    ],
    "public_pages": [
        {"page_name": "Home", "route": "/", "sections": ["Hero", "Features", "How it works", "Pricing", "Footer"]},
        {"page_name": "Contact", "route": "/contact", "sections": ["Form", "Details"]},
    ],
    "protected_pages": [
        {"page_name": "Member Dashboard", "route": "/app", "page_type": "portal", "allowed_roles": ["member"], "functions": ["View/manage own records", "Profile"]},
        {"page_name": "Admin Dashboard", "route": "/admin", "page_type": "dashboard", "allowed_roles": ["admin", "super_admin", "staff"], "functions": ["Manage records", "Manage users", "Reports", "Settings"]},
    ],
    "workflows": [
        {"workflow_name": "Core Record Lifecycle", "steps": ["User creates a record", "Staff reviews/updates it", "Record moves through statuses", "System notifies stakeholders", "Record is archived when done"]},
    ],
    "notifications": [
        {"event": "Record Created", "recipients": ["admin", "staff"], "channels": ["in_app", "email"]},
        {"event": "Record Updated", "recipients": ["member"], "channels": ["in_app"]},
    ],
    "reports": [
        {"report_name": "Activity Report", "filters": ["date range", "status"], "exports": ["PDF", "Excel"]},
    ],
    "integrations": [
        {"name": "Email Provider", "type": "messaging", "description": "Transactional email.", "required": False},
    ],
    "feature_options": ["User accounts & roles", "Core record management", "Dashboard & reports",
                        "Notifications", "File uploads", "Public landing site", "Online payments"],
    "nfr_focus": ["Role-based access control on all protected endpoints"],
    "risks": [
        {"area": "Scope", "risk": "Under-specified domain", "severity": "Medium", "reason": "Idea is generic, so the model is a sensible default.", "mitigation": "Refine via the requirement questions and prompt customization."},
    ],
}


