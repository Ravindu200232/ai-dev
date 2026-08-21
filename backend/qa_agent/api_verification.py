"""Focused API checks used by the QA pipeline."""
from __future__ import annotations


def probe_non_mutating_apis(analyzer, report, *, skip_health: bool = True):
    """Run the normal read-only API sweep."""
    analyzer.probe_api_routes(report, skip_health=skip_health)
    return report


def reprobe_affected_gets(analyzer, report, affected_files, finding_factory):
    """Recheck only GET APIs whose source was repaired."""
    affected = {str(p or "").replace("\\", "/") for p in affected_files or [] if p}
    for url, meta in sorted((getattr(report, "routes", None) or {}).items()):
        if meta.get("kind") != "api" or meta.get("dynamic"):
            continue
        if str(meta.get("file", "")).replace("\\", "/") not in affected:
            continue
        if "GET" not in (meta.get("methods") or []):
            continue
        status = analyzer._get_status(analyzer.base_url + url)
        if status is None or status == 404 or status >= 500:
            report.findings.append(finding_factory(
                "blocker", "ROUTE_ERROR", f"{url} returns HTTP {status}",
                path=meta.get("file", ""),
                fix=f"fix {meta.get('file','')} so {url} responds"))
    return report
