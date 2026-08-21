"""Interview topic catalog."""
from __future__ import annotations

from . import app_types as catalog
from .topics_core import (
    CRUD, LANDING, RECORD_PROFILES, TOOL, Topic, _and, _app_type_options,
    _domain_tables, _field_options, _mark_auth, _offering_options, _pack,
    _palette_options, _sections_options, _snake_value, _yes_no, ans, archetype, has_auth, only_for,
    open_registration, question_set, roles_list, self_signup_roles, tables_list, wants_images,
    wants_leads,
)

TOPICS: list[Topic] = [
    Topic(
        key="app_type", kind="single", label="App type",
        intent="What kind of application this is. Drives every later question.",
        options_from=lambda s, item: _app_type_options(s),
        fallback_options=catalog.app_type_options(),

        options_locked=True,
        srs_fields=("system_category", "app_type"),
        coverage=("business_type", "app_surfaces"),
    ),
    Topic(
        key="app_name", kind="text", label="Name",
        intent="The product name shown in the header, title bar and login page.",
        placeholder="e.g. FreshMart POS",
        srs_fields=("project_name", "app_summary.app_name"),
    ),
    Topic(
        key="core_outcome", kind="text", label="Main outcome",
        intent="The one outcome that must feel successful to the person using the product. "
               "This anchors the main workflow, acceptance criteria and what the build must prove.",
        placeholder="e.g. A manager can assign a task, the assignee updates progress, and the change is saved",
        multiline=True,
        srs_fields=("app_summary.business_goal", "business_workflows", "acceptance_criteria"),
        coverage=("main_goal", "features_modules"),
    ),
    Topic(
        key="auth_roles", profiles=RECORD_PROFILES, kind="multi", label="Roles",
        intent="Which kinds of user exist, in the customer's own words. "
               "Each option must be exactly ONE role — never bundle roles "
               "together ('Manager', not 'Manager + Cashier'), because the "
               "customer picks several and each becomes its own permission set.",
        applies_to=_and(only_for(CRUD), has_auth),
        options_from=lambda s, item: [
            {"label": r.replace("_", " ").title(), "value": r}
            for r in (_pack(s).get("roles") or ["admin", "user"])
        ],
        fallback_options=[
            {"label": "Admin", "value": "admin"},
            {"label": "Staff", "value": "staff"},
            {"label": "Customer", "value": "customer"},
        ],
        srs_fields=("roles", "role_access_matrix"),
        coverage=("users_roles", "auth"),
    ),
    Topic(
        key="auth_identity", profiles=RECORD_PROFILES, kind="multi", label="Login fields",
        intent="What a user signs in and registers with.",
        applies_to=_and(only_for(CRUD), has_auth),
        fallback_options=[
            {"label": "Email + password", "value": "email"},
            {"label": "Username + password", "value": "username"},
            {"label": "Phone number", "value": "phone"},
            {"label": "Full name (on register)", "value": "full_name"},
        ],
        srs_fields=("authentication_requirement",),
        coverage=("auth",),
    ),
    Topic(
        key="account_creation", profiles=RECORD_PROFILES, kind="single", label="Account creation",
        intent="Who is allowed to create a new account.",
        applies_to=_and(only_for(CRUD), has_auth),
        fallback_options=[
            {"label": "Anyone can sign up", "value": "open"},
            {"label": "An admin creates accounts", "value": "admin_created"},
            {"label": "Invite only", "value": "invite"},
            {"label": "Request access, then approve", "value": "request"},
        ],
        srs_fields=("authentication_requirement", "roles", "role_access_matrix"),
        coverage=("auth", "users_roles"),
    ),
    Topic(
        key="signup_role", profiles=RECORD_PROFILES, kind="single", label="New account role",
        intent="The one role a public sign-up creates. Never let visitors choose a privileged role.",
        applies_to=_and(only_for(CRUD), has_auth, open_registration),
        options_from=lambda s, item: [
            {"label": r.replace("_", " ").title(), "value": r}
            for r in self_signup_roles(s)
        ],
        fallback_options=[
            {"label": "Customer", "value": "customer"},
            {"label": "User", "value": "user"},
            {"label": "Member", "value": "member"},
        ],
        srs_fields=("authentication_requirement", "roles"),
        coverage=("auth", "users_roles"),
    ),
    Topic(
        key="role_functions", profiles=RECORD_PROFILES, kind="multi", label="Role duties",
        intent="What this specific role is allowed to do. Ask once per role.",
        applies_to=_and(only_for(CRUD), has_auth),
        repeats_over=roles_list,
        options_from=lambda s, item: [
            {"label": f, "value": f} for f in (_pack(s).get("features") or [])
        ],
        fallback_options=[
            {"label": "View records", "value": "view"},
            {"label": "Create and edit records", "value": "edit"},
            {"label": "Delete records", "value": "delete"},
            {"label": "View reports", "value": "reports"},
            {"label": "Manage users", "value": "manage_users"},
        ],
        srs_fields=("functional_requirements", "main_modules", "role_access_matrix"),
        coverage=("features_modules", "users_roles"),
    ),
    Topic(
        key="page_functions", profiles=RECORD_PROFILES, kind="multi", label="Page features",
        intent="What each page should let a visitor do, since there are no roles.",
        applies_to=_and(only_for(CRUD), lambda s: not has_auth(s)),
        options_from=lambda s, item: [
            {"label": f, "value": f} for f in (_pack(s).get("features") or [])
        ],
        fallback_options=[
            {"label": "Show information", "value": "info"},
            {"label": "Collect an enquiry", "value": "enquiry"},
            {"label": "Show a gallery", "value": "gallery"},
        ],
        srs_fields=("functional_requirements", "main_modules", "public_pages"),
        coverage=("features_modules", "public_pages"),
    ),

    Topic(
        key="data_tables", profiles=RECORD_PROFILES, kind="multi", label="Data",
        intent="What kinds of records the system stores. The customer can pick "
               "several and type any you did not think of.",
        applies_to=only_for(CRUD),
        options_from=lambda s, item: [
            {"label": e, "value": e} for e in (_pack(s).get("entities") or [])
        ],
        fallback_options=[
            {"label": "Users", "value": "User"},
            {"label": "Records", "value": "Record"},
        ],
        placeholder="e.g. Purchase Order",
        srs_fields=("database_design.tables",),
        coverage=("data_entities",),
    ),
    Topic(
        key="table_entities", profiles=RECORD_PROFILES, kind="multi", label="Fields",
        intent="Which individual details are stored for this record type. Each "
               "option must be ONE field a person would fill in — 'Price', "
               "'Barcode', 'Expiry date' — never a bundle or a preset like "
               "'Basic details'. Offer fields that genuinely belong to THIS "
               "record and nothing else.",
        applies_to=only_for(CRUD),
        repeats_over=tables_list,
        options_from=_field_options,
        fallback_options=[
            {"label": "Name", "value": "name"},
            {"label": "Description", "value": "description"},
            {"label": "Status", "value": "status"},
            {"label": "Created date", "value": "created_at"},
        ],
        placeholder="e.g. Reorder level",
        srs_fields=("database_design.tables",),
        coverage=("data_entities",),
    ),

    Topic(
        key="pos_payments", kind="multi", label="Payments",
        intent="How a customer can pay at the counter. Each option is ONE way "
               "of paying.",
        profiles=("pos",),
        fallback_options=[
            {"label": "Cash", "value": "cash"},
            {"label": "Card", "value": "card"},
            {"label": "Bank transfer", "value": "transfer"},
            {"label": "Mobile wallet", "value": "wallet"},
            {"label": "On account / credit", "value": "credit"},
        ],
        placeholder="e.g. Gift voucher",
        srs_fields=("integration_requirements", "functional_requirements"),
        coverage=("payments_billing",),
    ),
    Topic(
        key="pos_receipt", kind="multi", label="Receipt",
        intent="What happens at the end of a sale.",
        profiles=("pos",),
        fallback_options=[
            {"label": "Print a receipt", "value": "print"},
            {"label": "Email the receipt", "value": "email"},
            {"label": "No receipt", "value": "none"},
        ],
        srs_fields=("notification_rules", "functional_requirements"),
        coverage=("notifications",),
    ),
    Topic(
        key="pos_stock_rules", kind="multi", label="Stock",
        intent="How stock behaves as things are sold and received.",
        profiles=("pos",),
        fallback_options=[
            {"label": "Reduce stock on every sale", "value": "decrement"},
            {"label": "Warn when stock runs low", "value": "low_stock_alert"},
            {"label": "Record stock coming in", "value": "receiving"},
            {"label": "Allow selling below zero", "value": "allow_negative"},
        ],
        srs_fields=("business_workflows", "validation_rules"),
        coverage=("special_rules",),
    ),

    Topic(
        key="saas_plans", kind="multi", label="Plans",
        intent="The subscription tiers customers can be on.",
        profiles=("saas",),
        fallback_options=[
            {"label": "Free tier", "value": "free"},
            {"label": "Starter", "value": "starter"},
            {"label": "Pro", "value": "pro"},
            {"label": "Enterprise", "value": "enterprise"},
        ],
        placeholder="e.g. Education plan",
        srs_fields=("functional_requirements",),
        coverage=("payments_billing",),
    ),
    Topic(
        key="saas_signup", kind="single", label="Sign-up",
        intent="Whether anyone can create an account or the team invites them.",
        profiles=("saas",),
        fallback_options=[
            {"label": "Anyone can sign up", "value": "open"},
            {"label": "Invite only", "value": "invite"},
            {"label": "Request access, then approve", "value": "request"},
        ],
        srs_fields=("authentication_requirement", "functional_requirements"),
        coverage=("auth",),
    ),
    Topic(
        key="saas_workspace", kind="single", label="Accounts",
        intent="Whether people work alone or share a workspace with colleagues.",
        profiles=("saas",),
        fallback_options=[
            {"label": "Each person works alone", "value": "solo"},
            {"label": "Teams share a workspace", "value": "team"},
        ],
        srs_fields=("roles", "functional_requirements"),
        coverage=("users_roles",),
    ),

    Topic(
        key="store_checkout", kind="single", label="Checkout",
        intent="Whether a shopper must have an account to buy.",
        profiles=("ecommerce",),
        fallback_options=[
            {"label": "Guest checkout allowed", "value": "guest"},
            {"label": "Account required", "value": "account"},
            {"label": "Either, shopper chooses", "value": "either"},
        ],
        srs_fields=("functional_requirements", "authentication_requirement"),
        coverage=("auth",),
    ),
    Topic(
        key="store_payments", kind="multi", label="Payments",
        intent="How a shopper pays.",
        profiles=("ecommerce",),
        fallback_options=[
            {"label": "Card online", "value": "card"},
            {"label": "Cash on delivery", "value": "cod"},
            {"label": "Bank transfer", "value": "transfer"},
            {"label": "Mobile wallet", "value": "wallet"},
        ],
        srs_fields=("integration_requirements", "functional_requirements"),
        coverage=("payments_billing",),
    ),
    Topic(
        key="store_fulfilment", kind="multi", label="Delivery",
        intent="How an order reaches the customer.",
        profiles=("ecommerce",),
        fallback_options=[
            {"label": "Delivery", "value": "delivery"},
            {"label": "Collect in store", "value": "pickup"},
            {"label": "Digital download", "value": "digital"},
        ],
        srs_fields=("business_workflows",),
        coverage=("special_rules",),
    ),

    Topic(
        key="dash_metrics", kind="multi", label="Headline figures",
        intent="The numbers someone opens this tool to check first.",
        profiles=("dashboard",),
        options_from=lambda s, item: [
            {"label": f, "value": _snake_value(f)}
            for f in (_pack(s).get("features") or [])
        ],
        fallback_options=[
            {"label": "Total records", "value": "total_records"},
            {"label": "Added this week", "value": "recent"},
            {"label": "Outstanding items", "value": "outstanding"},
        ],
        placeholder="e.g. Overdue invoices",
        srs_fields=("reporting_requirements",),
        coverage=("reports",),
    ),
    Topic(
        key="dash_exports", kind="multi", label="Exports",
        intent="How the data leaves the tool.",
        profiles=("dashboard",),
        fallback_options=[
            {"label": "CSV download", "value": "csv"},
            {"label": "Printable view", "value": "print"},
            {"label": "No export", "value": "none"},
        ],
        srs_fields=("reporting_requirements",),
        coverage=("reports",),
    ),

    Topic(
        key="blog_workflow", kind="multi", label="Publishing",
        intent="The steps a post goes through before readers see it.",
        profiles=("blog",),
        fallback_options=[
            {"label": "Draft then publish", "value": "draft_publish"},
            {"label": "Schedule for later", "value": "scheduled"},
            {"label": "Needs approval first", "value": "review"},
            {"label": "Publish immediately", "value": "immediate"},
        ],
        srs_fields=("business_workflows",),
        coverage=("special_rules",),
    ),
    Topic(
        key="blog_organisation", kind="multi", label="Organising",
        intent="How readers find their way around the posts.",
        profiles=("blog",),
        fallback_options=[
            {"label": "Categories", "value": "categories"},
            {"label": "Tags", "value": "tags"},
            {"label": "Search", "value": "search"},
            {"label": "Featured posts", "value": "featured"},
        ],
        srs_fields=("main_modules", "functional_requirements"),
        coverage=("features_modules",),
    ),
    Topic(
        key="blog_engagement", kind="multi", label="Readers",
        intent="What readers can do besides read.",
        profiles=("blog",),
        fallback_options=[
            {"label": "Leave comments", "value": "comments"},
            {"label": "Subscribe by email", "value": "subscribe"},
            {"label": "Share to social", "value": "share"},
            {"label": "Nothing — read only", "value": "none"},
        ],
        srs_fields=("functional_requirements",),
        coverage=("features_modules",),
    ),

    Topic(
        key="sections", profiles=("landing", "portfolio"), kind="multi", label="Sections",
        intent="Which bands the page is built from, top to bottom. These become "
               "the actual sections of the single page, so name what a visitor "
               "scrolls past — not app features.",
        applies_to=only_for(LANDING),
        options_from=_sections_options,
        fallback_options=[
            {"label": "Hero", "value": "hero"},
            {"label": "Services", "value": "services"},
            {"label": "About", "value": "about"},
            {"label": "Testimonials", "value": "testimonials"},
            {"label": "Pricing", "value": "pricing"},
            {"label": "Contact", "value": "contact"},
        ],
        placeholder="e.g. Before & after gallery",
        srs_fields=("public_pages",),
        coverage=("public_pages",),
    ),
    Topic(
        key="offerings", profiles=("landing", "portfolio"), kind="multi", label="What you offer",
        intent="The specific services, packages or pieces of work to show off. "
               "Each option is ONE thing a visitor could buy or book.",
        applies_to=only_for(LANDING),
        options_from=_offering_options,
        fallback_options=[
            {"label": "Standard service", "value": "standard"},
            {"label": "Premium service", "value": "premium"},
            {"label": "One-off job", "value": "one_off"},
        ],
        placeholder="e.g. Deep clean, End-of-tenancy",
        srs_fields=("main_modules", "functional_requirements"),
        coverage=("features_modules",),
    ),
    Topic(
        key="cta", profiles=("landing", "portfolio"), kind="single", label="Main action",
        intent="The one thing the page is trying to get a visitor to do. Every "
               "button on the page points at this.",
        applies_to=only_for(LANDING),
        fallback_options=[
            {"label": "Book now", "value": "book"},
            {"label": "Get a quote", "value": "quote"},
            {"label": "Contact us", "value": "contact"},
            {"label": "Buy now", "value": "buy"},
        ],
        srs_fields=("app_summary.business_goal",),
        coverage=("main_goal",),
    ),
    Topic(
        key="lead_capture", profiles=("landing", "portfolio"), kind="yes_no", label="Enquiries",
        intent="Whether the page collects enquiries the owner can read later, "
               "rather than only listing a phone number.",
        applies_to=only_for(LANDING),
        fallback_options=_yes_no(),
        srs_fields=("functional_requirements",),
        coverage=("features_modules",),
    ),
    Topic(
        key="lead_fields", profiles=("landing", "portfolio"), kind="multi", label="Enquiry form",
        intent="What the enquiry form asks for. Each option is ONE field a "
               "visitor fills in — keep it short, every extra field loses "
               "enquiries.",
        applies_to=_and(only_for(LANDING), wants_leads),
        fallback_options=[
            {"label": "Name", "value": "name"},
            {"label": "Phone", "value": "phone"},
            {"label": "Email", "value": "email"},
            {"label": "Service wanted", "value": "service"},
            {"label": "Preferred date", "value": "preferred_date"},
            {"label": "Message", "value": "message"},
        ],
        placeholder="e.g. Postcode",
        srs_fields=("database_design.tables", "validation_rules"),
        coverage=("data_entities",),
    ),
    Topic(
        key="contact_channels", profiles=("landing", "portfolio"), kind="multi", label="Contact",
        intent="How a visitor can reach the business besides the form.",
        applies_to=only_for(LANDING),
        fallback_options=[
            {"label": "Phone number", "value": "phone"},
            {"label": "Email address", "value": "email"},
            {"label": "WhatsApp", "value": "whatsapp"},
            {"label": "Map / address", "value": "map"},
            {"label": "Opening hours", "value": "hours"},
        ],
        srs_fields=("integration_requirements",),
        coverage=("integrations",),
    ),
    Topic(
        key="social_proof", profiles=("landing", "portfolio"), kind="multi", label="Trust",
        intent="What convinces a visitor this business is real and good.",
        applies_to=only_for(LANDING),
        fallback_options=[
            {"label": "Customer testimonials", "value": "testimonials"},
            {"label": "Client logos", "value": "logos"},
            {"label": "Numbers (jobs done, years)", "value": "stats"},
            {"label": "Photo gallery", "value": "gallery"},
            {"label": "Certifications", "value": "certifications"},
        ],
        srs_fields=("public_pages",),
        coverage=("public_pages",),
    ),

    Topic(
        key="tool_job", profiles=("utility",), kind="text", label="The job",
        intent="In one sentence, what the tool does for someone — the thing "
               "they open it to get done.",
        applies_to=only_for(TOOL),
        placeholder="e.g. Work out a loan repayment",
        srs_fields=("app_summary.business_goal",),
        coverage=("main_goal",),
    ),
    Topic(
        key="tool_inputs", profiles=("utility",), kind="multi", label="Inputs",
        intent="What the person types in. Each option is ONE input.",
        applies_to=only_for(TOOL),
        fallback_options=[
            {"label": "A number", "value": "number"},
            {"label": "A date", "value": "date"},
            {"label": "Some text", "value": "text"},
            {"label": "A choice from a list", "value": "choice"},
            {"label": "A file", "value": "file"},
        ],
        placeholder="e.g. Loan amount",
        srs_fields=("functional_requirements", "validation_rules"),
        coverage=("data_entities",),
    ),
    Topic(
        key="tool_outputs", profiles=("utility",), kind="multi", label="Results",
        intent="What the tool shows back. Each option is ONE result.",
        applies_to=only_for(TOOL),
        fallback_options=[
            {"label": "A single figure", "value": "figure"},
            {"label": "A breakdown table", "value": "table"},
            {"label": "A converted value", "value": "converted"},
            {"label": "A downloadable file", "value": "download"},
        ],
        placeholder="e.g. Monthly repayment",
        srs_fields=("functional_requirements",),
        coverage=("features_modules",),
    ),
    Topic(
        key="save_history", profiles=("utility",), kind="yes_no", label="History",
        intent="Whether past results are kept so the person can come back to "
               "them, or the tool forgets everything on refresh.",
        applies_to=only_for(TOOL),
        fallback_options=_yes_no(),
        srs_fields=("database_design.tables",),
        coverage=("data_entities",),
    ),

    Topic(
        key="images", kind="yes_no", label="Images",
        intent="Whether the app needs generated artwork.",
        fallback_options=_yes_no(),
        srs_fields=("ui_ux_requirements",),
        coverage=("file_uploads",),
    ),
    Topic(
        key="image_kinds", kind="multi", label="Artwork",
        intent="Which images are needed.",
        applies_to=wants_images,
        fallback_options=[
            {"label": "Hero banner", "value": "banner"},
            {"label": "Logo mark", "value": "logo"},
            {"label": "Avatar placeholders", "value": "avatar"},
            {"label": "Empty-state art", "value": "empty"},
            {"label": "Social share card", "value": "og"},
        ],
        srs_fields=("ui_ux_requirements",),
        coverage=("file_uploads",),
    ),

    Topic(
        key="responsive_pwa", kind="multi", label="Devices",
        intent="Which devices matter and whether it should install as an app.",
        fallback_options=[
            {"label": "Works on mobile", "value": "responsive"},
            {"label": "Installable (PWA)", "value": "pwa"},
            {"label": "Desktop only", "value": "desktop"},
        ],
        srs_fields=("ui_ux_requirements",),
        coverage=("devices_mobile",),
    ),

    Topic(
        key="real_details", kind="text", label="Real details",
        intent="The customer's actual name, contact details and links, so the "
               "build seeds the real business rather than placeholder copy. "
               "Everything here is optional and used verbatim — never invent "
               "an address, a phone number or a profile link.",
        placeholder=("Paste whatever you have — name, address, phone, email, "
                     "website, LinkedIn, GitHub, Facebook. One per line.\n"
                     "e.g. Otier Lundora Boutique Hotel\n"
                     "No. 12/3, Galle Road, Colombo 04\n"
                     "hello@otierlundora.lk · +94 77 123 4567\n"
                     "otierlundora.lk · facebook.com/otierlundora"),
        multiline=True,
        optional=True,
        srs_fields=("app_summary.organisation", "integration_requirements"),
        coverage=("business_type", "integrations"),
    ),
    Topic(
        key="extra_notes", kind="text", label="Anything else",
        intent="Anything the customer wants that no question covered — "
               "especially what they want the site to show.",
        fixed="Last check — is there any rule, exception, must-have behaviour, or failure case that would make the finished product unacceptable if we missed it? Write as much as you like.",
        placeholder="e.g. never allow two active bookings for the same room; managers can reopen a cancelled booking; keep an audit note when a price changes",
        optional=True,
        multiline=True,
        srs_fields=("app_summary.short_description", "functional_requirements"),
        coverage=("special_rules",),
    ),
]

