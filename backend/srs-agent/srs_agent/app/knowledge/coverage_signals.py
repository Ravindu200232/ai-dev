"""Which coverage areas the brief already answers by itself."""
from __future__ import annotations

BUSINESS_INDICATORS = (
    "service", "services", "shop", "store", "website", "web site", "platform",
    "app", "application", "system", "portal", "software", "tool", "marketplace",
    "business", "company", "agency", "startup", "cleaning", "booking", "rental",
    "delivery", "management", "ecommerce", "e-commerce", "hotel", "school",
    "clinic", "hospital", "restaurant", "cafe", "gym", "fitness", "salon",
    "repair", "tutoring", "consulting", "real estate", "pharmacy", "retail",
    "pos", "vehicle", "car", "food", "grocery", "event", "blog", "social",
)

_SIGNALS: dict[str, list[str]] = {
    "business_type": list(BUSINESS_INDICATORS),
    "main_goal": ["goal", "so that", "in order to", "helps", "manage", "allow", "let", "enable",
                  "doing", "provide", "offer", "where", "can"],
    "users_roles": ["admin", "user", "customer", "staff", "role", "guest", "manager", "owner",
                    "client", "member", "employee", "people", "team"],
    "auth": ["login", "register", "sign in", "sign up", "account", "password", "auth"],
    "public_pages": ["landing", "website", "web site", "public", "home page", "marketing", "site"],
    "features_modules": ["feature", "module", "manage", "track", "report", "booking", "order",
                         "cleaning", "schedule", "inventory", "payment"],
    "data_entities": ["table", "record", "data", "inventory", "product", "booking", "order",
                      "customer", "room", "service", "schedule"],
    "payments_billing": ["pay", "payment", "billing", "invoice", "checkout", "price", "subscription"],
    "reports": ["report", "analytics", "dashboard", "kpi", "revenue", "insight"],
    "notifications": ["notify", "notification", "email", "sms", "alert", "reminder", "whatsapp"],
    "file_uploads": ["upload", "file", "document", "image", "photo", "attachment", "gallery"],
    "integrations": ["integrate", "integration", "api", "third party", "gateway", "sync", "maps", "stripe"],
    "security_privacy": ["secure", "security", "privacy", "gdpr", "permission", "encrypt", "sensitive"],
    "performance": ["fast", "performance", "scale", "concurrent", "load", "users at once"],
    "devices_mobile": ["mobile", "phone", "pwa", "responsive", "tablet", "android", "ios"],
    "languages": ["language", "multilingual", "sinhala", "tamil", "english", "bilingual"],
    "deployment_stack": ["deploy", "cloud", "aws", "azure", "docker", "stack", "react", "node", "on-premise"],
    "special_rules": ["approval", "workflow", "rule", "policy", "approve"],
    "app_surfaces": ["portal", "dashboard", "pos", "admin", "app", "mobile"],
}


def covered(area: str, brief: str) -> bool:
    b = (brief or "").lower()
    return any(k in b for k in _SIGNALS.get(area, []))
