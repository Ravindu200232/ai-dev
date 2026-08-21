"""The end-to-end pass: sign in as a real seeded account and use the app."""
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from agents.architect import FileStreamParser
from agents.tester import overlay_error

from .flows import (CLICKABLE_CSS, FIELD_CSS, GRAMMAR, PLAYWRIGHT_CONFIG,
                    Scenario, Selector, field_css, parse_scenario, to_playwright_js)
from .session import QASession
from .spec import TestFailure

log = logging.getLogger("qa.e2e")

TEMPERATURE = 0.3

# One scenario is a few dozen lines.
CALL_BUDGET = 180

STEP_TIMEOUT = 7_000
ASSERT_TIMEOUT = 8_000
GOTO_TIMEOUT = 30_000
HYDRATE_TIMEOUT = 8_000
# How many journeys the E2E stage walks.
MAX_FLOWS = 6
MAX_REAUTHOR = 3


KIND_CRASH = "CRASH"
KIND_SELECTOR = "SELECTOR"
KIND_BEHAVIOR = "BEHAVIOR"

SYSTEM = """\
You describe ONE end-to-end user journey through a Next.js app, as a scenario.

You are NOT writing code. You emit only the lines below, and AgentForge runs them
with a real browser. Anything that is not one of these verbs is discarded.

""" + GRAMMAR + """
RULES THAT DECIDE WHETHER THIS WORKS
  • Selectors must be REGEXES WITH ALTERNATION, because the exact wording
    varies per app: use /sign in|log in|login/i, never 'Sign In'. A scenario
    that pins exact wording fails on an app that is working perfectly.
  • For login/auth fields use `field=email` and `field=password`. They bind to
    input type/name/autocomplete, so `placeholder="name@example.com"` is still
    an email field. For other text inputs use the REAL placeholder shown in the
    markup, then `role=textbox name=…`. Do NOT invent `/email/i` merely because
    the field contains an email address.
  • Do NOT use `label=` unless the markup ties the label to the input with
    htmlFor/id. A visible `<label>` beside an input is not automatically a
    Playwright label.
  • RUNTIME DOM EVIDENCE IS AUTHORITATIVE. If it exposes a `data-testid`,
    accessible name, field name or href for the action, COPY that observed
    locator instead of paraphrasing it. Prefer an observed `testid=` for icon
    controls and mutation buttons because it survives copy changes. Never
    invent a star glyph, an id, or button wording that the evidence does
    not show. If a workflow requires an action but the evidence has no usable
    control, keep the business action in the scenario using its natural
    accessible name so the run exposes an APP defect; do not replace it with an
    unrelated control merely to make the test green.
  • CLICK must name a control: `role=button name=/…/i` or
    `role=link name=/…/i`. A bare text match is ambiguous with prose — a login
    page whose heading reads "Sign in to manage your tickets" puts that
    sentence before the button, so `text=/sign in/i` matches the paragraph.
  • NEVER click a control and then refer to it again. A submit button becomes
    "Signing in..." the moment it is pressed, so the second lookup finds
    nothing and reports a bug that does not exist.
  • Page identity is a URL fact, not a copywriting fact. Prefer EXPECT_URL for
    "I am on the list page for this feature". Do NOT assert a marketing
    slogan or section heading unless the runtime DOM evidence below contains it.
  • Use semantic business fields when present: field=start, field=end,
    field=price, field=amount, field=status, field=rating, field=review, field=quantity.
    A promo/coupon/discount-code box is field=promo — never field=amount, which
    is the money total, not the code input.
    You may ALSO use `field=<name-or-id>` when RUNTIME DOM EVIDENCE literally shows
    that input/select `name=` or `id=` (for example name=discount -> field=discount,
    id=itemName -> field=itemName). That is observed evidence, not invention.
    Never invent a field key that is absent from both DOM evidence and source. When the
    runtime evidence says the element is a <select>, use SELECT, not FILL.
{auth_rules}
  • Assert something the seeded data guarantees. Do not assert a number, a
    date, or a name you are guessing at. Prefer EXPECT_VALUE after editing a
    field and EXPECT_URL after navigation.
  • Prefer the shortest journey that proves the feature works end to end.
  • Between 4 and 12 steps. Finish with DONE.

Emit the scenario and nothing else — no explanation, no code fences.
"""


_NOISE = ("[Fast Refresh]", "webpack-hmr", "Download the React DevTools",
          "[browser]", "hydrat", "favicon.ico", "DevTools",
          "destination stream closed early")


def _is_noise(text: str) -> bool:
    return any(n.lower() in (text or "").lower() for n in _NOISE)


def _landed_on(current: str, route: str) -> bool:
    """Whether the browser is on `route` — the whole path, not the tail of it."""
    try:
        path = urlparse(current or "").path or "/"
    except Exception:
        return False
    return path.rstrip("/") == (route or "/").rstrip("/")

