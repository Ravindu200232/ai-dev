"""App-type catalog — the structural half of the template system."""
from __future__ import annotations

import re
from typing import Any

from .domains import DOMAIN_LIBRARY, GENERIC_KEY, classify_domain, get_domain

PALETTES = {
    "business": {"primary": "#0A6ED1", "ink": "#0A294F", "canvas": "#F5F7FB",
                 "accent": "#0854A1", "danger": "#D9534F"},
    "mint":     {"primary": "#1DD1A1", "ink": "#0F172A", "canvas": "#FFFFFF",
                 "accent": "#0EA5A0", "danger": "#EF4444"},
    "navy":     {"primary": "#063970", "ink": "#0B1727", "canvas": "#FBFCFE",
                 "accent": "#38BDF8", "danger": "#DC2626"},
    "gold":     {"primary": "#F59E0B", "ink": "#0A0A0A", "canvas": "#0A0A0A",
                 "accent": "#FBBF24", "danger": "#EF4444"},
    "indigo":   {"primary": "#6366F1", "ink": "#111827", "canvas": "#F9FAFB",
                 "accent": "#A855F7", "danger": "#EF4444"},
}

APP_TYPES: dict[str, dict[str, Any]] = {
    "pos": {
        "label": "POS / Retail",
        "icon": "🧾",
        "desc": "Point of sale, stock, invoices — no landing page",
        "archetype": "fullstack-crud",
        "keywords": ["pos", "point of sale", "cashier", "billing", "invoice", "grn",
                     "stock", "inventory", "till", "counter", "receipt", "barcode",
                     "take payment", "card payment", "at the counter"],
        "shell": {"sidebar": True, "navbar": False, "public_site": False},
        "palette": "business",
        "auth_default": True,
        "default_roles": ["admin", "cashier"],
        "default_pages": [
            {"name": "Dashboard", "route": "/admin", "type": "dashboard"},
            {"name": "Sale Terminal", "route": "/admin/sale", "type": "custom"},
            {"name": "Sales History", "route": "/admin/sales", "type": "entity_crud"},
            {"name": "Products", "route": "/admin/products", "type": "entity_crud"},
            {"name": "Stock", "route": "/admin/stock", "type": "entity_crud"},
            {"name": "Customers", "route": "/admin/customers", "type": "entity_crud"},
            {"name": "Categories", "route": "/admin/categories", "type": "entity_crud"},
            {"name": "Suppliers", "route": "/admin/suppliers", "type": "entity_crud"},
            {"name": "Employees", "route": "/admin/employees", "type": "entity_crud"},
            {"name": "Reports", "route": "/admin/reports", "type": "report"},
            {"name": "Settings", "route": "/admin/settings", "type": "settings"},
        ],
        "default_entities": ["Product", "Category", "Customer", "Sale", "Supplier", "StockMovement"],
        "operations": [
            {"id": "pos_checkout", "name": "POS checkout", "method": "POST",
             "path": "/api/ops/pos-checkout",
             "effects": ["create Sale", "decrement Product.stock", "create StockMovement"]},
        ],
        "features": [
            "Barcode / quick product search", "Cart with line discounts",
            "Multiple payment methods", "Printable receipt", "Daily sales summary",
            "Low-stock alerts", "Stock receiving (GRN)", "Customer credit accounts",
        ],
    },
    "saas": {
        "label": "SaaS Platform",
        "icon": "☁️",
        "desc": "Subscription product with accounts and a dashboard",
        "archetype": "fullstack-crud",
        "keywords": ["saas", "platform", "subscription", "b2b", "crm", "workspace",
                     "management system", "portal", "tenant", "sign up and pay",
                     "other businesses", "a month for", "per month", "monthly fee"],
        "shell": {"sidebar": True, "navbar": True, "public_site": True},
        "palette": "mint",
        "auth_default": True,
        "default_roles": ["admin", "user"],
        "default_pages": [
            {"name": "Landing", "route": "/", "type": "marketing"},
            {"name": "Login", "route": "/login", "type": "auth"},
            {"name": "Register", "route": "/register", "type": "auth"},
            {"name": "Dashboard", "route": "/app", "type": "dashboard"},
            {"name": "Settings", "route": "/app/settings", "type": "settings"},
        ],
        "default_entities": ["Project", "Member", "Activity"],
        "operations": [],
        "features": [
            "Email + password accounts", "Role-based permissions", "Team workspaces",
            "Dashboard with KPIs", "Search and filtering", "CSV export",
            "Activity log", "Email notifications",
        ],
    },
    "ecommerce": {
        "label": "Online Store",
        "icon": "🛒",
        "desc": "Storefront, cart, checkout, orders",
        "archetype": "fullstack-crud",
        "keywords": ["cart", "checkout", "product", "ecommerce", "e-commerce",
                     "buy", "retail store", "order online", "online shop",
                     "online store", "pay online", "order and pay",
                     "click and collect", "pre-order", "pay for them", "posted out"],
        "shell": {"sidebar": True, "navbar": True, "public_site": True},
        "palette": "indigo",
        "auth_default": True,
        "default_roles": ["admin", "customer"],
        "default_pages": [
            {"name": "Storefront", "route": "/", "type": "marketing"},
            {"name": "Products", "route": "/products", "type": "catalog"},
            {"name": "Product Detail", "route": "/products/[id]", "type": "detail"},
            {"name": "Cart", "route": "/cart", "type": "custom"},
            {"name": "Checkout", "route": "/checkout", "type": "custom"},
            {"name": "My Orders", "route": "/orders", "type": "entity_crud"},
            {"name": "Admin Products", "route": "/admin/products", "type": "entity_crud"},
            {"name": "Admin Orders", "route": "/admin/orders", "type": "entity_crud"},
        ],
        "default_entities": ["Product", "Category", "Order", "OrderItem", "Customer"],
        "operations": [
            {"id": "place_order", "name": "Place order", "method": "POST",
             "path": "/api/ops/place-order",
             "effects": ["create Order", "create OrderItem rows", "decrement Product.stock"]},
        ],
        "features": [
            "Product catalogue with filters", "Shopping cart", "Guest checkout",
            "Order tracking", "Product reviews", "Discount codes",
            "Stock management", "Order status emails",
        ],
    },
    "dashboard": {
        "label": "Dashboard / Admin",
        "icon": "📊",
        "desc": "Internal tool built around data tables",
        "archetype": "fullstack-crud",
        "keywords": ["dashboard", "admin panel", "analytics", "metrics", "monitor",
                     "internal tool", "back office", "records", "tracker",
                     "booking system", "appointments", "bookings", "attendance",
                     "rota", "job sheet", "keep track", "who owes", "front desk",
                     "in one place", "log every"],
        "shell": {"sidebar": True, "navbar": False, "public_site": False},
        "palette": "business",
        "auth_default": True,
        "default_roles": ["admin", "viewer"],
        "default_pages": [
            {"name": "Dashboard", "route": "/admin", "type": "dashboard"},
            {"name": "Records", "route": "/admin/records", "type": "entity_crud"},
            {"name": "Reports", "route": "/admin/reports", "type": "report"},
            {"name": "Settings", "route": "/admin/settings", "type": "settings"},
        ],
        "default_entities": ["Record", "Category"],
        "operations": [],
        "features": [
            "KPI summary cards", "Charts over time", "Searchable data tables",
            "Bulk actions", "CSV / PDF export", "Audit log", "Saved filters",
        ],
    },
    "blog": {
        "label": "Blog / Content",
        "icon": "📝",
        "desc": "Posts, categories, an author admin",
        "archetype": "fullstack-crud",
        "keywords": ["blog", "article", "news", "magazine", "journal", "post",
                     "publication", "editorial", "cms"],
        "shell": {"sidebar": True, "navbar": True, "public_site": True},
        "palette": "navy",
        "auth_default": True,
        "default_roles": ["admin", "author"],
        "default_pages": [
            {"name": "Home", "route": "/", "type": "marketing"},
            {"name": "Post", "route": "/posts/[slug]", "type": "detail"},
            {"name": "Category", "route": "/category/[slug]", "type": "catalog"},
            {"name": "Admin Posts", "route": "/admin/posts", "type": "entity_crud"},
        ],
        "default_entities": ["Post", "Category", "Tag", "Comment"],
        "operations": [],
        "features": [
            "Rich text posts", "Categories and tags", "Draft / publish workflow",
            "Featured image", "Search", "Comments", "RSS feed", "SEO metadata",
        ],
    },
    "landing": {
        "label": "Landing Page",
        "icon": "🚀",
        "desc": "One-page marketing site",
        "archetype": "landing-single-page",
        "keywords": ["landing", "one page", "single page", "marketing site",
                     "brochure", "coming soon", "waitlist", "leave their email",
                     "leave their name", "tell them when we open"],
        "shell": {"sidebar": False, "navbar": True, "public_site": True},
        "palette": "navy",
        "auth_default": False,
        "default_roles": [],
        "default_pages": [
            {"name": "Home", "route": "/", "type": "marketing",
             "sections": ["Hero", "Features", "Services", "Pricing",
                          "Process", "Testimonials", "FAQ", "Contact"]},
        ],
        "default_entities": ["Lead"],
        "operations": [
            {"id": "submit_lead", "name": "Submit enquiry", "method": "POST",
             "path": "/api/ops/submit-lead", "effects": ["create Lead"]},
        ],
        "features": [
            "Hero with call to action", "Feature grid", "Pricing table",
            "Testimonials", "FAQ accordion", "Contact / quote form",
            "Sticky navigation", "SEO metadata and sitemap",
        ],
    },
    "portfolio": {
        "label": "Portfolio",
        "icon": "🎨",
        "desc": "Personal or studio showcase",
        "archetype": "landing-single-page",
        "keywords": ["portfolio", "resume", "cv", "showcase", "my work",
                     "photography", "gallery", "freelance", "personal site"],
        "shell": {"sidebar": False, "navbar": True, "public_site": True},
        "palette": "gold",
        "auth_default": False,
        "default_roles": [],
        "default_pages": [
            {"name": "Home", "route": "/", "type": "marketing",
             "sections": ["Hero", "About", "Featured work", "Services", "Contact"]},
            {"name": "Gallery", "route": "/gallery", "type": "catalog"},
            {"name": "Packages", "route": "/packages", "type": "catalog"},
            {"name": "Contact", "route": "/contact", "type": "custom"},
        ],
        "default_entities": ["Project", "Inquiry"],
        "operations": [
            {"id": "submit_inquiry", "name": "Submit inquiry", "method": "POST",
             "path": "/api/ops/submit-inquiry", "effects": ["create Inquiry"]},
        ],
        "features": [
            "Image gallery with lightbox", "Project case studies", "About / bio",
            "Service packages with pricing", "Contact form", "Social links",
            "Downloadable CV", "Dark theme",
        ],
    },
    "utility": {
        "label": "Utility / Tool",
        "icon": "🔧",
        "desc": "Single-purpose tool — calculator, calendar",
        "archetype": "single-tool",
        "keywords": ["calculator", "converter", "timer", "clock", "weather",
                     "currency", "generator", "calendar", "tool", "utility",
                     "planner", "stopwatch", "works out", "nothing is kept",
                     "on the spot"],
        "shell": {"sidebar": False, "navbar": True, "public_site": False},
        "palette": "indigo",
        "auth_default": False,
        "default_roles": [],
        "default_pages": [
            {"name": "Tool", "route": "/", "type": "custom"},
            {"name": "History", "route": "/history", "type": "entity_crud"},
            {"name": "Settings", "route": "/settings", "type": "settings"},
        ],
        "default_entities": ["Entry"],
        "operations": [],
        "features": [
            "Core tool interaction", "Keyboard shortcuts", "Saved history",
            "Light / dark theme", "Mobile-first layout", "Offline support",
        ],
    },

    "other": {
        "label": "Other",
        "icon": "✨",
        "desc": "Something else — tell us what it does",
        "archetype": "fullstack-crud",
        "keywords": [],
        "shell": {"sidebar": True, "navbar": True, "public_site": False},
        "palette": "indigo",
        "auth_default": True,
        "default_roles": ["admin", "user"],
        "default_pages": [
            {"name": "Dashboard", "route": "/admin", "type": "dashboard"},
        ],
        "default_entities": ["Record"],
        "operations": [],
        "features": [],
    },
}

