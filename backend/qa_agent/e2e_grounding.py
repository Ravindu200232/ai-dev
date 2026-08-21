"""Scenario grounding, rendering and spec persistence."""
import re
from .e2e_common import *

class E2EGroundingMixin:
    _FIELD_ALIASES = {
        "email": ("email", "e-mail"),
        "password": ("password", "passcode", "passwd"),
        "phone": ("phone", "mobile", "telephone", "tel"),
        "name": ("name", "full name", "first name", "last name"),
        "price": ("price", "rate", "cost", "fee", "unit price"),
        "amount": ("amount", "total", "charge"),
        "status": ("status", "state"),
        "rating": ("rating", "stars", "score"),
        "review": ("review", "comment", "feedback"),
        "quantity": ("quantity", "stock", "qty"),
        "discount": ("discount", "percent", "percentage", "promo", "coupon",
                     "code"),
        "promo": ("promo", "coupon", "voucher", "discount", "code"),
        "coupon": ("coupon", "promo", "voucher", "discount", "code"),
        "category": ("category", "type", "group"),
        "start": ("start", "checkin", "check-in"),
        "end": ("end", "check-out", "until", "return", "due", "expiry"),
    }

    def _journey_requires_field(self, journey: dict, key: str) -> bool:
        """Whether the accepted workflow explicitly requires this field."""
        journey = journey or {}
        text = " ".join([str(journey.get("title") or "")]
                        + [str(x) for x in (journey.get("steps") or [])]).lower()
        aliases = self._FIELD_ALIASES.get(str(key or "").lower(), (str(key or "").lower(),))
        return any(re.search(r"\b" + re.escape(a) + r"\b", text, re.I)
                   for a in aliases if a)

    def _field_is_grounded(self, key: str, hay: str) -> bool:
        """True when runtime/source evidence actually exposes a matching form field."""
        key = str(key or "").strip().lower()
        if not key or not hay:
            return False
        aliases = self._FIELD_ALIASES.get(key, (key,))
        blob = str(hay or "")

        attrs = ("name", "id", "label", "placeholder", "aria",
                 "aria-label", "autocomplete")
        for alias in aliases:
            token = re.escape(alias).replace(r"\ ", r"[\s_-]*")
            for attr in attrs:
                if re.search(
                    rf"\b{re.escape(attr)}\s*(?:=|:)\s*(?:['\"])?[^\n|'\"]*{token}",
                    blob, re.I):
                    return True

        if key == "email" and re.search(r"\btype\s*=\s*['\"]?email\b", blob, re.I):
            return True
        if key == "password" and re.search(r"\btype\s*=\s*['\"]?password\b", blob, re.I):
            return True
        if key == "phone" and re.search(r"\btype\s*=\s*['\"]?tel\b", blob, re.I):
            return True
        if key == "date" and re.search(r"\btype\s*=\s*['\"]?date\b", blob, re.I):
            return True
        return False

    # Headings and button copy the app really renders. A refusal that does not
    # carry these is a refusal the model cannot act on: it rewrites blind,
    # picks another string that is not there, and the journey dies on the
    # second attempt having never been told what WAS available.
    _COPY_RE = re.compile(
        r">\s*([A-Z][^<>{}\n]{3,60}?)\s*<"                # >Featured Projects<
        r"|<h[1-6][^>]*>\s*\{?['\"`]?([^<>{}'\"`\n]{4,60})",  # heading text
        re.M)

    def app_copy(self, limit: int = 14) -> list:
        """Text the app actually renders, for handing back with a refusal."""
        files = getattr(self.arch, "files", None) or {}
        seen, out = set(), []
        for path, body in files.items():
            if not str(path).startswith(("app/", "components/")):
                continue
            for m in self._COPY_RE.finditer(str(body or "")):
                text = (m.group(1) or m.group(2) or "").strip()
                if len(text) < 4 or text.lower() in seen:
                    continue
                if re.search(r"[{}<>=]|^\W+$|className|import |export ", text):
                    continue
                seen.add(text.lower())
                out.append(text)
                if len(out) >= limit:
                    return out
        return out

    def _copy_hint(self) -> str:
        """`, and the app does render: …` or '' when nothing could be read."""
        got = self.app_copy()
        if not got:
            return ""
        return (" The app DOES render these, so assert one of them instead: "
                + " · ".join(f"/{c}/i" for c in got[:10]))

    def _text_absent_from_app(self, st) -> bool:
        """True only when the step's literal text is in none of the app's files."""
        sel = getattr(st, "selector", None)
        if not sel or getattr(sel, "kind", "") not in {"text", "role"}:
            return False
        raw = str(getattr(sel, "pattern", "") or "").strip()
        if not raw or re.search(r"[\\[\]()|^$*+?{}]", raw):
            return False
        if len(raw) < 4:
            return False

        files = getattr(self.arch, "files", None) or {}
        needle = raw.lower()
        for path, body in files.items():
            if not str(path).startswith(("app/", "components/", "lib/")):
                continue
            if needle in str(body or "").lower():
                return False
        return bool(files)

    @staticmethod
    def _assertion_matches_entered_value(st, values: list[str]) -> bool:
        """Whether a later text assertion proves data the scenario itself entered."""
        sel = getattr(st, "selector", None)
        if not sel or not values:
            return False
        try:
            pat = sel.compiled()
        except Exception:
            pat = None
        raw = str(getattr(sel, "pattern", "") or "").strip().strip('"\'')
        for value in values:
            value = str(value or "").strip()
            if not value:
                continue
            try:
                if hasattr(pat, "search") and pat.search(value):
                    return True
            except Exception:
                pass
            if raw and (raw.lower() in value.lower() or value.lower() in raw.lower()):
                return True
        return False

    # `<input name="fullName".../>` in JSX or HTML.
    _CONTROL_RE = re.compile(r"<(input|textarea|select)\b([^>]{0,400})>", re.I | re.S)
    _CONTROL_ATTR_RE = re.compile(
        r"""\b(name|id|placeholder|aria-label|type)\s*=\s*["']([^"']{1,60})["']""",
        re.I)
    # `fields: input type=email name=email id=email |...`
    _DOM_FIELD_RE = re.compile(
        r"\b(?:name|id)=([A-Za-z_][\w.\[\]-]{0,40})")

    def fields_in_journey(self, journey: dict = None, page: str = "",
                          limit: int = 30) -> list:
        """Every form field this journey can actually type into."""
        blocks = []
        try:
            blocks.append(self.runtime_evidence(journey))
        except Exception as e:
            log.debug(f"fields from DOM: {e}")
        try:
            blocks.append(self.journey_source_bundle(journey, page=page))
        except Exception as e:
            log.debug(f"fields from source: {e}")
        for rel in ([page] if page else []) + self._journey_pages(journey):
            if not rel:
                continue
            try:
                blocks.append(self.markup_for(rel))
            except Exception:
                pass
        hay = "\n".join(b for b in blocks if b)
        if not hay:
            return []

        out, seen = [], set()

        def add(key, kind=""):
            key = str(key or "").strip()
            low = key.lower()
            if not key or low in seen or not field_css(low):
                return
            seen.add(low)
            out.append(f"{key} ({kind})" if kind else key)

        for tag, attrs in self._CONTROL_RE.findall(hay):
            found = {k.lower(): v for k, v in self._CONTROL_ATTR_RE.findall(attrs)}
            kind = found.get("type") or tag.lower()
            for attr in ("name", "id", "aria-label", "placeholder"):
                if found.get(attr):
                    add(found[attr], kind)
                    break

        for line in hay.splitlines():
            if not re.match(r"\s*fields?\s*:", line, re.I):
                continue
            for key in self._DOM_FIELD_RE.findall(line):
                add(key)

        return out[:limit]

    def grounding_issue(self, sc: Scenario, journey: dict = None) -> str:
        """Reject guessed page-copy assertions before spending a browser run."""
        evidence = self.runtime_evidence(journey)
        blocks = []
        for rel in self._journey_pages(journey):
            try:
                block = self.markup_for(rel)
            except Exception:
                block = ""
            if block:
                blocks.append(block)
        try:
            # Never a smaller budget than `author` spent
            bundle = self.journey_source_bundle(
                journey, budget=max(self.SOURCE_BUNDLE_BUDGET,
                                    int(self.prompt_budget_chars() * 0.55)))
        except Exception as e:
            log.debug(f"grounding source bundle: {e}")
            bundle = ""
        # Uncapped on purpose: nothing here reaches a model
        hay = "\n".join(x for x in (evidence, "\n".join(blocks), bundle) if x)
        if not hay.strip():
            return ""

        business_started = False
        entered_values = []
        for st in sc.steps:
            sel = getattr(st, "selector", None)
            if (st.verb in {"FILL", "SELECT", "EXPECT_VALUE"} and sel and
                    getattr(sel, "kind", "") == "field"):
                key = str(getattr(sel, "pattern", "") or "").strip()
                if (key and not self._field_is_grounded(key, hay) and
                        not self._journey_requires_field(journey or {}, key)):
                    return (f"ungrounded form field {st.describe()}: field={key} "
                            "is absent from the observed DOM/source and is not "
                            "required by the accepted workflow. Re-author using "
                            "an observed field instead of inventing one.")

            if st.verb == "SELECT":
                # A select often commits through onChange.
                if str(getattr(st, "value", "") or "").strip():
                    entered_values.append(str(st.value).strip())
                business_started = True
            elif st.verb == "FILL":
                if str(getattr(st, "value", "") or "").strip():
                    entered_values.append(str(st.value).strip())
            elif st.verb == "CLICK":
                sel = getattr(st, "selector", None)
                text = " ".join([str(getattr(sel, "pattern", "") or ""),
                                 str(getattr(sel, "role", "") or "")]).lower()
                if not re.search(r"sign.?in|log.?in|login|authenticate", text):
                    business_started = True

            if st.verb not in {"WAIT_FOR", "EXPECT_TEXT"}:
                continue
            if business_started:
                if self._assertion_matches_entered_value(st, entered_values):
                    continue
                if self._text_absent_from_app(st):
                    return (f"ungrounded page-copy check {st.describe()}: that "
                            "text appears nowhere in the app's source, so no "
                            "run of it can produce the text. Use EXPECT_URL for "
                            "page identity."
                            + self._copy_hint())
                continue
            sel = getattr(st, "selector", None)
            if not sel or getattr(sel, "kind", "") not in {"text", "role"}:
                continue
            try:
                pat = sel.compiled()
                ok = bool(pat.search(hay) if hasattr(pat, "search")
                          else str(pat).lower() in hay.lower())
            except Exception:
                ok = True
            if not ok:
                prev = getattr(self, "_active_journey", None)
                try:
                    self._active_journey = journey or {}
                    target = self._route_for_page_copy(sel)
                finally:
                    self._active_journey = prev
                if target:
                    continue
                return (f"ungrounded page-copy check {st.describe()}: that text "
                        "is absent from the real DOM/source. Use EXPECT_URL for "
                        "page identity, or text that was actually observed."
                        + self._copy_hint())
        return ""

    def _role_in(self, text):
        """The role a scenario runs as, or "" for signed out."""
        m = re.search(r"^\s*(?:AS|ROLE)\s*::\s*(.+)$", text or "", re.M | re.I)
        role = (m.group(1).strip().lower() if m else "")
        if any((a.get("role") or "").lower() == role for a in self.accounts()):
            return role
        return "" if role in self._SIGNED_OUT else role

    def _idea(self):
        try:
            convo = getattr(self.arch, "convo", None) or []
            if len(convo) > 1 and convo[1].get("role") == "user":
                return convo[1]["content"][:3000]
        except Exception:
            pass
        return (getattr(self.arch, "plan_md", "") or "")[:3000]

    _REVEAL_VERBS = ("edit", "manage", "view", "details", "detail", "open",
                     "expand", "show")

    def _is_reveal_click(self, st) -> bool:
        """A click that only exposes UI — never one that changes data."""
        if getattr(st, "verb", "") != "CLICK":
            return False
        if self._is_business_step(st):
            return False
        text = " ".join(str(getattr(getattr(st, "selector", None), k, "") or "")
                        for k in ("pattern", "value")).lower()
        if not any(v in text for v in self._REVEAL_VERBS):
            return False
        # Anything with a commit/destroy verb in it is not a reveal
        return self._same_action_class(text, "edit view open")

    def verify_selector_live(self, sc, step, failure=None):
        """(ok, why) — does `step`'s selector resolve where it will run?"""
        verb = str(getattr(step, "verb", "") or "")
        if verb not in ("FILL", "SELECT", "CLICK", "WAIT_FOR", "EXPECT_VALUE"):
            return True, ""
        ev = dict(getattr(self, "_last_run_evidence", {}) or {})
        route = str(ev.get("route_before") or ev.get("route") or "").strip()
        if not route.startswith("/") or route.startswith("/api/"):
            return True, ""
        try:
            idx = self._failure_step_index(sc, failure) if failure is not None else -1
        except Exception:
            idx = -1
        prefix = list(sc.steps[:idx]) if idx > 0 else []
        for st in prefix:
            if st.verb in ("FILL", "SELECT") or (
                    st.verb == "CLICK" and not self._is_reveal_click(st)):
                return True, ""          # State we cannot rebuild read-only
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return True, ""
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(viewport={"width": 1280,
                                                    "height": 800})
                page = ctx.new_page()
                try:
                    acc = self.account_for(sc.role) if sc.role else None
                    if acc:
                        page.goto(self.base_url + "/", timeout=GOTO_TIMEOUT,
                                  wait_until="domcontentloaded")
                        self._sign_in(page, acc)
                    page.goto(self.base_url + route, timeout=GOTO_TIMEOUT,
                              wait_until="domcontentloaded")
                    self._wait_hydrated(page)
                    for st in prefix:
                        if self._is_reveal_click(st):
                            try:
                                self._locator(page, st.selector, clickable=True) \
                                    .first.click(timeout=STEP_TIMEOUT)
                                page.wait_for_timeout(400)
                            except Exception as e:
                                log.debug(f"verify reveal: {e}")
                    found = self._count(self._locator(
                        page, step.selector, clickable=verb == "CLICK"))
                finally:
                    browser.close()
        except Exception as e:
            log.debug(f"verify selector live: {e}")
            return True, ""
        if found:
            return True, ""
        return False, (f"{step.selector.describe()} is absent from {route} "
                       f"too — the swap does not name anything the page has")

    def preground(self, sc: Scenario, journey: dict = None) -> int:
        """Bind FILL/SELECT/CLICK selectors to the live DOM before the run."""
        steps = list(getattr(sc, "steps", []) or [])
        if not steps:
            return 0
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return 0

        # Everything up to the first business mutation, in order.
        prefix = []
        for st in steps:
            if self._is_business_step(st):
                break
            if st.verb in ("GOTO", "FILL", "SELECT", "CLICK", "WAIT_FOR"):
                prefix.append(st)
        if not any(getattr(st, "selector", None) for st in prefix):
            return 0

        fixed = 0
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(viewport={"width": 1280,
                                                    "height": 800})
                page = ctx.new_page()
                role = str((journey or {}).get("role") or sc.role or "")
                acc = self.account_for(role) if role else None
                try:
                    if acc:
                        page.goto(self.base_url + "/", timeout=GOTO_TIMEOUT,
                                  wait_until="domcontentloaded")
                        self._sign_in(page, acc)
                    for st in prefix:
                        if st.verb == "GOTO":
                            try:
                                page.goto(self.base_url + str(st.value or "/"),
                                          timeout=GOTO_TIMEOUT,
                                          wait_until="domcontentloaded")
                                self._wait_hydrated(page)
                            except Exception as e:
                                log.debug(f"preground goto: {e}")
                            continue
                        sel = getattr(st, "selector", None)
                        if sel is None:
                            continue
                        clickable = st.verb == "CLICK"
                        if not self._count(self._locator(page, sel,
                                                         clickable=clickable)):
                            _, replacement, note = self._smart_locator(
                                page, sel, verb=st.verb)
                            if replacement is not None:
                                old = sel.describe()
                                st.selector = replacement
                                sel = replacement
                                self._scenario_changed = True
                                fixed += 1
                                self._log("INFO",
                                          f"   🧲 pre-grounded: {old} → "
                                          f"{replacement.describe()}"
                                          + (f" ({note})" if note else ""))
                        # Only reveal controls are pressed
                        if self._is_reveal_click(st):
                            try:
                                self._locator(page, sel, clickable=True) \
                                    .first.click(timeout=STEP_TIMEOUT)
                                page.wait_for_timeout(400)
                            except Exception as e:
                                log.debug(f"preground reveal: {e}")
                finally:
                    browser.close()
        except Exception as e:
            log.debug(f"preground: {e}")
        return fixed

    def action_id_block(self, journey: dict = None) -> str:
        """The ids the app is guaranteed to carry, for this journey's routes."""
        try:
            from agents.action_ids import workflow_actions
            rows = workflow_actions(getattr(self.arch, "plan", None) or {})
        except Exception as e:
            log.debug(f"action id block: {e}")
            return ""
        if not rows:
            return ""
        who = str((journey or {}).get("role") or "").strip().lower()
        title = str((journey or {}).get("title") or "").strip().lower()
        mine = [r for r in rows
                if not who or not r["who"]
                or who in r["who"].lower() or r["who"].lower() in who
                or r["workflow"].lower() in title]
        rows = mine or rows
        lines = ["GUARANTEED CONTROL IDS — the app was built to carry these and "
                 "the build already verified they exist. For any step below "
                 "that matches one, write `testid=<id>` exactly; do not "
                 "paraphrase its wording into a role/name locator."]
        for r in rows[:20]:
            lines.append(f"  {r['route']:<20} {r['phrase'][:40]:<42} "
                         f"testid={r['testid']}")
        return "\n".join(lines)

    @staticmethod
    def _render(sc: Scenario) -> str:
        lines = [f"FLOW :: {sc.title}", f"AS :: {sc.role or 'SIGNED OUT'}"]
        lines += [s.describe() for s in sc.steps]
        return "\n".join(lines)

    def write_spec(self, sc: Scenario) -> str:
        """Write the scenario as a real Playwright spec in the project."""
        rel = sc.spec_path()
        try:
            fp = self.project_dir / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            body = to_playwright_js(sc, base_url=self.base_url)
            fp.write_text(body, encoding="utf-8")
            cfg = self.project_dir / "playwright.config.js"
            if not cfg.is_file():
                cfg.write_text(PLAYWRIGHT_CONFIG, encoding="utf-8")
        except Exception as e:
            self._log("WARN", f"   ⚠ could not write {rel}: {e}")
            return ""
        size = f"{len(body) / 1024:.1f}KB" if len(body) >= 1024 else f"{len(body)}B"
        self._fire("on_file_written", rel, size, body)
        self._log("INFO", f"   🎭 {rel}")
        return rel


