"""Thin REST helpers for the Vercel operations the dashboard drives."""

from __future__ import annotations

from typing import Any

from .security import redact_text

VERCEL_API = "https://api.vercel.com"


def _request(method: str, path: str, token: str, team_id: str = "", **kwargs: Any) -> Any:
    import requests

    params = dict(kwargs.pop("params", {}) or {})
    if team_id:
        params["teamId"] = team_id
    response = requests.request(
        method,
        f"{VERCEL_API}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=kwargs.pop("timeout", 30),
        **kwargs,
    )
    if response.status_code >= 400:
        detail = ""
        try:
            detail = str(response.json().get("error", {}).get("message", ""))
        except Exception:
            detail = response.text[:200]
        raise RuntimeError(f"Vercel API {method} {path} failed ({response.status_code}): {redact_text(detail)}")
    if not response.content:
        return {}
    return response.json()


def get_project(token: str, project_id: str, team_id: str = "") -> dict[str, Any]:
    return _request("GET", f"/v9/projects/{project_id}", token, team_id)


def production_url(project: dict[str, Any]) -> str:
    """The stable production address, preferring an alias over the build URL."""
    targets = project.get("targets") or {}
    prod = targets.get("production") or {}
    for alias in prod.get("alias") or []:
        if alias:
            return f"https://{alias}"
    if prod.get("url"):
        return f"https://{prod['url']}"
    name = project.get("name") or ""
    return f"https://{name}.vercel.app" if name else ""


def list_deployments(token: str, project_id: str, team_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
    payload = _request(
        "GET", "/v6/deployments", token, team_id,
        params={"projectId": project_id, "limit": limit, "target": "production"},
    )
    return payload.get("deployments", []) or []


def deployment_events(token: str, deployment_id: str, team_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """Build log lines shaped for the Logs tab."""
    payload = _request(
        "GET", f"/v3/deployments/{deployment_id}/events", token, team_id,
        params={"limit": limit, "direction": "backward"},
    )
    events = payload if isinstance(payload, list) else payload.get("events", []) or []
    rows: list[dict[str, Any]] = []
    for item in events:
        text = item.get("text") or (item.get("payload") or {}).get("text") or ""
        if not text:
            continue
        rows.append({"timestamp": item.get("created") or item.get("date"), "message": redact_text(str(text))})
    return rows[-limit:]


def list_env(token: str, project_id: str, team_id: str = "") -> list[dict[str, Any]]:
    """Variable names and targets only - values are never requested."""
    payload = _request("GET", f"/v9/projects/{project_id}/env", token, team_id)
    return payload.get("envs", []) or []


def upsert_env(
    token: str,
    project_id: str,
    key: str,
    value: str,
    team_id: str = "",
    targets: tuple[str, ...] = ("production",),
) -> dict[str, Any]:
    """Create or replace a production environment variable."""
    return _request(
        "POST",
        f"/v10/projects/{project_id}/env",
        token,
        team_id,
        params={"upsert": "true"},
        json={"key": key, "value": value, "type": "encrypted", "target": list(targets)},
    )


def delete_env(token: str, project_id: str, env_id: str, team_id: str = "") -> dict[str, Any]:
    return _request("DELETE", f"/v9/projects/{project_id}/env/{env_id}", token, team_id)


def promote_deployment(token: str, project_id: str, deployment_id: str, team_id: str = "") -> dict[str, Any]:
    return _request("POST", f"/v9/projects/{project_id}/promote/{deployment_id}", token, team_id)


def delete_project(token: str, project_id: str, team_id: str = "") -> dict[str, Any]:
    return _request("DELETE", f"/v9/projects/{project_id}", token, team_id)


def list_domains(token: str, project_id: str, team_id: str = "") -> list[dict[str, Any]]:
    payload = _request("GET", f"/v9/projects/{project_id}/domains", token, team_id)
    return payload.get("domains", []) or []


def add_domain(token: str, project_id: str, domain: str, team_id: str = "") -> dict[str, Any]:
    """Add a domain and return any DNS record the operator must create."""
    return _request(
        "POST", f"/v10/projects/{project_id}/domains", token, team_id, json={"name": domain}
    )


def remove_domain(token: str, project_id: str, domain: str, team_id: str = "") -> dict[str, Any]:
    return _request("DELETE", f"/v9/projects/{project_id}/domains/{domain}", token, team_id)