DEFAULT_APP_TYPE = "saas"

ARCHETYPES = (
    "fullstack-crud",
    "public-content",
    "landing-single-page",
    "single-tool",
)

PROFILE_CONTRACTS: dict[str, dict[str, Any]] = {
    "pos": {
        "auth_policy": "required",
        "required_pages": ["login", "dashboard", "sale_terminal"],
        "optional_pages": ["products", "inventory", "sales_history",
                           "customers", "suppliers", "reports", "settings"],
        "prohibited_page_types": ["marketing"],
        "base_operations": ["pos_checkout"],
        "question_set": "pos",
    },
    "saas": {
        "auth_policy": "required",
        "required_pages": ["marketing_home", "login", "register", "dashboard"],
        "optional_pages": ["settings", "team", "billing", "reports"],
        "prohibited_page_types": [],
        "base_operations": [],
        "question_set": "saas",
    },
    "ecommerce": {
        "auth_policy": "required",
        "required_pages": ["storefront", "product_detail", "cart", "checkout",
                           "admin_products", "admin_orders"],
        "optional_pages": ["account", "orders", "wishlist", "login", "register"],
        "prohibited_page_types": [],
        "base_operations": ["place_order"],
        "question_set": "ecommerce",
    },
    "dashboard": {
        "auth_policy": "required",
        "required_pages": ["login", "dashboard"],
        "optional_pages": ["reports", "settings", "users"],
        "prohibited_page_types": ["marketing"],
        "base_operations": [],
        "question_set": "dashboard",
    },
    "blog": {
        "auth_policy": "required",
        "required_pages": ["public_home", "post_detail", "login", "admin_posts"],
        "optional_pages": ["categories", "search", "about", "settings"],
        "prohibited_page_types": [],
        "base_operations": ["publish_post"],
        "question_set": "blog",
    },
    "landing": {
        "auth_policy": "disabled",
        "required_pages": ["marketing_home"],
        "optional_pages": ["thank_you"],
        "prohibited_page_types": ["dashboard", "entity_crud", "settings", "auth"],
        "base_operations": ["submit_enquiry"],
        "question_set": "landing",
    },
    "portfolio": {
        "auth_policy": "disabled",
        "required_pages": ["marketing_home", "work"],
        "optional_pages": ["project_detail", "services", "contact", "about"],
        "prohibited_page_types": ["dashboard", "settings", "auth"],
        "base_operations": ["submit_enquiry"],
        "question_set": "portfolio",
    },
    "utility": {
        "auth_policy": "disabled",
        "required_pages": ["tool"],
        "optional_pages": ["history", "settings"],
        "prohibited_page_types": ["dashboard", "marketing", "auth"],
        "base_operations": [],
        "question_set": "utility",
    },
    "other": {
        "auth_policy": "optional",
        "required_pages": [],
        "optional_pages": [],
        "prohibited_page_types": [],
        "base_operations": [],
        "question_set": "other",
    },
}

