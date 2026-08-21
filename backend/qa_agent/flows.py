"""The scenario grammar: what the model is allowed to say about a user journey."""
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("qa.flows")

MAX_STEPS = 24


VERBS = {
    "GOTO": 1,
    "FILL": 2,
    "SELECT": 2,
    "CLICK": 1,
    "WAIT_FOR": 1,
    "EXPECT_TEXT": 1,
    "EXPECT_URL": 1,
    "EXPECT_VALUE": 2,
    "EXPECT_NO_ERROR": 0,
}
ASSERTIONS = {"EXPECT_TEXT", "EXPECT_URL", "EXPECT_VALUE", "EXPECT_NO_ERROR"}


SELECTOR_KINDS = ("role", "label", "placeholder", "text", "testid",
                  "field", "href")


CLICKABLE_CSS = ("button, a, [role=\"button\"], [role=\"link\"], "
                 "input[type=\"submit\"], input[type=\"button\"]")

# Stable semantic form fields.
FIELD_CSS = {
    "email": ('input[type="email"], input[name*="email" i], '
              'input[autocomplete="email"]'),
    "password": ('input[type="password"], input[name*="password" i], '
                 'input[autocomplete="current-password"], '
                 'input[autocomplete="new-password"]'),
    "search": ('input[type="search"], input[name*="search" i], '
               'input[placeholder*="search" i]'),
    "name": ('input[name="name" i], input[name*="name" i], '
             'input[autocomplete="name"]'),
    "phone": ('input[type="tel"], input[name*="phone" i], '
              'input[name*="tel" i], input[autocomplete="tel"]'),
    "date": 'input[type="date"], input[name*="date" i]',
    # Common business fields.
    "start": ('input[name*="start" i], input[name*="checkin" i], '
              'input[name*="check-in" i], input[name*="from" i]'),
    "end": ('input[name*="end" i], input[name*="until" i], '
            'input[name*="return" i], input[name*="due" i], '
            'input[name*="to" i]'),
    "price": ('input[name*="price" i], input[name*="rate" i], '
              'input[name*="cost" i], input[id*="price" i], '
              'input[id*="rate" i]'),
    "amount": ('input[name*="amount" i], input[id*="amount" i], '
               'input[name*="total" i]'),
    "number": 'input[type="number"]',
    "status": ('select[name*="status" i], select[id*="status" i], '
               '[role="combobox"][aria-label*="status" i]'),
    "rating": ('input[name*="rating" i], input[id*="rating" i], '
               'select[name*="rating" i], [role="radiogroup"] input, '
               'input[type="number"][min="1"][max="5"]'),
    "review": ('textarea[name*="review" i], textarea[name*="comment" i], '
               'textarea[id*="review" i], textarea[id*="comment" i], '
               'textarea[aria-label*="review" i], textarea[placeholder*="review" i]'),
    "quantity": ('input[name*="quantity" i], input[name*="stock" i], '
                 'input[id*="quantity" i], input[id*="stock" i]'),
    # Promo boxes rarely carry a label
    "promo": ('input[name*="promo" i], input[name*="coupon" i], '
              'input[name*="discount" i], input[name*="voucher" i], '
              'input[id*="promo" i], input[id*="coupon" i], '
              'input[placeholder*="promo" i], input[placeholder*="coupon" i], '
              'input[placeholder*="discount" i], input[placeholder*="code" i]'),
    "coupon": ('input[name*="coupon" i], input[name*="promo" i], '
               'input[name*="discount" i], input[id*="coupon" i], '
               'input[placeholder*="coupon" i], input[placeholder*="promo" i]'),
    "discount": ('input[name*="discount" i], input[name*="promo" i], '
                 'input[name*="coupon" i], input[name*="percent" i], '
                 'input[id*="discount" i], input[placeholder*="discount" i], '
                 'input[placeholder*="promo" i]'),
}

_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$", re.I)

