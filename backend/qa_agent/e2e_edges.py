"""The things nobody writes a journey for."""
from __future__ import annotations

import re

from .e2e_common import GOTO_TIMEOUT, log


class E2EEdgeChecksMixin:
    # ObjectId-shaped and all zeroes: syntactically valid
    ABSENT_ID = "000000000000000000000000"
    ABSENT_ROUTE_CAP = 4

    _SIGN_IN_JS = """async ({ email, password }) => {
        const r = await fetch('/api/auth/sign-in/email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });
        return r.status;
    }"""

    def credential_integrity(self, page) -> list:
        """`(what, message)` for every bad credential the app let through."""
        accs = [a for a in self.accounts()
                if a.get("email") and a.get("password")]
        if not accs:
            return []
        acc = accs[0]
        email = str(acc["email"])
        domain = email.split("@", 1)[1] if "@" in email else "example.com"

        probes = (
            ("a wrong password",
             {"email": email, "password": str(acc["password"]) + "-not-it-9Z"}),
            ("an account that does not exist",
             {"email": f"nobody.{self.ABSENT_ID[:8]}@{domain}",
              "password": str(acc["password"])}),
        )

        out = []
        for what, creds in probes:
            try:
                page.context.clear_cookies()
                status = int(page.evaluate(self._SIGN_IN_JS, creds) or 0)
            except Exception as e:                             # noqa: BLE001
                log.debug(f"credential probe ({what}): {e}")
                continue
            if 200 <= status < 300:
                out.append((what, f"POST /api/auth/sign-in/email answered "
                                  f"{status} for {what} — it must refuse"))
                continue
            if self._session_alive(page, timeout_ms=1500):
                out.append((what, f"sign-in refused {what} with {status}, but a "
                                  f"browser session exists afterwards anyway"))
        try:
            page.context.clear_cookies()
        except Exception:
            pass
        return out

    def absent_record_integrity(self, page) -> list:
        """`(route, message)` for each detail page that breaks on a missing row."""
        try:
            routes = [u for u, m in sorted(self.route_map().items())
                      if m.get("kind") == "page" and "[" in u]
        except Exception as e:                                 # noqa: BLE001
            log.debug(f"absent record routes: {e}")
            return []
        if not routes:
            return []

        out, seen = [], set()
        for pattern in routes[:self.ABSENT_ROUTE_CAP]:
            url = re.sub(r"\[[^\]]+\]", self.ABSENT_ID, pattern)
            if url in seen:
                continue
            seen.add(url)
            try:
                resp = page.goto(self.base_url + url, timeout=GOTO_TIMEOUT,
                                 wait_until="domcontentloaded")
            except Exception as e:                             # noqa: BLE001
                out.append((pattern, f"opening {url} failed: "
                                     f"{type(e).__name__}: {e}"[:250]))
                continue
            status = resp.status if resp else None
            if status and status >= 500:
                out.append((pattern, f"{url} answered HTTP {status} — a record "
                                     f"that does not exist must render a not-"
                                     f"found state, not fail the request"))
        return out