PLACEHOLDER_ENTITIES = {"Project", "Member", "Activity", "Record", "Entry"}

for _key, _profile in APP_TYPES.items():
    _profile.setdefault("key", _key)
    _profile["base_entities"] = list(_profile.get("default_entities") or [])
    for _field, _value in PROFILE_CONTRACTS.get(_key, {}).items():
        _profile.setdefault(_field, _value)

    _profile.setdefault("auth_policy",
                        "required" if _profile.get("auth_default") else "disabled")
    for _field in ("required_pages", "optional_pages", "prohibited_page_types",
                   "base_operations"):
        _profile.setdefault(_field, [])
    _profile.setdefault("question_set", _key)
del _key, _profile

_ARCHETYPE_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("single-tool", ("calculator", "converter", "generator", "timer",
                     "counter", "single tool", "one page tool", "utility")),
    ("landing-single-page", ("landing", "marketing page", "one page site",
                             "brochure", "coming soon", "waitlist")),
    ("public-content", ("blog", "news", "articles", "magazine", "portfolio",
                        "gallery", "showcase", "publication", "cms")),
]

def resolve_archetype(text: str, fallback: str = "fullstack-crud") -> str:
    """Map a free-text description onto exactly one archetype."""
    lowered = str(text or "").lower()
    for archetype, words in _ARCHETYPE_HINTS:
        if any(word in lowered for word in words):
            return archetype
    return fallback if fallback in ARCHETYPES else "fullstack-crud"