# Filler an app-agnostic reader should ignore. No domain nouns belong here —
# whatever this app is about has to come from its own routes and plan.
CONCEPT_STOPWORDS = {
    "page", "pages", "view", "views", "list", "lists", "item", "items",
    "management", "manage", "managing", "section", "sections", "detail",
    "details", "info", "information", "data", "content", "main", "header",
    "footer", "sidebar", "menu", "panel", "screen", "form", "forms", "field",
    "fields", "button", "buttons", "link", "links", "table", "tables", "row",
    "rows", "column", "columns", "card", "cards", "modal", "dialog", "toast",
    "label", "labels", "title", "heading", "text", "value", "values",
    "click", "clicks", "open", "close", "show", "hide", "your", "our",
    "this", "that", "these", "those", "there", "here", "with", "from",
    "into", "have", "been", "will", "just", "only", "also", "then", "than",
    "when", "what", "which", "were", "was", "are", "the", "and", "all",
    "new", "old", "next", "back", "more", "less", "some", "any", "each",
    "welcome", "please", "loading", "error", "success", "test", "tests",
}

_PLURAL_RULES = (
    ("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("sses", "ss"),
    ("xes", "x"), ("zes", "z"), ("ses", "s"),
)


def singular(word: str) -> str:
    """`orders` -> `order`, `categories` -> `category`, with no word list."""
    w = str(word or "").lower()
    if len(w) < 4 or w.endswith(("ss", "us", "is", "as", "os")):
        return w
    for end, repl in _PLURAL_RULES:
        if w.endswith(end) and len(w) > len(end) + 1:
            return w[:-len(end)] + repl
    return w[:-1] if w.endswith("s") else w



AUTH_RULES_WITH_ACCOUNTS = """\
  • If a journey needs another role page after login, GOTO that exact served
    route, copied from the route list above. Do not assume the login landing
    page is the page where the rest of the workflow happens.
  • Name the role on the AS line and let AgentForge sign in for you. The
    shortest journey that proves a feature is: sign in, reach the page that
    needs the session, see the thing only a signed-in person sees.
  • Roles this app really has: {roles}. Never invent another one."""

AUTH_RULES_NO_ACCOUNTS = """\
  • THIS APP HAS NO SIGN-IN. It seeds no accounts and serves no session, so
    every journey runs as `AS :: SIGNED OUT`.
  • Never write a sign-in step, never GOTO /login or /register, never FILL
    field=email or field=password, and never assert anything about being
    signed in. Those pages do not exist here, and a step that reaches for
    one fails an app that is working perfectly.
  • Prove the feature from the public pages alone: open the page, use the
    control, see the result."""


def auth_rules(accounts) -> str:
    """The sign-in half of the prompt, or the rule that there is no sign-in."""
    rows = [a for a in (accounts or []) if a.get("email") and a.get("password")]
    if not rows:
        return AUTH_RULES_NO_ACCOUNTS
    roles = sorted({str(a.get("role") or "").strip() for a in rows if a.get("role")})
    return AUTH_RULES_WITH_ACCOUNTS.format(
        roles=", ".join(roles) if roles else "one signed-in role")


def system_prompt(accounts) -> str:
    """The authoring prompt, told whether this app has people who sign in."""
    return SYSTEM.replace("{auth_rules}", auth_rules(accounts))


# What `get_by_role` will actually see. A number box is a spinbutton, not a
# textbox, so a scenario that says `role=textbox name=/price/i` misses a
# perfectly good <input type="number"> and the run blames the app for it.
INPUT_ROLE = {
    "number": "spinbutton", "range": "slider", "checkbox": "checkbox",
    "radio": "radio", "submit": "button", "button": "button",
    "reset": "button", "file": "button", "color": "button",
    "search": "searchbox", "email": "textbox", "tel": "textbox",
    "url": "textbox", "password": "textbox", "text": "textbox",
    "date": "textbox", "time": "textbox", "datetime-local": "textbox",
    "month": "textbox", "week": "textbox",
}

# Roles a person means the same thing by when they name a form field.
ROLE_FAMILY = (
    {"textbox", "searchbox", "spinbutton", "combobox"},
    {"button", "link", "menuitem"},
    {"checkbox", "switch", "radio"},
)


def role_of(tag: str, input_type: str = "") -> str:
    """The ARIA role Playwright will match on this element."""
    tag = str(tag or "").lower().strip()
    if tag == "textarea":
        return "textbox"
    if tag == "select":
        return "combobox"
    if tag == "button":
        return "button"
    if tag != "input":
        return ""
    return INPUT_ROLE.get(str(input_type or "text").lower().strip(), "textbox")


def same_role_family(wanted: str, found: str) -> bool:
    """Whether two roles are close enough that one stands in for the other."""
    wanted, found = str(wanted or "").lower(), str(found or "").lower()
    if not wanted or not found:
        return False
    if wanted == found:
        return True
    return any(wanted in group and found in group for group in ROLE_FAMILY)


# Routes that answer without a session. Anything else is app data, whatever
# the app happens to be about.
OPEN_ROUTES = {"/", "/login", "/signin", "/sign-in", "/signup", "/sign-up",
               "/register", "/about", "/contact", "/pricing", "/terms",
               "/privacy", "/forgot-password", "/reset-password"}


def needs_session(route: str) -> bool:
    """Whether a route is one only a signed-in person should see data on."""
    clean = str(route or "/").split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    return clean not in OPEN_ROUTES


__all__ = [name for name in globals() if not name.startswith("__")]
