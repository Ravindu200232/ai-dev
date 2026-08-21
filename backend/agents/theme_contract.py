"""Extract a stable visual contract from an approved HTML design."""
from __future__ import annotations

from collections import Counter, OrderedDict
import html as _html
import re

_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
_DECL_RE = re.compile(r"([\w-]+)\s*:\s*([^;{}]+)")
_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGB_RE = re.compile(r"rgba?\(([^)]+)\)", re.I)
_HSL_RE = re.compile(r"hsla?\(([^)]+)\)", re.I)
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)")
_LENGTH_RE = re.compile(r"(?<![\w.-])(-?(?:\d*\.)?\d+)(px|rem|em|%)\b", re.I)


def _styles(html: str) -> str:
    return "\n".join(_html.unescape(x) for x in _STYLE_RE.findall(html or ""))


def _rules(css: str) -> list[tuple[str, OrderedDict[str, str]]]:
    out = []
    for selector, body in _RULE_RE.findall(css or ""):
        selector = " ".join(selector.split()).strip()
        if not selector or selector.startswith("@"):
            continue
        decls: OrderedDict[str, str] = OrderedDict()
        for key, value in _DECL_RE.findall(body):
            decls[key.lower()] = " ".join(value.split()).strip()
        if decls:
            out.append((selector, decls))
    return out


def _rgb_to_hex(raw: str) -> str:
    parts = [x.strip() for x in raw.split(",")]
    if len(parts) < 3:
        return ""
    vals = []
    for p in parts[:3]:
        if p.endswith("%"):
            try:
                vals.append(round(float(p[:-1]) * 2.55))
            except ValueError:
                return ""
        else:
            try:
                vals.append(round(float(p)))
            except ValueError:
                return ""
    if any(v < 0 or v > 255 for v in vals):
        return ""
    return "#" + "".join(f"{v:02x}" for v in vals)


def _colors(value: str, variables: dict[str, str]) -> list[str]:
    value = str(value or "")
    for name in _VAR_RE.findall(value):
        if name in variables:
            value += " " + variables[name]
    out = [x.lower() for x in _HEX_RE.findall(value)]
    for raw in _RGB_RE.findall(value):
        got = _rgb_to_hex(raw)
        if got:
            out.append(got)
    # Keep HSL exact when conversion would invent rounding
    out.extend("hsl(" + " ".join(x.split()) + ")" for x in _HSL_RE.findall(value))
    return out


def _pick_rules(rules, patterns: list[str], limit: int = 4) -> list[tuple[str, dict]]:
    found = []
    for selector, decls in rules:
        low = selector.lower()
        if any(re.search(p, low) for p in patterns):
            found.append((selector, decls))
        if len(found) >= limit:
            break
    return found


def _subset(decls: dict, names: tuple[str, ...]) -> dict[str, str]:
    return {k: decls[k] for k in names if k in decls}