def field_css(key: str) -> str:
    """CSS for a semantic field name observed in the app."""
    key = str(key or "").strip().lower()
    if key in FIELD_CSS:
        return FIELD_CSS[key]
    if not _FIELD_KEY_RE.fullmatch(key):
        return ""
    safe = key.replace('"', '')
    # A form may use stock_quantity in the plan.
    tokens = [t for t in re.split(r"[_-]+", safe) if t]
    if len(tokens) == 1:
        camel = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key or ""))
        tokens = [t.lower() for t in re.split(r"[_-]+", camel) if t]
    selectors = [
        f'input[name*="{safe}" i]', f'textarea[name*="{safe}" i]',
        f'select[name*="{safe}" i]', f'input[id*="{safe}" i]',
        f'textarea[id*="{safe}" i]', f'select[id*="{safe}" i]',
        f'[aria-label*="{safe}" i]', f'[placeholder*="{safe}" i]',
    ]
    if len(tokens) > 1:
        attrs = ("name", "id", "aria-label", "placeholder")
        tags = ("input", "textarea", "select")
        for tag in tags:
            for attr in attrs:
                selectors.append(tag + "".join(
                    f'[{attr}*="{tok}" i]' for tok in tokens[:4]))
    return ", ".join(dict.fromkeys(selectors))


# Name= is optional; a bare role=combobox means "any combobox"
_ROLE_RE = re.compile(r"^role=([a-z]+)(?:\s+name=(.+))?$", re.I)
_KIND_RE = re.compile(r"^(label|placeholder|text|testid|field|href)=(.+)$", re.I)
_REGEX_RE = re.compile(r"^/(.*)/([a-z]*)$", re.S)
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(email|password|role)\s*\}\}")


_PY_ONLY_RE = re.compile(r"\(\?P[<=]|\(\?#|\\A|\\Z|\(\?\(|\\p\{", re.I)


@dataclass
class Selector:
    """One locator, restricted to the five forms Playwright recommends."""
    kind: str
    pattern: str
    flags: str = ""
    role: str = ""
    is_regex: bool = True

    def key(self) -> str:
        """Identity, for the never-re-query rule."""
        return f"{self.kind}:{self.role}:{self.pattern}:{self.flags}".lower()

    def describe(self) -> str:
        head = f"{self.role} " if self.role else ""
        body = f"/{self.pattern}/{self.flags}" if self.is_regex else repr(self.pattern)
        return f"{head}{self.kind} {body}".strip()

    def compiled(self):
        """The Python matcher — a compiled regex, or a plain string."""
        if not self.is_regex:
            return self.pattern
        return re.compile(self.pattern, re.I if "i" in self.flags else 0)

    def js(self) -> str:
        """The JavaScript matcher, verbatim."""
        return (f"/{self.pattern}/{self.flags}" if self.is_regex
                else js_string(self.pattern))

    def js_locator(self, clickable: bool = False) -> str:
        if self.kind == "role":
            return f"getByRole({js_string(self.role)}, {{ name: {self.js()} }})"
        if self.kind == "testid":
            return f"getByTestId({self.js()})"
        if self.kind == "field":
            return f"locator({js_string(field_css(self.pattern))})"
        if self.kind == "href":
            # Href selectors are accepted only for same-app absolute paths.
            href = self.pattern.replace("\\", "\\\\").replace('"', '\\"')
            return f"locator({js_string('a[href^=\\\"' + href + '\\"]')})"
        if self.kind == "text" and clickable:
            return (f"locator({js_string(CLICKABLE_CSS)})"
                    f".filter({{ hasText: {self.js()} }})")
        return f"getBy{self.kind.capitalize()}({self.js()})"


@dataclass
class Step:
    verb: str
    selector: Selector = None
    value: str = ""
    line: int = 0

    def describe(self) -> str:
        bits = [self.verb]
        if self.selector:
            bits.append(self.selector.describe())
        if self.value:
            bits.append(self.value if "password" not in self.value.lower()
                        else "••••")
        return " :: ".join(bits)


@dataclass
class Scenario:
    title: str = "user flow"
    role: str = ""
    steps: list = field(default_factory=list)
    dropped: list = field(default_factory=list)

    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", (self.title or "flow").lower()).strip("-")
        return (s or "flow")[:40]

    def spec_path(self) -> str:
        return f"tests/e2e/{self.slug()}.spec.js"

    def is_runnable(self) -> str:
        """`""` when this scenario is worth running, else why not."""
        if not self.steps:
            return "the scenario has no steps"
        if not any(s.verb == "GOTO" for s in self.steps):
            return "the scenario never navigates anywhere"
        if not any(s.verb in ASSERTIONS for s in self.steps):

            return "the scenario asserts nothing"
        return ""