def get_app_type(key: str) -> dict[str, Any]:
    return APP_TYPES.get(key, APP_TYPES[DEFAULT_APP_TYPE])

def profile_contract(key: str, idea: str = "") -> dict[str, Any]:
    """The decisions this app type fixes, which no later stage may overwrite."""
    profile = get_app_type(key)
    archetype = profile["archetype"]
    if key == "other":
        archetype = resolve_archetype(idea, archetype)
    shell = profile.get("shell") or {}
    return {
        "app_type": key if key in APP_TYPES else DEFAULT_APP_TYPE,
        "archetype": archetype,
        "auth_policy": profile["auth_policy"],
        "auth_locked": profile["auth_policy"] in ("required", "disabled"),
        "shell_locked": True,
        "public_site": bool(shell.get("public_site")),
        "sidebar": bool(shell.get("sidebar")),
        "navbar": bool(shell.get("navbar")),
        "required_pages": list(profile["required_pages"]),
        "optional_pages": list(profile["optional_pages"]),
        "prohibited_page_types": list(profile["prohibited_page_types"]),
        "base_entities": list(profile["base_entities"]),
        "base_operations": list(profile["base_operations"]),
        "default_roles": list(profile.get("default_roles") or []),
        "question_set": profile["question_set"],
    }

def app_type_options() -> list[dict]:
    """Shaped for the interview's first question."""
    return [
        {"label": v["label"], "value": k, "hint": v["desc"], "icon": v.get("icon", "")}
        for k, v in APP_TYPES.items()
    ]

