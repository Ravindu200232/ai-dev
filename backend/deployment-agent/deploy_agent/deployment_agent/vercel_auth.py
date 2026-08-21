"""Locate the token `vercel login` wrote, and identify who it belongs to."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

VERCEL_API = "https://api.vercel.com"

SIGN_IN_HINT = (
    "The Vercel CLI is not signed in. Open the Vercel Connection tab and run the sign-in, "
    "or paste a Vercel access token there."
)


def _candidate_auth_files() -> list[Path]:
    """Every place a `vercel login` token is known to live, most specific first."""
    candidates: list[Path] = []
    override = os.environ.get("VERCEL_DIR")
    if override:
        candidates.append(Path(override) / "auth.json")
    home = Path.home()
    candidates.append(home / ".vercel" / "auth.json")
    for variable in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if not base:
            continue
        candidates.append(Path(base) / "xdg.data" / "com.vercel.cli" / "auth.json")
        candidates.append(Path(base) / "com.vercel.cli" / "auth.json")
    candidates.append(home / "Library" / "Application Support" / "com.vercel.cli" / "auth.json")
    xdg = os.environ.get("XDG_DATA_HOME")
    candidates.append((Path(xdg) if xdg else home / ".local" / "share") / "com.vercel.cli" / "auth.json")
    return candidates


def read_vercel_token() -> str:
    """The first non-empty token found, or "" when the CLI is not signed in."""
    for path in _candidate_auth_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        token = str(data.get("token") or "").strip()
        if token:
            return token
    return ""


def require_vercel_token(supplied: str = "") -> str:
    token = str(supplied or "").strip() or read_vercel_token()
    if not token:
        raise ValueError(SIGN_IN_HINT)
    return token


def vercel_identity(token: str) -> dict[str, Any]:
    """Who the token belongs to."""
    import requests

    response = requests.get(
        f"{VERCEL_API}/v2/user",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if response.status_code == 403:
        raise ValueError("The Vercel token was rejected. Sign in again from the Vercel Connection tab.")
    response.raise_for_status()
    user = response.json().get("user", {})
    return {
        "username": user.get("username", ""),
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "id": user.get("id", ""),
    }


def connection_status(supplied: str = "") -> dict[str, Any]:
    """Non-throwing summary for the dashboard."""
    token = str(supplied or "").strip() or read_vercel_token()
    if not token:
        return {"connected": False, "source": "", "message": SIGN_IN_HINT}
    source = "pasted token" if supplied else "vercel CLI"
    try:
        identity = vercel_identity(token)
    except Exception as exc:
        return {"connected": False, "source": source, "message": str(exc)[:200]}
    return {"connected": True, "source": source, **identity}