def js_string(s: str) -> str:
    return "'" + (s or "").replace("\\", "\\\\").replace("'", "\\'") \
                          .replace("\n", "\\n") + "'"


def parse_selector(raw: str):
    """`(Selector, "")` or `(None, reason)`."""
    raw = (raw or "").strip()
    if not raw:
        return None, "empty selector"

    m = _ROLE_RE.match(raw)
    if m:
        role, rest = m.group(1).lower(), (m.group(2) or "/.*/").strip()
        pat, flags, is_rx, why = _pattern(rest)
        if why:
            return None, why
        return Selector(kind="role", role=role, pattern=pat, flags=flags,
                        is_regex=is_rx), ""

    m = _KIND_RE.match(raw)
    if m:
        kind, rest = m.group(1).lower(), m.group(2).strip()

        # Identity selectors are literals.
        if kind in ("testid", "field", "href"):
            pat = rest.strip("'\"")
            flags, is_rx, why = "", False, ""
        else:
            pat, flags, is_rx, why = _pattern(rest)

        if why:
            return None, why
        if kind == "field":
            key = pat.strip().lower()
            if not field_css(key):
                return None, (f"unsafe field={pat!r}; use an observed simple "
                              "field name such as email, price or guestCount")
            pat = key
        if kind == "href":
            pat = pat.strip()
            if not pat.startswith("/") or pat.startswith("//") or \
                    not re.fullmatch(r"/[A-Za-z0-9_./?=&%#:+~-]*", pat):
                return None, "href= must be a same-app path such as /orders/new"
        return Selector(kind=kind, pattern=pat, flags=flags, is_regex=is_rx), ""

    pat, flags, is_rx, why = _pattern(raw)
    if why:
        return None, why
    return Selector(kind="text", pattern=pat, flags=flags, is_regex=is_rx), ""


def parse_url_selector(raw: str):
    """Parse an `EXPECT_URL` target without turning route patterns into text."""
    text = str(raw or "").strip()
    if text.lower().startswith("text="):
        text = text.split("=", 1)[1].strip()

    # First honor the documented selector/regex grammar exactly.
    sel, why = parse_selector(text)
    if why:
        return sel, why
    if not sel or sel.is_regex:
        return sel, why

    pat = str(sel.pattern or "").strip()
    if not pat.startswith("/"):
        return sel, why

    translated = pat
    dynamic = False
    if re.search(r"\[[^/\]]+\]", translated):
        translated = re.sub(r"\[[^/\]]+\]", r"[^/?#]+", translated)
        dynamic = True
    if re.search(r"(?<=/):[A-Za-z_][A-Za-z0-9_]*", translated):
        translated = re.sub(r"(?<=/):[A-Za-z_][A-Za-z0-9_]*", r"[^/?#]+", translated)
        dynamic = True
    if "*" in translated:
        if ".*" not in translated:
            translated = translated.replace("*", ".*")
        dynamic = True
    if ".*" in translated or ".+" in translated:
        dynamic = True

    if dynamic:
        # The same pattern is emitted as a JavaScript /.../ literal.
        translated = re.sub(r"(?<!\\)/", r"\\/", translated)
        try:
            re.compile(translated)
        except re.error:
            return sel, why
        return Selector(kind="text", pattern=translated, flags="",
                        is_regex=True), ""
    return sel, why


def _pattern(raw: str):
    """`(pattern, flags, is_regex, reason)` — reason non-empty means refuse."""
    raw = raw.strip()
    m = _REGEX_RE.match(raw)
    if not m:
        return raw.strip("'\""), "", False, ""
    src, flags = m.group(1), m.group(2).lower()
    if not src:
        return "", "", False, "an empty regex matches everything"
    if _PY_ONLY_RE.search(src):

        return "", "", True, f"/{src}/ uses syntax JavaScript does not share"
    try:
        re.compile(src)
    except re.error:
        return re.escape(src), ("i" if "i" in flags else ""), True, ""
    return src, ("i" if "i" in flags else ""), True, ""


_SIGNED_OUT = ("", "signed out", "signed-out", "logged out", "logged-out",
               "anonymous", "none", "nobody", "visitor", "public")


