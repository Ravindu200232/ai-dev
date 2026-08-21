"""Open the app and watch it fail, before changing a line of it."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("reproduce")

WAIT_MS = 2500
SETTLE_MS = 900
MAX_LINES = 12

_STOP = {
    "the", "a", "an", "is", "it", "on", "in", "to", "of", "and", "or", "not",
    "does", "do", "did", "doesn", "don", "isn", "wont", "won", "cant", "can",
    "nothing", "anything", "something", "when", "click", "clicking", "clicked",
    "press", "pressing", "pressed", "button", "page", "screen", "app", "my",
    "this", "that", "there", "here", "after", "before", "but", "still", "just",
    "broken", "break", "breaks", "wrong", "error", "errors", "fail", "fails",
    "failing", "blank", "empty", "white", "work", "works", "working", "should",
    "would", "have", "has", "i", "we", "you", "me", "us", "no", "any",
}


_NOISE = re.compile(
    r"_next/(?:static|hmr)|/_next/webpack|hot-update|"
    r"WebSocket connection to .*_next|"
    r"Download the React DevTools|React DevTools|"
    r"favicon\.ico|"
    r"Fast Refresh|\[HMR\]|"
    r"webpack-internal|turbopack",
    re.I)


def _is_noise(line: str) -> bool:
    return bool(_NOISE.search(str(line or "")))


def _shape(url: str) -> str:
    """A URL with the record ids taken out, so two runs can be compared."""
    parts = []
    for seg in str(url or "").split("/"):
        if seg and (re.fullmatch(r"[0-9a-fA-F-]{6,}", seg) or any(c.isdigit() for c in seg)):
            parts.append("#")
        else:
            parts.append(seg)
    return "/".join(parts)


@dataclass
class Reproduction:
    """What the browser saw when it was asked to do the thing complained about."""

    route: str = "/"
    ran: bool = False
    why_not: str = ""
    signed_in: bool = False
    console: list = field(default_factory=list)  # Error text, with source
    page_errors: list = field(default_factory=list)  # Uncaught, with stacks
    network: list = field(default_factory=list)  # 4xx/5xx with body snippets
    clicked: str = ""  # The control pressed, if any
    filled: list = field(default_factory=list)  # Fields given a value first
    changed: bool = False  # Did the page react at all
    html: str = ""
    screenshot_b64: str = ""

    def is_clean(self) -> bool:
        """Nothing went wrong that a browser could see."""
        return not (self.console or self.page_errors or self.network)

    def signature(self) -> set:
        """The stable part of each fault, for telling one run from the next."""
        out = set()
        for line in list(self.console) + list(self.page_errors):
            text = re.sub(r"\d+", "#", " ".join(str(line).split()))
            out.add(text[:160])
        for line in self.network:
            hit = re.match(r"HTTP (\d{3}) (\w+) (\S+)", str(line))
            if hit:
                out.add(f"HTTP {hit.group(1)} {hit.group(2)} {_shape(hit.group(3))}")
        return out

    def as_prompt(self) -> str:
        """The evidence, in the order a person would read it."""
        if not self.ran:
            return f"The app could not be opened to reproduce this: {self.why_not}"
        parts = [f"The app was opened at {self.route}"
                 + (" signed in as a demo user." if self.signed_in else ".")]
        if self.filled:
            parts.append("The form was filled in first: "
                         + ", ".join(self.filled[:10]) + ".")
        if self.clicked:
            parts.append(f"'{self.clicked}' was clicked. "
                         + ("The page changed." if self.changed
                            else "NOTHING on the page changed."))
        if self.page_errors:
            parts.append("Uncaught in the browser:\n"
                         + "\n".join(f"  {e}" for e in self.page_errors[:MAX_LINES]))
        if self.console:
            parts.append("The browser console:\n"
                         + "\n".join(f"  {c}" for c in self.console[:MAX_LINES]))
        if self.network:
            parts.append("Requests that failed:\n"
                         + "\n".join(f"  {n}" for n in self.network[:MAX_LINES]))
        if self.is_clean():
            parts.append("No console error, no failed request and no uncaught "
                         "exception. Whatever is wrong did not announce itself "
                         "to the browser — look for a handler that does nothing, "
                         "a condition that never matches, or data that is empty.")
        return "\n\n".join(parts)


def wanted_control(complaint: str) -> str:
    """The words most likely to name the control being complained about."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", complaint or "")
             if w.lower() not in _STOP and len(w) > 2]
    return " ".join(words[:3])


