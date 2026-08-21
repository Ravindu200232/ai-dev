"""E2E sweep and fixed scenario step execution."""
from .e2e_common import *
from .e2e_verbs import label_mutates


class E2EStepsMixin:
    def sweep(self, page) -> list:
        """Open every page the app serves, signed in, and report what breaks."""
        if not self.az:
            return []
        try:
            routes = [u for u, m in sorted(self.az.enumerate_routes().items())
                      if m.get("kind") == "page" and not m.get("dynamic")
                      and not self.SWEEP_SKIP_RE.search(u)]
        except Exception as e:
            log.debug(f"sweep routes: {e}")
            return []
        if not routes:
            return []
        if len(routes) > self.SWEEP_CAP:
            self._log("WARN", f"   ⚠ {len(routes)} routes — sweeping the "
                              f"first {self.SWEEP_CAP}")
            routes = routes[:self.SWEEP_CAP]

        self._log("INFO", f"   🔍 Checking {len(routes)} page(s) while signed in")
        seen, out = set(), []
        for url in routes:
            try:
                resp = page.goto(self.base_url + url, timeout=GOTO_TIMEOUT,
                                 wait_until="domcontentloaded")
            except Exception as e:
                if "ERR_ABORTED" in str(e):
                    try:
                        page.wait_for_timeout(250)
                        resp = page.goto(self.base_url + url, timeout=GOTO_TIMEOUT,
                                         wait_until="domcontentloaded")
                    except Exception as again:
                        out.append((url, f"{type(again).__name__}: {again}"[:250]))
                        continue
                else:
                    out.append((url, f"{type(e).__name__}: {e}"[:250]))
                    continue

            status = resp.status if resp else None
            if status and status >= 400:
                body = ""
                try:
                    body = page.inner_text("body")[:250]
                except Exception:
                    pass
                out.append((url, f"HTTP {status}. {body}".strip()))
                continue

            msg = overlay_error(page)
            if not msg or len(msg) <= 15:
                continue

            key = msg.splitlines()[0][:90]
            if key in seen:
                self._log("INFO", f"   ↳ {url} — same fault again")
                continue
            seen.add(key)
            out.append((url, msg[:500]))

        if out:
            self._log("WARN", f"   ❌ {len(out)} page(s) broken while signed in")
        else:
            self._log("INFO", "   ✅ every page rendered clean")
        return out

    def _auth_bounce(self, page, intended: str):
        """`None` when fine; a failure tuple only when the app truly rejects."""
        intended = str(intended or "").split("?", 1)[0].rstrip("/") or "/"
        if intended in ("/", "/login", "/signup", "/register"):
            return None
        here = self._route_from_page(page)
        if here != "/login":
            return None
        journey = getattr(self, "_active_journey", {}) or {}
        role = str(journey.get("role") or "").strip().lower()
        if not role or role in set(getattr(self, "_SIGNED_OUT", ()) or ()):
            return None
        acc = self.account_for(role)
        if not acc:
            return None
        try:
            if not self._session_alive(page, timeout_ms=1500):
                if self._sign_in(page, acc):
                    page.goto(self.base_url + intended,
                              timeout=GOTO_TIMEOUT,
                              wait_until="domcontentloaded")
                    self._wait_hydrated(page)
                    if self._route_from_page(page) != "/login":
                        self._log("INFO", f"   🔐 session re-established — "
                                          f"{intended} reached on retry")
                        return None
        except Exception as e:
            log.debug(f"auth bounce recovery {intended}: {e}")
        src = self._page_for(intended)
        return (KIND_BEHAVIOR,
                f"{intended} redirected an authenticated {role} session to "
                f"/login — the guard in {src or 'that page'} rejects a live "
                f"session instead of the missing one it was written for")

    @staticmethod
    def _is_business_step(st) -> bool:
        """Whether a successful step has started the feature mutation itself."""
        if st.verb == "SELECT":
            return False
        if st.verb != "CLICK":
            return False
        sel = getattr(st, "selector", None)
        text = " ".join([str(getattr(sel, "pattern", "") or ""),
                         str(getattr(sel, "role", "") or "")])
        return label_mutates(text)

    @staticmethod
    def _concept_words(text: str) -> set:
        """The nouns a phrase is about, with no idea what app this is."""
        out = set()
        for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
            if len(w) < 4 or w in CONCEPT_STOPWORDS:
                continue
            out.add(singular(w))
        return out

    def _route_for_page_copy(self, sel) -> str:
        """Best concrete workflow route described by a missing page-copy check."""
        if not getattr(self, "_active_journey", None):
            return ""
        desired = self._concept_words(getattr(sel, "pattern", ""))
        if not desired:
            return ""
        best = (0, "")
        try:
            routes = self._journey_routes(self._active_journey)
        except Exception:
            routes = []
        for url in routes:
            if url in {"/", "/login", "/signup"} or "[" in url:
                continue
            got = self._concept_words(url.replace("/", " "))
            score = len(desired & got)
            if score > best[0]:
                best = (score, url)
        return best[1] if best[0] else ""

    def _recover_page_identity(self, page, st) -> bool:
        """Turn guessed pre-work copy into an exact journey-route assertion."""
        if getattr(self, "_business_started", False):
            return False
        sel = getattr(st, "selector", None)
        if not sel or getattr(sel, "kind", "") not in {"text", "role"}:
            return False
        target = self._route_for_page_copy(sel)
        if not target:
            return False
        current = self._route_from_page(page)
        if current == target:
            # We are already on the semantic route.
            st.verb = "EXPECT_URL"
            st.selector = Selector(kind="text", pattern=re.escape(target),
                                   flags="", is_regex=True)
            self._scenario_changed = True
            self._log("INFO", f"   ↪ page identity recovered from copy → URL {target}")
            return True
        try:
            resp = page.goto(self.base_url + target, timeout=GOTO_TIMEOUT,
                             wait_until="load")
            if resp and resp.status >= 400:
                return False
            self._wait_hydrated(page)
        except Exception:
            return False
        st.verb = "GOTO"
        st.value = target
        st.selector = None
        self._scenario_changed = True
        self._log("INFO", f"   ↪ page identity recovered from copy → GOTO {target}")
        return True

    def _step(self, page, st, next_step=None):
        """`None` when the step passed, else `(kind, message)`."""
        if st.verb == "GOTO":

            try:
                resp = page.goto(self.base_url + st.value, timeout=GOTO_TIMEOUT,
                                 wait_until="domcontentloaded")
            except Exception as e:
                if "ERR_ABORTED" not in str(e):
                    raise
                page.wait_for_timeout(250)
                resp = page.goto(self.base_url + st.value, timeout=GOTO_TIMEOUT,
                                 wait_until="domcontentloaded")
            if resp and resp.status >= 500:
                body = ""
                try:
                    body = page.inner_text("body")[:300]
                except Exception:
                    pass
                return (KIND_CRASH, f"{st.value} returned HTTP {resp.status}"
                                    + (f"\n{body}" if body else ""))
            if resp and resp.status >= 400:
                return (KIND_CRASH, f"{st.value} returned HTTP {resp.status}")
            self._wait_hydrated(page)
            bounce = self._auth_bounce(page, st.value)
            if bounce:
                return bounce
            return None

        if st.verb in ("FILL", "SELECT", "CLICK", "WAIT_FOR"):
            loc, replacement, note = self._smart_locator(
                page, st.selector, verb=st.verb, next_step=next_step)
            if replacement is not None:
                old = st.selector.describe()
                st.selector = replacement
                self._scenario_changed = True
                self._log("INFO", f"   ↪ selector recovered: {old} → "
                                  f"{replacement.describe()}"
                                  + (f" ({note})" if note else ""))
            if loc is None or self._count(loc) == 0:
                if st.verb == "WAIT_FOR" and self._recover_page_identity(page, st):
                    return None
                # A control cannot match on a page we were bounced off of.
                bounce = self._auth_bounce(
                    page, getattr(self, "_intended_route", "") or "")
                if bounce:
                    return bounce
                inv = self._selector_inventory(page)
                kind = (KIND_BEHAVIOR if st.verb == "WAIT_FOR" and
                        getattr(self, "_business_started", False) else KIND_SELECTOR)
                return (kind,
                        f"nothing matched {st.selector.describe()}"
                        + (f"\n{inv}" if inv else ""))
            loc = loc.first
            loc.wait_for(state="visible", timeout=STEP_TIMEOUT)
            if st.verb == "FILL":
                loc.fill(st.value, timeout=STEP_TIMEOUT)
            elif st.verb == "SELECT":
                # Prefer the human-visible option label; fall back to its value.
                try:
                    loc.select_option(label=st.value, timeout=STEP_TIMEOUT)
                except Exception:
                    loc.select_option(st.value, timeout=STEP_TIMEOUT)
            elif st.verb == "CLICK":
                auth_click = (self._is_auth_action(st) or
                              (self._route_from_page(page) == "/login" and
                               str(getattr(st.selector, "role", "") or "").lower()
                               == "button"))
                loc.click(timeout=STEP_TIMEOUT)
                page.wait_for_timeout(350)
                if (auth_click and
                        (getattr(self, "_active_journey", {}) or {}).get("role")):
                    if not self._session_alive(page):
                        return (KIND_BEHAVIOR,
                                "sign-in returned, but no authenticated browser "
                                "session was established")
            return None

        if st.verb == "EXPECT_TEXT":
            deadline = time.time() + ASSERT_TIMEOUT / 1000
            pat, seen = st.selector.compiled(), ""
            while time.time() < deadline:
                try:
                    seen = page.inner_text("body")
                except Exception:
                    seen = ""
                if (pat.search(seen) if hasattr(pat, "search")
                        else pat.lower() in seen.lower()):
                    return None
                if self._number_text_present(st.selector, seen):
                    return None
                page.wait_for_timeout(400)
            if self._recover_page_identity(page, st):
                return None
            kind = KIND_BEHAVIOR if getattr(self, "_business_started", False) else "ASSERTION"
            return (kind,
                    f"the page never showed {st.selector.describe()}\n"
                    f"what it showed: {seen[:300]}")

        if st.verb == "EXPECT_URL":
            deadline = time.time() + ASSERT_TIMEOUT / 1000
            pat, url = st.selector.compiled(), page.url
            while time.time() < deadline:
                url = page.url
                if (pat.search(url) if hasattr(pat, "search") else pat in url):
                    return None
                page.wait_for_timeout(300)
            kind = KIND_BEHAVIOR if getattr(self, "_business_started", False) else "ASSERTION"
            return (kind, f"the url stayed at {url}, expected "
                          f"{st.selector.describe()}")

        if st.verb == "EXPECT_VALUE":
            loc, replacement, note = self._smart_locator(
                page, st.selector, verb=st.verb, next_step=next_step)
            if replacement is not None:
                old = st.selector.describe()
                st.selector = replacement
                self._scenario_changed = True
                self._log("INFO", f"   ↪ selector recovered: {old} → "
                                  f"{replacement.describe()}"
                                  + (f" ({note})" if note else ""))
            if loc is None or self._count(loc) == 0:
                kind = (KIND_BEHAVIOR if getattr(self, "_business_started", False)
                        else KIND_SELECTOR)
                return (kind,
                        f"nothing matched {st.selector.describe()}\n"
                        + self._selector_inventory(page))
            deadline = time.time() + ASSERT_TIMEOUT / 1000
            seen = ""
            while time.time() < deadline:
                try:
                    seen = loc.first.input_value(timeout=2000)
                except Exception:
                    try:
                        seen = loc.first.get_attribute("value") or ""
                    except Exception:
                        seen = ""
                if str(seen) == str(st.value):
                    return None
                page.wait_for_timeout(300)
            return (KIND_BEHAVIOR,
                    f"{st.selector.describe()} stayed at {seen!r}, "
                    f"expected {st.value!r}")

        if st.verb == "EXPECT_NO_ERROR":
            return None
        return None

    _MONEY_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

    @classmethod
    def _number_text_present(cls, sel, page_text: str) -> bool:
        """Whether a numeric assertion is satisfied under real money formatting."""
        raw = str(getattr(sel, "pattern", "") or "")
        if not raw or not re.search(r"\d", raw):
            return False
        body = str(page_text or "")
        if not body:
            return False
        literal = re.sub(r"\\([^A-Za-z0-9])", r"\1", raw)
        literal = re.sub(r"[\\^$*+?()\[\]{}|]", " ", literal)

        def canon(num: str) -> str:
            num = num.replace(",", "")
            if "." in num:
                num = num.rstrip("0").rstrip(".")
            return num

        wanted_nums = [canon(m) for m in cls._MONEY_RE.findall(literal)]
        if not wanted_nums:
            return False
        seen_nums = {canon(m) for m in cls._MONEY_RE.findall(body)}
        if any(n not in seen_nums for n in wanted_nums):
            return False
        low = body.lower()
        for word in re.findall(r"[A-Za-z]{3,}", literal):
            if word.lower() in ("i", "s") or len(word) < 3:
                continue
            if word.lower() not in low:
                return False
        return True

    @staticmethod
    def _wait_hydrated(page):
        """Wait until React has attached its handlers."""
        try:
            page.wait_for_function(
                """() => {
                    const el = document.querySelector('button, input, a[href]')
                    return !!el && Object.keys(el).some(
                        (k) => k.startsWith('__react'))
                }""", timeout=HYDRATE_TIMEOUT)
        except Exception:
            page.wait_for_timeout(1500)

    @staticmethod
    def _wait_app_settled(page, timeout: int = 2600):
        """Wait briefly for a data-driven page to become actionable."""
        try:
            page.wait_for_function(
                """() => {
                    const txt=(document.body?.innerText||'').trim().toLowerCase();
                    if (!txt || /^(loading[.…]*|please wait[.…]*)$/.test(txt)) return false;
                    const busy=[...document.querySelectorAll('[aria-busy="true"]')];
                    return busy.length===0;
                }""", timeout=timeout)
        except Exception:
            try:
                page.wait_for_timeout(180)
            except Exception:
                pass

    def warm(self, sc):
        """Visit each page the flow will reach, once, before the flow runs."""
        import requests

        targets = {st.value for st in sc.steps if st.verb == "GOTO"}

        known = list((self.az.enumerate_routes() if self.az else {}) or {})
        for st in sc.steps:
            if st.verb == "EXPECT_URL" and st.selector:
                for url in known:
                    if url != "/" and url.strip("/") in st.selector.pattern:
                        targets.add(url)
        # Concurrently, because `next dev` compiles routes in parallel
        cold = [p for p in sorted(targets) if p not in self._warmed_routes]
        if not cold:
            return sorted(targets)

        from concurrent.futures import ThreadPoolExecutor

        def fetch(path):
            try:
                requests.get(self.base_url + path, timeout=30)
                return path
            except Exception as e:                             # noqa: BLE001
                log.debug(f"warm {path}: {e}")
                return ""

        with ThreadPoolExecutor(max_workers=min(6, len(cold))) as pool:
            for path in pool.map(fetch, cold):
                if path:
                    self._warmed_routes.add(path)
        return sorted(targets)