def parse_scenario(text: str, *, account: dict = None) -> Scenario:
    """Read the model's reply into a `Scenario`."""
    sc = Scenario()
    last_click = ""
    changed_since_click = True

    for i, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        if "::" not in line:
            if line.upper() == "EXPECT_NO_ERROR":
                sc.steps.append(Step(verb="EXPECT_NO_ERROR", line=i))
            elif line.upper() not in ("DONE", "END"):
                sc.dropped.append((i, line[:80], "not a `VERB :: …` line"))
            continue

        head, _, rest = line.partition("::")
        verb = head.strip().upper()
        rest = rest.strip()

        if verb == "FLOW":
            sc.title = rest[:80]
            continue
        if verb in ("AS", "ROLE"):
            sc.role = "" if rest.lower() in _SIGNED_OUT else rest.strip().lower()[:40]
            continue
        if verb not in VERBS:
            sc.dropped.append((i, line[:80], f"{verb} is not a verb"))
            continue
        if len(sc.steps) >= MAX_STEPS:
            sc.dropped.append((i, line[:80], f"over the {MAX_STEPS}-step cap"))
            continue

        arity = VERBS[verb]
        if arity == 0:
            sc.steps.append(Step(verb=verb, line=i))
            continue

        if verb == "GOTO":
            path = rest.split("::")[0].strip()
            if not path.startswith("/"):
                sc.dropped.append((i, line[:80], "GOTO needs a path like /login"))
                continue
            if path.startswith("//") or "://" in path:

                sc.dropped.append((i, line[:80], "GOTO must stay in this app"))
                continue
            if "[" in path or "]" in path:

                sc.dropped.append((i, line[:80],
                                   "a dynamic route needs a real id — reach it "
                                   "by clicking a link"))
                continue
            sc.steps.append(Step(verb=verb, value=path[:200], line=i))
            changed_since_click = True
            continue

        if verb in ("EXPECT_TEXT", "EXPECT_URL"):
            sel, why = (parse_url_selector(rest) if verb == "EXPECT_URL"
                        else parse_selector(rest))
            if why:
                sc.dropped.append((i, line[:80], why))
                continue
            sc.steps.append(Step(verb=verb, selector=sel, line=i))
            continue

        sel_raw, _, value = rest.partition("::")
        sel, why = parse_selector(sel_raw)
        if why:
            sc.dropped.append((i, line[:80], why))
            continue

        if verb in ("FILL", "SELECT", "EXPECT_VALUE"):
            value = value.strip()
            if not value:
                sc.dropped.append((i, line[:80], f"{verb} needs a value"))
                continue
            value = _fill_placeholders(value, account)

        if verb == "CLICK" and sel.key() == last_click and not changed_since_click:
            sc.dropped.append((i, line[:80],
                               "the exact same control was clicked twice in a row without any intervening state change"))
            continue
        if verb == "CLICK":
            last_click = sel.key()
            changed_since_click = False
        elif verb in ("FILL", "SELECT"):
            changed_since_click = True

        sc.steps.append(Step(verb=verb, selector=sel, value=value.strip(),
                             line=i))

    return sc


def _fill_placeholders(value, account):
    acc = account or {}

    def sub(m):
        return str(acc.get(m.group(1), "")) or m.group(0)
    return _PLACEHOLDER_RE.sub(sub, value)[:200]


def to_playwright_js(sc: Scenario, *, base_url="http://localhost:5173") -> str:
    """The same scenario as a real `@playwright/test` spec, for the project."""
    body = [
        "// Written by AgentForge from a generated scenario.",
        "//   npm i -D @playwright/test && npx playwright test",
        "//",
        "// Navigation waits on 'load', never 'networkidle'. The dev server's",
        "// HMR socket never closes, so a networkidle wait can only time out.",
        "import { test, expect } from '@playwright/test'",
        "",
        f"const BASE = process.env.BASE_URL || {js_string(base_url)}",
        "",
        f"test({js_string(sc.title)}, async ({{ page }}) => {{",
        "  const problems = []",
        "  page.on('pageerror', (e) => problems.push(String(e)))",
        "  page.on('console', (m) => "
        "{ if (m.type() === 'error') problems.push(m.text()) })",
        "",
    ]
    for st in sc.steps:
        body.append("  " + _js_step(st))
    body += ["})", ""]
    return "\n".join(body)


