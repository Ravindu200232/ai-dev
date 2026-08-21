"""Checkpoint state restoration for focused browser reruns."""
from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlsplit


class E2EStateMixin:
    def _journey_role(self, journey=None, scenario=None) -> str:
        return str((journey or {}).get("role") or getattr(scenario, "role", "") or "").strip().lower()

    def _role_requires_session(self, journey=None, scenario=None) -> bool:
        role = self._journey_role(journey, scenario)
        return bool(role and role not in set(getattr(self, "_SIGNED_OUT", ()) or ()))

    def _session_snapshot(self, page) -> dict:
        try:
            got = page.evaluate(
                """async () => { try {
                    const r = await fetch('/api/auth/get-session', {cache:'no-store'});
                    let body = null; try { body = await r.json(); } catch (_) {}
                    return {status:r.status, body};
                } catch (e) { return {status:0, error:String(e)}; } }""")
            return got if isinstance(got, dict) else {"status": 0, "body": got}
        except Exception as exc:
            return {"status": 0, "error": str(exc)[:180]}

    @staticmethod
    def _session_identity(body) -> tuple[str, str]:
        if not isinstance(body, dict):
            return "", ""
        user = body.get("user") if isinstance(body.get("user"), dict) else body
        email = str(user.get("email") or "").strip().lower() if isinstance(user, dict) else ""
        role = str(user.get("role") or body.get("role") or "").strip().lower() if isinstance(user, dict) else ""
        return email, role

    def _session_matches_expected_role(self, page, journey=None, scenario=None) -> bool:
        if not self._role_requires_session(journey, scenario):
            return True
        snap = self._session_snapshot(page)
        if int(snap.get("status") or 0) != 200 or not snap.get("body"):
            return False
        expected_role = self._journey_role(journey, scenario)
        account = self.account_for(expected_role)
        expected_email = str((account or {}).get("email") or "").strip().lower()
        email, role = self._session_identity(snap.get("body"))
        if expected_email and email:
            return email == expected_email
        if expected_role and role:
            return role == expected_role
        return bool(snap.get("body"))

    def _restore_checkpoint_session(self, page, journey=None, scenario=None,
                                    route: str = "") -> tuple[bool, str]:
        """Restore the expected demo identity without replaying business steps."""
        if not self._role_requires_session(journey, scenario):
            return True, "signed-out journey"
        if self._session_matches_expected_role(page, journey, scenario):
            return True, "checkpoint session valid"

        role = self._journey_role(journey, scenario)
        account = self.account_for(role)
        if not account:
            return False, f"no demo account exists for role {role or '?'}"
        try:
            if not self._sign_in(page, account):
                return False, f"could not restore the {role or 'required'} demo session"
            if route:
                page.goto(self.base_url + route, wait_until="domcontentloaded")
            if not self._session_matches_expected_role(page, journey, scenario):
                return False, f"restored session does not match role {role or '?'}"
            self._log("INFO", f"   🔐 restored {role} session at the browser checkpoint")
            return True, "session restored"
        except Exception as exc:
            return False, f"checkpoint auth restore failed: {type(exc).__name__}: {exc}"

    def _checkpoint_business_values(self, route: str, sc=None, upto: int = 0) -> dict:
        """Persist dynamic query values and values already entered before a repair."""
        out = {"query": {}, "filled": {}}
        try:
            out["query"] = dict(parse_qsl(urlsplit(str(route or "")).query, keep_blank_values=True))
        except Exception:
            pass
        for step in (getattr(sc, "steps", []) or [])[:max(0, int(upto or 0))]:
            sel = getattr(step, "selector", None)
            if getattr(step, "verb", "") not in ("FILL", "SELECT") or not sel:
                continue
            key = str(getattr(sel, "pattern", "") or getattr(sel, "role", "") or "").strip()
            if key:
                out["filled"][key] = str(getattr(step, "value", "") or "")[:220]
        return out
