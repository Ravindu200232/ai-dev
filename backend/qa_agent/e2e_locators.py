"""Playwright locator resolution and DOM-aware selector recovery."""
from .e2e_common import *


class E2ELocatorsMixin:
    @staticmethod
    def _locator(page, sel, clickable=False):
        """Must mirror `Selector.js_locator` exactly."""
        if sel.kind == "role":
            return page.get_by_role(sel.role, name=sel.compiled())
        if sel.kind == "label":
            return page.get_by_label(sel.compiled())
        if sel.kind == "placeholder":
            return page.get_by_placeholder(sel.compiled())
        if sel.kind == "testid":
            return page.get_by_test_id(sel.pattern)
        if sel.kind == "field":
            return page.locator(field_css(sel.pattern))
        if sel.kind == "href":
            # Same selector emitted by Selector.js_locator.
            safe = sel.pattern.replace("\\", "\\\\").replace('"', '\\"')
            return page.locator(f'a[href^="{safe}"]')
        if clickable:
            return page.locator(CLICKABLE_CSS).filter(has_text=sel.compiled())
        return page.get_by_text(sel.compiled())

    @staticmethod
    def _count(locator) -> int:
        try:
            return locator.count()
        except Exception:
            return 0

    @staticmethod
    def _field_hint(sel) -> str:
        """Which semantic input the authored locator was trying to name."""
        if str(getattr(sel, "kind", "") or "").lower() == "field":
            key = str(getattr(sel, "pattern", "") or "").strip()
            if field_css(key):
                return key.lower()
        text = " ".join(filter(None, [getattr(sel, "kind", ""),
                                      getattr(sel, "role", ""),
                                      getattr(sel, "pattern", "")])).lower()
        # Order matters: password often sits in a phrase that also.
        for key, words in (
            ("password", ("password", "passcode", "passwd")),
            ("email", ("email", "e-mail", "mail")),
            # Promo wording must win before "amount"/"discount" grab it.
            ("promo", ("promo", "coupon", "voucher", "promo code",
                       "coupon code", "discount code", "gift code")),
            ("price", ("price", "pricing", "rate", "cost", "fee")),
            ("amount", ("amount", "total", "charge")),
            ("status", ("status", "state")),
            ("rating", ("rating", "star", "stars", "score")),
            ("review", ("review", "comment", "feedback", "experience")),
            ("quantity", ("quantity", "stock", "qty")),
            ("discount", ("discount", "percentage", "percent", "pct")),
            ("category", ("category", "type", "group")),
            ("description", ("description", "details", "summary")),
            ("start", ("start", "checkin", "check-in", "from")),
            ("end", ("end", "until", "return", "due", "expiry", "to")),
            ("search", ("search", "find")),
            ("phone", ("phone", "telephone", "mobile", "tel")),
            ("date", ("date", "day")),
            ("name", ("name",)),
        ):
            if any(w in text for w in words):
                return key
        return ""

    @staticmethod
    def _normal_words(text: str) -> set[str]:
        raw = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
        stop = {"button", "link", "role", "testid", "text", "the", "a", "an",
                "now", "this", "that", "to", "for", "my"}
        return {w for w in raw.split() if len(w) > 2 and w not in stop}

    def _href_fallback(self, page, expected) -> Selector:
        if not expected or expected.verb != "EXPECT_URL" or not expected.selector:
            return None
        pat = expected.selector.compiled()
        try:
            hrefs = page.locator("a[href]").evaluate_all(
                """els => els.map(e => e.getAttribute('href') || '').filter(Boolean)""")
        except Exception:
            return None
        for href in hrefs or []:
            try:
                parsed = urlparse(href)
                path = parsed.path or href
                matched = (pat.search(href) if hasattr(pat, "search") else pat in href)
                if not matched:
                    matched = (pat.search(path) if hasattr(pat, "search") else pat in path)
                if matched and path.startswith("/") and not path.startswith("//"):
                    return Selector(kind="href", pattern=path, is_regex=False)
            except Exception:
                continue
        return None

    def _fuzzy_click_fallback(self, page, sel) -> Selector:
        """Recover a missing action selector from controls the DOM really has."""
        try:
            rows = page.locator(CLICKABLE_CSS).evaluate_all(
                """els => els.slice(0, 100).map(e => {
                    const tag = (e.tagName || '').toLowerCase()
                    const role = (e.getAttribute('role') ||
                        (tag === 'a' ? 'link' : 'button')).toLowerCase()
                    const testid = e.getAttribute('data-testid') || ''
                    const action = e.getAttribute('data-action') || ''
                    const title = e.getAttribute('title') || ''
                    const value = e.value || ''
                    const name = (e.getAttribute('aria-label') || e.innerText ||
                        value || title || testid || action || '')
                        .replace(/\\s+/g, ' ').trim()
                    return { role, name, href: e.getAttribute('href') || '',
                             testid, action, title, value }
                })""")
        except Exception:
            return None

        desired_text = re.sub(r"[^a-z0-9]+", " ",
                              getattr(sel, "pattern", "").lower()).strip()
        desired = self._normal_words(getattr(sel, "pattern", ""))
        # Verb families every app shares. A domain word never belongs here —
        # the app's own wording arrives through the DOM evidence instead.
        synonym_groups = [
            {"book", "reserve", "request", "schedule", "order", "claim"},
            {"pay", "paid", "payment"},
            {"price", "rate", "cost"},
            # "update" used to sit in both of the next two groups
            {"save", "apply", "submit", "confirm"},
            {"edit", "change", "modify"},
            {"status", "state"},
            {"review", "comment", "feedback"},
            {"star", "rating", "rate"},
            {"add", "create", "new"},
            {"delete", "remove"},
            {"login", "signin", "sign"},
        ]
        synonyms = {}
        for group in synonym_groups:
            for word in group:
                synonyms.setdefault(word, set()).update(group)

        def expanded(words):
            out = set(words)
            for w in list(out):
                out |= synonyms.get(w, set())
            return out

        want = expanded(desired)
        scored = []
        for row in rows or []:
            corpus = " ".join(str(row.get(k) or "")
                              for k in ("name", "testid", "action", "title", "value"))
            name = str(row.get("name") or row.get("testid") or
                       row.get("action") or row.get("title") or "").strip()
            norm = re.sub(r"[^a-z0-9]+", " ", corpus.lower()).strip()
            got = expanded(self._normal_words(corpus))
            score = 0
            if desired_text and desired_text in norm:
                score = 100
            elif want and got:
                overlap = len(want & got)
                score = int(76 * overlap / max(1, len(set(desired) or want)))
            if getattr(sel, "role", "") and row.get("role") == sel.role:
                score += 10
            if getattr(sel, "kind", "") == "testid" and row.get("testid"):
                test_words = expanded(self._normal_words(str(row.get("testid") or "")))
                if want & test_words:
                    score += 18
            if score:
                scored.append((score, row, name))
        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored or scored[0][0] < 56:
            return None
        if len(scored) > 1 and scored[1][0] == scored[0][0] and scored[0][0] < 90:
            return None
        _, row, name = scored[0]
        if row.get("testid"):
            return Selector(kind="testid", pattern=str(row["testid"]),
                            is_regex=False)
        if not name:
            return None
        return Selector(kind="role", role=row.get("role") or "button",
                        pattern=re.escape(name), flags="i", is_regex=True)

    def _source_testid_fallback(self, page, sel):
        """Use a declared source testid when its words match the requested action."""
        desired = self._normal_words(" ".join([
            str(getattr(sel, "pattern", "") or ""),
            str(getattr(sel, "role", "") or "")]))
        if not desired:
            return None
        scored = []
        for tid in self.testids_in_app():
            words = self._normal_words(tid.replace('-', ' '))
            overlap = len(desired & words)
            if overlap:
                scored.append((overlap / max(1, len(desired)), tid))
        scored.sort(reverse=True)
        if not scored or scored[0][0] < 0.5:
            return None
        tid = scored[0][1]
        loc = page.get_by_test_id(tid)
        try:
            loc.first.wait_for(state="visible", timeout=2200)
        except Exception:
            pass
        return Selector(kind="testid", pattern=tid, is_regex=False) if self._count(loc) else None

    @staticmethod
    def _field_rows(page, limit: int = 80) -> list:
        """Structured form controls from the live DOM."""
        try:
            return page.locator("input, textarea, select").evaluate_all(
                r"""(els) => els.slice(0, 80).map(e => {
                    const labels = e.labels ? [...e.labels].map(x =>
                      (x.innerText || x.textContent || '').replace(/\s+/g,' ').trim()
                    ).filter(Boolean) : []
                    let visual = ''
                    const prev = e.previousElementSibling
                    if (prev && prev.tagName && prev.tagName.toLowerCase() === 'label')
                      visual = (prev.innerText || prev.textContent || '').replace(/\s+/g,' ').trim()
                    if (!visual && e.parentElement) {
                      const lab = e.parentElement.querySelector(':scope > label')
                      if (lab) visual = (lab.innerText || lab.textContent || '').replace(/\s+/g,' ').trim()
                    }
                    return {
                      tag: (e.tagName || '').toLowerCase(), type: e.type || '',
                      name: e.name || '', id: e.id || '',
                      placeholder: e.placeholder || '',
                      aria: e.getAttribute('aria-label') || '',
                      labelledby: e.getAttribute('aria-labelledby') || '',
                      autocomplete: e.autocomplete || '',
                      label: labels.join(' '), visualLabel: visual,
                      testid: e.getAttribute('data-testid') || '',
                      disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true'
                    }
                })""")
        except Exception:
            return []

    def _field_selector_from_rows(self, sel, rows):
        """Best stable field selector from a structured DOM field inventory."""
        if not rows:
            return None
        desired_text = " ".join(filter(None, [
            str(getattr(sel, "pattern", "") or ""),
            str(getattr(sel, "role", "") or ""),
            self._field_hint(sel),
        ]))
        desired = self._normal_words(desired_text)
        hint = self._field_hint(sel)
        if hint:
            desired |= self._normal_words(hint)
        if not desired and hint:
            desired = {hint}
        if not desired:
            return None

        synonyms = {
            "qty": {"qty", "quantity", "stock"},
            "quantity": {"qty", "quantity", "stock"},
            "stock": {"qty", "quantity", "stock"},
            "discount": {"discount", "percent", "percentage", "pct", "off",
                         "promo", "coupon", "code"},
            "percent": {"discount", "percent", "percentage", "pct"},
            "promo": {"promo", "coupon", "voucher", "discount", "code"},
            "coupon": {"promo", "coupon", "voucher", "discount", "code"},
            "code": {"promo", "coupon", "voucher", "discount", "code"},
            "amount": {"amount", "total", "charge"},
            "title": {"title", "name"},
            "name": {"name", "title"},
            "description": {"description", "details", "summary"},
            "category": {"category", "type", "group"},
        }
        def expand(words):
            out = set(words)
            for w in list(out):
                out |= synonyms.get(w, set())
            return out
        want = expand(desired)
        scored = []
        for row in rows:
            corpus = " ".join(str(row.get(k) or "") for k in (
                "name", "id", "placeholder", "aria", "label", "visualLabel",
                "autocomplete", "testid", "type"))
            got = expand(self._normal_words(corpus))
            overlap = len(want & got)
            if not overlap:
                continue
            score = int(80 * overlap / max(1, len(desired)))
            attrs = " ".join(str(row.get(k) or "") for k in
                             ("name", "id", "placeholder", "aria", "testid"))
            attr_words = expand(self._normal_words(attrs))
            if want & attr_words:
                score += 22
            if hint and hint in str(row.get("name") or "").lower():
                score += 18
            if hint and hint in str(row.get("id") or "").lower():
                score += 14
            real = role_of(row.get("tag"), row.get("type"))
            wanted = str(getattr(sel, "role", "") or "")
            if wanted and real:
                score += 8 if wanted == real else (
                    5 if same_role_family(wanted, real) else -4)
            if row.get("disabled"):
                score -= 20
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored or scored[0][0] < 48:
            return None
        if len(scored) > 1 and scored[1][0] >= scored[0][0] - 3:
            return None
        row = scored[0][1]
        if row.get("testid"):
            return Selector(kind="testid", pattern=str(row["testid"]), is_regex=False)
        for key in (row.get("name"), row.get("id")):
            key = str(key or "").strip()
            if key and field_css(key):
                return Selector(kind="field", pattern=key, is_regex=False)
        if row.get("placeholder"):
            return Selector(kind="placeholder",
                            pattern=re.escape(str(row["placeholder"])),
                            flags="i", is_regex=True)
        if row.get("aria"):
            role = role_of(row.get("tag"), row.get("type")) or "textbox"
            return Selector(kind="role", role=role,
                            pattern=re.escape(str(row["aria"])), flags="i",
                            is_regex=True)
        return None


    # Everything the live DOM offered as a way to name a control. A patch that
    # points at one of these is grounded even when the mechanical fallback
    # could not build it — the model read the same evidence and found a
    # handle the fallback's scoring rules threw away.
    def observed_handles(self) -> set:
        ev = dict(getattr(self, "_last_run_evidence", {}) or {})
        out = set()
        for row in (ev.get("fields") or []) + (ev.get("controls") or []):
            if not isinstance(row, dict):
                continue
            for key in ("name", "id", "aria", "placeholder", "testid",
                        "action", "title", "label", "value"):
                got = str(row.get(key) or "").strip()
                if len(got) > 1:
                    out.add(got.lower())
            for label in (row.get("labels") or []):
                got = str(label or "").strip()
                if len(got) > 1:
                    out.add(got.lower())
        return out

    def patch_is_observed(self, patch: str) -> bool:
        """Whether a proposed scenario line names something the DOM really had."""
        line = str(patch or "").strip()
        if not line or line.upper().startswith("DROP"):
            return False
        handles = self.observed_handles()
        if not handles:
            return False
        # The words the patch tries to find the control by.
        wanted = re.findall(r"(?:name=|field=|testid=|placeholder=|href=|text=)"
                            r"/?([^/\n:]{2,60}?)/?(?:i\b|\s|$)", line)
        for raw in wanted:
            probe = re.sub(r"[^a-z0-9 ]+", " ", str(raw).lower()).strip()
            if not probe:
                continue
            if any(probe in h or h in probe for h in handles):
                return True
        return False

    def _fuzzy_field_fallback(self, page, sel):
        """Resolve a missing textbox/combobox selector from observed fields."""
        return self._field_selector_from_rows(sel, self._field_rows(page))

    def dom_grounded_test_patch(self, failure, scenario=None) -> str:
        """Build one TEST_PATCH from the browser evidence without an LLM call."""
        ev = dict(getattr(self, "_last_run_evidence", {}) or {})
        rows = ev.get("fields") or []
        if not rows or scenario is None:
            return ""
        try:
            idx = self._failure_step_index(scenario, failure)
            if idx < 0:
                return ""
            st = scenario.steps[idx]
        except Exception:
            return ""
        if st.verb not in ("FILL", "SELECT", "EXPECT_VALUE") or not st.selector:
            return ""
        replacement = self._field_selector_from_rows(st.selector, rows)
        if replacement is None:
            return ""
        if st.verb == "EXPECT_VALUE":
            verb = "EXPECT_VALUE"
        else:
            # If the observed target is a select.
            matched = None
            key = str(getattr(replacement, "pattern", "") or "").lower()
            for row in rows:
                attrs = {str(row.get("name") or "").lower(),
                         str(row.get("id") or "").lower(),
                         str(row.get("testid") or "").lower()}
                if key and key in attrs:
                    matched = row; break
            verb = "SELECT" if matched and matched.get("tag") == "select" else st.verb
        sel_text = replacement.describe()
        # describe() is human-oriented; emit parser grammar explicitly.
        if replacement.kind == "field":
            grammar_sel = f"field={replacement.pattern}"
        elif replacement.kind == "testid":
            grammar_sel = f"testid={replacement.pattern}"
        elif replacement.kind == "placeholder":
            grammar_sel = f"placeholder=/{replacement.pattern}/{replacement.flags}"
        elif replacement.kind == "role":
            grammar_sel = f"role={replacement.role} name=/{replacement.pattern}/{replacement.flags}"
        else:
            return ""
        return f"{verb} :: {grammar_sel} :: {st.value}"[:1200]

    # What a control DOES, by the verb in its name.
    _ACTION_CLASSES = {
        "commit": {"save", "submit", "confirm", "apply", "update", "pay",
                   "place", "post", "send", "book", "reserve", "commit",
                   "complete", "finish", "approve", "publish"},
        "open": {"edit", "modify", "change", "open", "view", "details",
                 "manage", "expand", "show", "detail"},
        "create": {"add", "create", "new"},
        "destroy": {"delete", "remove", "cancel", "reject", "clear"},
        "auth": {"login", "signin", "signup", "register", "logout", "signout"},
    }

    @classmethod
    def _action_classes(cls, text) -> set:
        words = set(re.split(r"[^a-z0-9]+", str(text or "").lower())) - {""}
        return {name for name, verbs in cls._ACTION_CLASSES.items()
                if words & verbs}

    @classmethod
    def _same_action_class(cls, wanted, candidate) -> bool:
        """False only when both name an action and the two disagree."""
        a = cls._action_classes(wanted)
        b = cls._action_classes(candidate)
        if not a or not b:
            return True
        return bool(a & b)

    def _click_swap_ok(self, sel, replacement) -> bool:
        want = " ".join(str(x or "") for x in
                        (getattr(sel, "pattern", ""), getattr(sel, "value", "")))
        got = " ".join(str(x or "") for x in
                       (getattr(replacement, "pattern", ""),
                        getattr(replacement, "value", "")))
        if self._same_action_class(want, got):
            return True
        self._log("INFO", f"   🛑 refused to recover {sel.describe()} onto "
                          f"{replacement.describe()} — different action")
        return False

    def _smart_locator(self, page, sel, *, verb: str, next_step=None):
        primary = self._locator(page, sel, clickable=verb == "CLICK")
        if self._count(primary):
            return primary, None, ""

        # One bounded readiness retry before declaring a selector
        self._wait_app_settled(page)
        primary = self._locator(page, sel, clickable=verb == "CLICK")
        if self._count(primary):
            return primary, None, "became available after page readiness"

        if verb in ("FILL", "SELECT", "WAIT_FOR", "EXPECT_VALUE"):
            field = self._field_hint(sel)
            if field and field_css(field):
                replacement = Selector(kind="field", pattern=field,
                                       is_regex=False)
                alt = self._locator(page, replacement)
                if self._count(alt):
                    return alt, replacement, f"observed a real {field} input"

            replacement = self._fuzzy_field_fallback(page, sel)
            if replacement is not None:
                alt = self._locator(page, replacement)
                if self._count(alt):
                    return alt, replacement, "matched an observed field attribute from the live DOM"

        if verb == "CLICK":
            replacement = self._source_testid_fallback(page, sel)
            if replacement is not None and self._click_swap_ok(sel, replacement):
                alt = self._locator(page, replacement, clickable=True)
                if self._count(alt):
                    return alt, replacement, "matched a source-declared functional testid"

            replacement = self._href_fallback(page, next_step)
            if replacement is not None:
                alt = self._locator(page, replacement, clickable=True)
                if self._count(alt):
                    return alt, replacement, "matched the next expected route"

            replacement = self._fuzzy_click_fallback(page, sel)
            if replacement is not None and self._click_swap_ok(sel, replacement):
                alt = self._locator(page, replacement, clickable=True)
                if self._count(alt):
                    return alt, replacement, "matched an observed clickable name"

        return primary, None, ""

    @staticmethod
    def _selector_inventory(page, limit: int = 12) -> str:
        """Small DOM witness attached to a selector failure and re-author prompt."""
        try:
            data = page.evaluate(
                """(limit) => {
                    const fields = [...document.querySelectorAll(
                      'input, textarea, select')].slice(0, limit).map(e => {
                        let label = ''
                        if (e.id) {
                          const lab = document.querySelector(`label[for="${CSS.escape(e.id)}"]`)
                          if (lab) label = (lab.innerText || lab.textContent || '').trim()
                        }
                        if (!label && e.closest('label')) {
                          const lab = e.closest('label')
                          label = (lab.innerText || lab.textContent || '').trim()
                        }
                        return {
                          tag: e.tagName.toLowerCase(), type: e.type || '',
                          name: e.name || '', id: e.id || '',
                          placeholder: e.placeholder || '',
                          autocomplete: e.autocomplete || '',
                          aria: e.getAttribute('aria-label') || '',
                          label: label.replace(/\\s+/g, ' ').slice(0, 80),
                          value: (e.value || '').slice(0, 80)
                        }
                    })
                    const controls = [...document.querySelectorAll(
                      'button, a, [role="button"], [role="link"], input[type="submit"]'
                    )].slice(0, limit).map(e => ({
                        tag: e.tagName.toLowerCase(),
                        role: e.getAttribute('role') || '',
                        text: (e.getAttribute('aria-label') || e.innerText ||
                               e.value || '').replace(/\\s+/g, ' ').trim(),
                        href: e.getAttribute('href') || '',
                        testid: e.getAttribute('data-testid') || '',
                        title: e.getAttribute('title') || '',
                        action: e.getAttribute('data-action') || '',
                        value: e.value || '',
                        disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true'
                    }))
                    const headings = [...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
                      .slice(0, 10).map(e => (e.innerText || e.textContent || '')
                      .replace(/\\s+/g, ' ').trim()).filter(Boolean)
                    const body = (document.body.innerText || '').replace(/\\s+/g, ' ').trim()
                    return { url: location.pathname + location.search,
                             title: document.title || '', headings,
                             text: body.slice(0, 900), fields, controls }
                }""", limit)
        except Exception:
            return ""
        lines = [f"DOM at {data.get('url') or '?'}:"]
        if data.get("title"):
            lines.append(f"  title: {data.get('title')}")
        if data.get("headings"):
            lines.append("  headings: " + " | ".join(data.get("headings")[:10]))
        if data.get("text"):
            lines.append("  visible: " + str(data.get("text"))[:900])
        fields = data.get("fields") or []
        if fields:
            lines.append("  fields: " + " | ".join(
                f"{x.get('tag')} type={x.get('type') or '-'} "
                f"name={x.get('name') or '-'} id={x.get('id') or '-'} "
                f"label={x.get('label') or '-'} "
                f"placeholder={x.get('placeholder') or '-'} "
                f"aria={x.get('aria') or '-'} "
                f"autocomplete={x.get('autocomplete') or '-'} "
                f"value={x.get('value') or '-'}"
                for x in fields[:limit]))
        controls = data.get("controls") or []
        if controls:
            lines.append("  controls: " + " | ".join(
                f"{x.get('tag')} {x.get('text') or '(no name)'}"
                + (f" href={x.get('href')}" if x.get("href") else "")
                + (f" testid={x.get('testid')}" if x.get("testid") else "")
                + (f" title={x.get('title')}" if x.get("title") else "")
                + (f" action={x.get('action')}" if x.get("action") else "")
                + (f" value={x.get('value')}" if x.get("value") else "")
                + (" disabled" if x.get("disabled") else "")
                for x in controls[:limit]))
        return "\n".join(lines)[:5000]

    def _route_from_page(self, page, *, include_query: bool = False) -> str:
        """Current same-app route, including redirects/client navigation."""
        try:
            parsed = urlparse(page.url or "")
            if parsed.netloc and parsed.netloc != urlparse(self.base_url).netloc:
                return ""
            path = parsed.path or "/"
            if include_query and parsed.query:
                return path + "?" + parsed.query
            return path
        except Exception:
            return ""

    def _page_for(self, url) -> str:
        """The file that renders `url`, including concrete dynamic URLs."""
        if not self.az:
            return ""
        try:
            routes = self.az.enumerate_routes() or {}
            path = urlparse(url or "/").path or "/"
            meta = routes.get(path) or {}
            if meta.get("file"):
                return meta.get("file", "")

            for pattern, row in routes.items():
                if not row.get("dynamic"):
                    continue
                rx = re.escape(pattern)
                rx = re.sub(r"\\\[\\\.\\\.\\\.[^\\\]]+\\\]",
                            r".+", rx)
                rx = re.sub(r"\\\[[^\\\]]+\\\]",
                            r"[^/]+", rx)
                if re.fullmatch(rx, path):
                    return row.get("file", "")
            return ""
        except Exception:
            return ""