def _js_step(st: Step) -> str:
    if st.verb == "GOTO":

        return (f"await page.goto(BASE + {js_string(st.value)}, "
                f"{{ waitUntil: 'load' }})")
    if st.verb == "FILL":
        return f"await page.{st.selector.js_locator()}.first().fill({js_string(st.value)})"
    if st.verb == "SELECT":
        loc = f"page.{st.selector.js_locator()}.first()"
        return (f"await {loc}.selectOption({{ label: {js_string(st.value)} }})"
                f".catch(async () => {loc}.selectOption({js_string(st.value)}))")
    if st.verb == "CLICK":
        return (f"await page.{st.selector.js_locator(clickable=True)}"
                f".first().click()")
    if st.verb == "WAIT_FOR":
        return (f"await expect(page.{st.selector.js_locator()}.first())"
                f".toBeVisible({{ timeout: 15000 }})")
    if st.verb == "EXPECT_TEXT":
        return (f"await expect(page.locator('body'))"
                f".toContainText({st.selector.js()}, {{ timeout: 15000 }})")
    if st.verb == "EXPECT_URL":
        return f"await expect(page).toHaveURL({st.selector.js()})"
    if st.verb == "EXPECT_VALUE":
        return (f"await expect(page.{st.selector.js_locator()}.first())"
                f".toHaveValue({js_string(st.value)}, {{ timeout: 15000 }})")
    if st.verb == "EXPECT_NO_ERROR":
        return "expect(problems, problems.join('\\n')).toHaveLength(0)"
    return f"// unsupported: {st.verb}"


PLAYWRIGHT_CONFIG = """\
// Written by AgentForge.
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60000,
  retries: 0,
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:5173',
    // 'load', never 'networkidle': the dev server's HMR socket never closes,
    // so a networkidle wait can only ever time out.
    actionTimeout: 15000,
    trace: 'off',
  },
})
"""

GRAMMAR = """\
FLOW :: <what this journey proves, in a few words>
AS :: <the role from the demo accounts this journey runs as, or SIGNED OUT>
GOTO :: /path
FILL :: <selector> :: <value>
SELECT :: <selector> :: <option label or value>
CLICK :: <selector>
WAIT_FOR :: <selector>
EXPECT_TEXT :: /regex/i
EXPECT_URL :: /regex/
EXPECT_VALUE :: <selector> :: <value>
EXPECT_NO_ERROR
DONE

Selectors — one of these semantic forms, and nothing else:
    role=button name=/sign in|log in/i
    field=email
    field=password
    field=start
    field=end
    field=price
    field=amount
    field=status
    field=rating
    field=review
    field=quantity
    field=guestCount
    placeholder=/search/i
    text=/welcome back/i
    testid=order-total
    href=/orders/new

For login/authentication fields prefer `field=email` and `field=password`.
Those target input type/name/autocomplete and survive placeholder wording such
as "name@example.com". Business forms also have semantic selectors such as
`field=start`, `field=end`, `field=price`, `field=amount`, `field=status`, `field=rating`, `field=review`, and `field=quantity`.
Use them instead of guessing visible copy. A simple observed field name such as
`field=guestCount` or `field=nightlyRate` is also allowed and binds by
name/id/aria/placeholder. Use placeholder= only when that exact placeholder was
observed in source/runtime DOM.

Use href= for a link when the destination is the stable contract and its copy
may change. It accepts only a same-app absolute path.

Use testid= only when you have seen that exact `data-testid` in the app's own
source. Only some controls carry one, and only where the plan named it: across
the fifteen apps built before this change there are zero `data-testid`
attributes, and `testid=submit`, copied straight off this list, is why 3 of the
last 20 specs click a handle no page has. Every button and link has a role, so
`role=button name=/…/i` is the selector that actually finds things.

Values may use {{email}} and {{password}} — AgentForge substitutes the demo
account for the role you named on the AS line, so never write a real password,
and never type one role's email into another role's journey. Name the role whose
journey this is: a customer's journey signs in as the customer, and asserting
that it lands somewhere only an administrator can reach is a bug in the test,
not in the app.
"""