def reproduce(route: str, complaint: str = "", *, port: int = 5173,
              login: tuple = None, login_endpoint: str = "",
              timeout: int = 45_000) -> Reproduction:
    """Open `route`, press what the complaint names, and report what happened."""
    out = Reproduction(route=route or "/")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:                                      # noqa: BLE001
        out.why_not = f"Playwright is unavailable: {e}"
        return out

    base = f"http://127.0.0.1:{port}"
    target = wanted_control(complaint)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            try:
                if login and login_endpoint:
                    email, password = login
                    try:
                        r = ctx.request.post(base + login_endpoint,
                                             data={"email": email,
                                                   "password": password},
                                             timeout=20_000)
                        out.signed_in = r.status < 400
                    except Exception as e:                      # noqa: BLE001
                        log.info(f"pre-login failed: {e}")

                page = ctx.new_page()
                def _pageerror(e):
                    text = " ".join(str(e).split())[:600]
                    if not _is_noise(text):
                        out.page_errors.append(text)

                page.on("pageerror", _pageerror)

                def _console(m):
                    if m.type not in ("error", "warning"):
                        return
                    where = (m.location or {}).get("url") or ""
                    text = " ".join((m.text or "").split())[:400]
                    if _is_noise(text) or _is_noise(where):
                        return
                    line = f"{text}  [{where}]" if where else text
                    if line not in out.console:  # The same warning repeats
                        out.console.append(line)

                page.on("console", _console)

                def _response(r):
                    if r.status < 400 or _is_noise(r.url):
                        return
                    body = ""
                    try:
                        body = " ".join(r.text().split())[:220]
                    except Exception:                            # noqa: BLE001
                        pass
                    out.network.append(f"HTTP {r.status} {r.request.method} {r.url}"
                                       + (f" — {body}" if body else ""))

                page.on("response", _response)

                page.goto(base + (route or "/"), wait_until="load", timeout=timeout)
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:                                # noqa: BLE001
                    pass
                page.wait_for_timeout(SETTLE_MS)

                # Marked as run the moment the page is up, not at the end.
                out.ran = True

                if target:
                    try:
                        out.clicked, out.changed, out.filled = _press(page, target)
                    except Exception as e:                       # noqa: BLE001
                        log.debug(f"press failed: {e}")

                try:
                    out.html = (page.content() or "")[:60_000]
                except Exception:                                # noqa: BLE001
                    pass
                try:
                    import base64
                    shot = page.screenshot(type="png", full_page=False)
                    if len(shot) < 3_000_000:
                        out.screenshot_b64 = base64.b64encode(shot).decode()
                except Exception:                                # noqa: BLE001
                    pass
            finally:
                ctx.close()
                browser.close()
    except Exception as e:                                       # noqa: BLE001
        # Keep whatever was already seen.
        out.why_not = str(e)[:300]
        log.warning(f"reproduce failed: {e}")
    return out


_FILL_SCAN = """f => {
  const out = []
  let i = 0
  for (const el of f.querySelectorAll('input, select, textarea')) {
    const type = (el.type || '').toLowerCase()
    if (el.disabled || el.readOnly) continue
    if (type === 'hidden' || type === 'file') continue
    if (!el.required) continue
    if (el.value) continue
    el.setAttribute('data-af-fill', String(i))
    out.push({
      i, tag: el.tagName.toLowerCase(), type,
      name: el.name || el.id || el.getAttribute('aria-label') || el.tagName,
      options: el.tagName === 'SELECT'
        ? Array.from(el.options).map(o => o.value).filter(v => v) : [],
    })
    i++
  }
  return out
}"""


def _value_for(field, today) -> str:
    """Something the browser will accept for this input's type."""
    kind = field.get("type") or ""
    if kind == "email":
        return "test@example.com"
    if kind == "date":
        return today
    if kind in ("time",):
        return "09:00"
    if kind == "number":
        return "1"
    if kind == "tel":
        return "0771234567"
    if kind == "url":
        return "https://example.com"
    if kind == "password":
        return "Password123!"
    return "Test"


def _fill_required(page, control) -> list:
    """Give the form the values it insists on, before pressing its button."""
    try:
        form = control.evaluate_handle("el => el.closest('form')")
        if not form or form.evaluate("f => f === null"):
            return []
        fields = form.evaluate(_FILL_SCAN)
    except Exception as e:                                       # noqa: BLE001
        log.debug(f"form scan: {e}")
        return []

    from datetime import date, timedelta
    today = (date.today() + timedelta(days=7)).isoformat()

    done = []
    for f in fields or []:
        where = f"[data-af-fill=\"{f['i']}\"]"
        try:
            if f["tag"] == "select":
                if not f["options"]:
                    continue
                page.select_option(where, f["options"][0], timeout=2000)
                done.append(f"{f['name']}={f['options'][0]}")
            else:
                value = _value_for(f, today)
                page.fill(where, value, timeout=2000)
                done.append(f"{f['name']}={value}")
        except Exception as e:                                   # noqa: BLE001
            log.debug(f"fill {f['name']}: {e}")

    try:
        page.evaluate("() => document.querySelectorAll('[data-af-fill]')"
                      ".forEach(el => el.removeAttribute('data-af-fill'))")
    except Exception:                                            # noqa: BLE001
        pass
    return done


def _press(page, target: str) -> tuple:
    """Click the control the complaint names."""
    before = ""
    try:
        before = page.inner_text("body")[:4000]
    except Exception:                                            # noqa: BLE001
        pass

    control = None
    what = ""
    # Escaped: these are the user's own words.
    pattern = re.compile(re.escape(target), re.I)
    for how in (lambda: page.get_by_role("button", name=pattern).first,
                lambda: page.get_by_role("link", name=pattern).first,
                lambda: page.get_by_text(pattern).first):
        try:
            found = how()
            # A Locator is always truthy.
            if found is not None and found.count() > 0:
                found.wait_for(state="visible", timeout=1500)
                control = found
                break
        except Exception:                                        # noqa: BLE001
            continue

    if control is None:
        return "", False, []

    filled = _fill_required(page, control)

    try:
        what = " ".join((control.inner_text() or target).split())[:80]
        control.click(timeout=4000)
        page.wait_for_timeout(WAIT_MS)
    except Exception as e:                                       # noqa: BLE001
        return (f"{what or target} (could not be clicked: {str(e)[:80]})",
                False, filled)

    after = ""
    try:
        after = page.inner_text("body")[:4000]
    except Exception:                                            # noqa: BLE001
        pass
    return what, (after != before), filled


__all__ = ["Reproduction", "reproduce", "wanted_control"]