_WORD_RE = re.compile(r"[a-z0-9]+")

GUESS_FLOOR = 0.6

def guess_app_type(text: str) -> tuple[str, float]:
    """Keyword-score the idea against APP_TYPES."""
    low = (text or "").lower()
    tokens = set(_WORD_RE.findall(low))
    if not tokens:
        return "other", 0.0

    scores = {}
    for key, spec in APP_TYPES.items():
        hits = 0
        for kw in spec["keywords"]:
            if " " in kw:
                if kw in low:
                    hits += 2
            elif kw in tokens:
                hits += 1
        scores[key] = hits

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "other", 0.0
    total = sum(scores.values()) or 1
    share = scores[best] / total
    volume = min(scores[best], 4) / 4
    conf = min(0.97, 0.15 + 0.30 * share + 0.50 * volume)
    return best, round(conf, 2)

def build_pack(app_type_key: str, idea: str = "") -> dict[str, Any]:
    """Merge the structural app type with the inferred business domain."""
    app = get_app_type(app_type_key)
    domain_key, confidence, domain_label = classify_domain(idea)
    domain = get_domain(domain_key)

    if confidence >= 0.6 and app["auth_default"]:
        roles = [r["role_key"] for r in domain["roles"]
                 if r["role_key"] not in ("guest", "super_admin")]
    else:
        roles = list(app["default_roles"])

    if confidence >= 0.6:
        entities = []
        for t in domain["tables"]:
            name = _singular_pascal(t["table_name"])
            if name not in entities:
                entities.append(name)
        for name in app["default_entities"]:
            if name not in entities and name not in PLACEHOLDER_ENTITIES:
                entities.append(name)
    else:
        entities = list(app["default_entities"])

    if app["archetype"] in ("landing-single-page", "single-tool"):
        entities = entities[:1]

    features = list(app["features"])
    if confidence >= 0.6:
        for feat in domain.get("feature_options", []):
            if feat not in features:
                features.append(feat)

    return {
        "app_type": app_type_key if app_type_key in APP_TYPES else DEFAULT_APP_TYPE,
        "app_label": app["label"],
        "archetype": app["archetype"],

        "question_set": app["question_set"],
        "auth_policy": app["auth_policy"],
        "required_pages": list(app["required_pages"]),
        "optional_pages": list(app["optional_pages"]),
        "prohibited_page_types": list(app["prohibited_page_types"]),
        "base_operations": list(app["base_operations"]),
        "shell": dict(app["shell"]),
        "palette": PALETTES[app["palette"]],
        "palette_name": app["palette"],
        "auth_default": app["auth_default"],
        "roles": roles,
        "entities": entities,
        "features": features,
        "pages": [dict(p) for p in app["default_pages"]],
        "operations": [dict(o) for o in app["operations"]],
        "domain": domain_key,
        "domain_label": domain_label,
        "domain_confidence": confidence,
        "domain_modules": domain.get("modules", []),
        "domain_tables": domain.get("tables", []),
        "domain_workflows": domain.get("workflows", []),
        "domain_public_pages": domain.get("public_pages", []),
        "domain_protected_pages": domain.get("protected_pages", []),
        "domain_integrations": domain.get("integrations", []),
    }

_ES_PLURAL = ("sses", "xes", "zes", "ches", "shes")

def _singular_pascal(table_name: str) -> str:
    """`sales_orders` -> `SalesOrder`."""
    name = table_name.rstrip()
    if name.endswith("ies"):
        name = name[:-3] + "y"
    elif name.endswith(_ES_PLURAL):
        name = name[:-2]
    elif name.endswith("s") and not name.endswith("ss"):
        name = name[:-1]
    return "".join(p.capitalize() for p in name.split("_"))

__all__ = [
    "APP_TYPES", "DEFAULT_APP_TYPE", "PALETTES", "DOMAIN_LIBRARY", "GENERIC_KEY",
    "ARCHETYPES", "get_app_type", "profile_contract", "app_type_options",
    "guess_app_type", "build_pack", "resolve_archetype",
]
