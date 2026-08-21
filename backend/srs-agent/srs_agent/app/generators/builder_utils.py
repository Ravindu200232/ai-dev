"""Small helpers for the builder handoff."""
from __future__ import annotations

import re

from .builder_constants import KINDS

def _strip_forbidden(text: str) -> str:
    """Compatibility name; V2 deliberately preserves product requirements."""
    return str(text or "").strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".")


def _entity(name: str) -> str:
    """`sale items` -> `SaleItem`; useful as a builder-facing hint."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", str(name or "")) if p]
    singular = []
    for p in parts:
        if p.lower().endswith("ies"):
            p = p[:-3] + "y"
        elif p.lower().endswith("es") and p.lower().endswith(("ches", "shes", "sses", "xes")):
            p = p[:-2]
        elif p.lower().endswith("s") and not p.lower().endswith("ss"):
            p = p[:-1]
        singular.append(p[:1].upper() + p[1:])
    return "".join(singular) or "Record"


def _normal_type(raw: str, references: str = "") -> str:
    """Translate SRS data types without losing Mongo relation semantics."""
    low = re.sub(r"[^a-z0-9]+", "", str(raw or "").lower())
    if references or low in {"objectid", "mongoid", "reference", "ref", "foreignkey"}:
        return "ObjectId"
    if low in {"decimal", "integer", "int", "float", "double", "number", "numeric"}:
        return "Number"
    if low in {"boolean", "bool"}:
        return "Boolean"
    if low in {"date", "datetime", "timestamp"}:
        return "Date"
    if low in {"objectidarray", "references"}:
        return "ObjectId[]"
    if low in {"array", "list"} or low.endswith("array") or low.startswith("arrayof"):
        return "Array"
    if low in {"object", "json", "map", "dict", "dictionary"}:
        return "Object"
    return "String" if not raw else str(raw).strip()


def _field_spec(field: dict) -> dict:
    name = _clean(field.get("name", ""))
    ref = _clean(field.get("references") or field.get("foreign_key") or "")
    raw_type = _clean(field.get("type", "String")) or "String"
    nullable = field.get("nullable")
    values = list(field.get("values") or field.get("enum") or [])
    spec = {
        "name": name,
        "type": _normal_type(raw_type, ref),
        "srs_type": raw_type,
        "references": ref or None,
        "required": (None if nullable is None else not bool(nullable)),
        "unique": bool(field.get("unique")),
        "primary_key": bool(field.get("primary_key") or field.get("primary")),
        "default": field.get("default"),
        "enum": values,
    }
    return spec


def _route_segment(segment: str) -> str:
    segment = str(segment or "").strip()
    m = re.fullmatch(r"\{([^}]+)\}", segment)
    return f"[{m.group(1)}]" if m else segment


def _normal_route_pattern(route: str) -> str:
    raw = str(route or "/").split("?", 1)[0].split("#", 1)[0].strip()
    if not raw or raw == "/":
        return "/"
    bits = [_route_segment(x) for x in raw.strip("/").split("/") if x]
    return "/" + "/".join(bits)


def _page_file(route: str) -> str:
    path = str(route or "/").split("?", 1)[0].split("#", 1)[0].strip()
    if not path or path == "/":
        return "app/page.jsx"
    path = _normal_route_pattern(path)
    bits = [x for x in path.strip("/").split("/") if x]
    return "app/" + "/".join(bits) + "/page.jsx"


def _api_file(path: str) -> str:
    route = _normal_route_pattern(path).strip("/")
    bits = [x for x in route.split("/") if x]
    return "app/" + "/".join(bits) + "/route.js"


def _kind_for(text: str) -> str:
    low = str(text or "").lower()
    if re.search(r"\b(create|add|register|submit|book|place)\b", low):
        return "create"
    if re.search(r"\b(update|edit|modify|change|mark|approve|assign)\b", low):
        return "edit"
    if re.search(r"\b(delete|remove|cancel)\b", low):
        return "delete"
    if re.search(r"\b(list|browse|search|filter|view|see|show|history)\b", low):
        return "list"
    return "feature"


def _trace_map(doc: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in (doc.get("requirement_traceability_matrix") or []):
        if not isinstance(row, dict):
            continue
        rid = _clean(row.get("requirement_id") or row.get("id") or "")
        if rid:
            out[rid] = {
                "module": _clean(row.get("module") or ""),
                "pages": [str(x) for x in (row.get("pages") or []) if str(x).strip()],
                "tables": [str(x) for x in (row.get("tables") or []) if str(x).strip()],
                "test_case": _clean(row.get("test_case") or "") or None,
            }
    return out


def _roles_for_route(doc: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for page in (doc.get("protected_pages") or []):
        if not isinstance(page, dict):
            continue
        route = _normal_route_pattern(_clean(page.get("route") or ""))
        if route:
            out[route] = [_clean(r) for r in (page.get("allowed_roles") or []) if _clean(r)]
    for page in (doc.get("public_pages") or []):
        if isinstance(page, dict) and _clean(page.get("route") or ""):
            out.setdefault(_normal_route_pattern(_clean(page.get("route") or "")), [])
    return out