def extract_theme_contract(html: str) -> dict:
    """Return CSS facts without asking the model to infer visual values."""
    css = _styles(html)
    rules = _rules(css)
    variables: OrderedDict[str, str] = OrderedDict()
    for selector, decls in rules:
        if selector.lower() in {":root", "html", "body", "html, body", "body, html"}:
            for key, value in decls.items():
                if key.startswith("--") and key not in variables:
                    variables[key] = value
    for _selector, decls in rules:
        for key, value in decls.items():
            if key.startswith("--") and key not in variables:
                variables[key] = value

    color_counts: Counter[str] = Counter()
    radius_counts: Counter[str] = Counter()
    spacing_counts: Counter[str] = Counter()
    shadow_counts: Counter[str] = Counter()
    width_counts: Counter[str] = Counter()
    font_counts: Counter[str] = Counter()

    for _selector, decls in rules:
        for prop, value in decls.items():
            if any(x in prop for x in ("color", "background", "border", "outline")):
                color_counts.update(_colors(value, variables))
            if prop == "border-radius":
                radius_counts[value] += 1
            if prop in {"gap", "row-gap", "column-gap", "padding", "padding-top", "padding-right",
                        "padding-bottom", "padding-left", "margin", "margin-top", "margin-right",
                        "margin-bottom", "margin-left"}:
                spacing_counts.update("".join(m) for m in _LENGTH_RE.findall(value))
            if prop == "box-shadow" and value.lower() != "none":
                shadow_counts[value] += 1
            if prop in {"max-width", "width"}:
                width_counts[value] += 1
            if prop == "font-family":
                font_counts[value] += 1

    key_props = (
        "background", "background-color", "color", "border", "border-color",
        "border-radius", "box-shadow", "font-family", "font-size", "font-weight",
        "line-height", "letter-spacing", "height", "padding", "gap", "max-width",
    )
    groups = {
        "body": _pick_rules(rules, [r"(^|,)\s*(?:html\s+)?body(?:\b|\s|,)"] , 2),
        "navigation": _pick_rules(rules, [r"\bnav\b", r"\bheader\b", r"\.navbar\b", r"\.topbar\b", r"\.sidebar\b"], 5),
        "headings": _pick_rules(rules, [r"(^|,)\s*h1\b", r"(^|,)\s*h2\b", r"\.title\b", r"\.heading\b"], 5),
        "buttons": _pick_rules(rules, [r"\bbutton\b", r"\.btn\b", r"\.button\b"], 5),
        "fields": _pick_rules(rules, [r"\binput\b", r"\bselect\b", r"\btextarea\b", r"\.field\b"], 5),
        "surfaces": _pick_rules(rules, [r"\.card\b", r"\.panel\b", r"\.surface\b", r"\.tile\b", r"\.modal\b"], 5),
        "containers": _pick_rules(rules, [r"\.container\b", r"\.shell\b", r"\.wrap\b", r"\.content\b"], 4),
    }

    compact_groups = {}
    for name, rows in groups.items():
        compact = []
        for selector, decls in rows:
            picked = _subset(decls, key_props)
            if picked:
                compact.append((selector, picked))
        if compact:
            compact_groups[name] = compact

    return {
        "css_present": bool(css.strip()),
        "variables": variables,
        "colors": color_counts.most_common(14),
        "radii": radius_counts.most_common(8),
        "spacing": spacing_counts.most_common(12),
        "shadows": shadow_counts.most_common(6),
        "widths": width_counts.most_common(8),
        "fonts": font_counts.most_common(6),
        "groups": compact_groups,
    }


def _pairs(rows: list[tuple[str, int]]) -> str:
    return ", ".join(v for v, _n in rows)


def theme_facts_markdown(html: str) -> str:
    """Render controller-extracted CSS facts for design.md and the model."""
    c = extract_theme_contract(html)
    if not c["css_present"]:
        return ""
    lines = [
        "### Approved HTML theme receipt",
        "These values were read directly from `chosen.html`. They outrank visual defaults.",
    ]
    if c["variables"]:
        lines.append("- CSS tokens: " + "; ".join(
            f"{k}={v}" for k, v in list(c["variables"].items())[:18]))
    if c["colors"]:
        lines.append("- Observed colours: " + _pairs(c["colors"]))
    if c["fonts"]:
        lines.append("- Font families: " + _pairs(c["fonts"]))
    if c["radii"]:
        lines.append("- Radius scale: " + _pairs(c["radii"]))
    if c["spacing"]:
        lines.append("- Spacing values: " + _pairs(c["spacing"]))
    if c["widths"]:
        lines.append("- Width/max-width values: " + _pairs(c["widths"]))
    if c["shadows"]:
        lines.append("- Shadows: " + _pairs(c["shadows"]))

    for group, rows in c["groups"].items():
        lines.append(f"- {group.title()} CSS:")
        for selector, decls in rows:
            lines.append("  - `" + selector[:90] + "`: " + "; ".join(
                f"{k}: {v}" for k, v in decls.items()))
    return "\n".join(lines)[:9000]


__all__ = ["extract_theme_contract", "theme_facts_markdown"]
