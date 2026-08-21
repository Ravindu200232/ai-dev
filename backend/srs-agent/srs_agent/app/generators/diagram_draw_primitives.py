"""Professional native vector renderers for SRS diagrams."""
from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from typing import Optional

from reportlab.graphics.shapes import Circle, Drawing, Ellipse, Group, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth

# Visual system — intentionally quiet and document-like.
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#52606D")
LINE = colors.HexColor("#B8C2CC")
LINE_DARK = colors.HexColor("#405261")
PANEL = colors.HexColor("#F7F9F8")
PANEL_2 = colors.HexColor("#F2F5F7")
GREEN = colors.HexColor("#2F7D5B")
GREEN_BG = colors.HexColor("#F4F9F6")
ORANGE = colors.HexColor("#B96821")
ORANGE_BG = colors.HexColor("#FFF8F1")
BLUE = colors.HexColor("#496A84")
BLUE_BG = colors.HexColor("#F4F7F9")
PURPLE = colors.HexColor("#6857A5")
PURPLE_BG = colors.HexColor("#F7F5FB")
WHITE = colors.white
BLACK = colors.HexColor("#202A33")
F = "Helvetica"
FB = "Helvetica-Bold"
FM = "Courier"


def _drawing(width: float, height: float) -> tuple[Drawing, Group]:
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=WHITE, strokeColor=None))
    g = Group()
    return d, g


def _clean(text: str) -> str:
    return " ".join(str(text or "").replace("\n", " ").split())


def _fit(text: str, font: str, size: float, maxw: float) -> str:
    text = _clean(text)
    if stringWidth(text, font, size) <= maxw:
        return text
    ell = "…"
    while text and stringWidth(text + ell, font, size) > maxw:
        text = text[:-1]
    return (text.rstrip() or "") + ell


def _wrap(text: str, font: str, size: float, maxw: float, max_lines: int = 2) -> list[str]:
    words = _clean(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = (cur + " " + word).strip()
        if not cur or stringWidth(trial, font, size) <= maxw:
            cur = trial
            continue
        lines.append(cur)
        cur = word
        if len(lines) == max_lines - 1:
            break
    remaining_idx = sum(len(x.split()) for x in lines)
    if len(lines) < max_lines and cur:
        tail_words = words[remaining_idx:]
        tail = " ".join(tail_words)
        lines.append(_fit(tail, font, size, maxw))
    return lines[:max_lines]


def _txt(g: Group, x, y, s, size=8.5, font=F, color=INK, anchor="start"):
    g.add(String(x, y, str(s), fontName=font, fontSize=size, fillColor=color, textAnchor=anchor))


def _text_block(g: Group, cx: float, cy: float, text: str, *, maxw: float,
                size: float = 8.5, font: str = F, color=INK, max_lines: int = 2,
                leading: float | None = None):
    lines = _wrap(text, font, size, maxw, max_lines=max_lines)
    leading = leading or (size + 2)
    start = cy + ((len(lines) - 1) * leading) / 2 - size * .36
    for i, line in enumerate(lines):
        _txt(g, cx, start - i * leading, line, size, font, color, "middle")


def _line(g: Group, x1, y1, x2, y2, color=INK, sw=1.0, dashed=False):
    ln = Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=sw)
    if dashed:
        ln.strokeDashArray = [5, 3]
    g.add(ln)
    return ln


def _arrow_head(g: Group, x1: float, y1: float, x2: float, y2: float,
                *, color=INK, sw=1.0, open_head=False, size=6.5):
    ang = math.atan2(y2 - y1, x2 - x1)
    p1 = (x2 - size * math.cos(ang - .48), y2 - size * math.sin(ang - .48))
    p2 = (x2 - size * math.cos(ang + .48), y2 - size * math.sin(ang + .48))
    if open_head:
        _line(g, x2, y2, p1[0], p1[1], color, sw)
        _line(g, x2, y2, p2[0], p2[1], color, sw)
    else:
        g.add(Polygon(points=[x2, y2, p1[0], p1[1], p2[0], p2[1]],
                      fillColor=color, strokeColor=color))


def _arrow(g: Group, x1, y1, x2, y2, *, color=INK, sw=1.0, dashed=False,
           label: str | None = None, open_head=False, label_size=7.2):
    _line(g, x1, y1, x2, y2, color, sw, dashed)
    _arrow_head(g, x1, y1, x2, y2, color=color, sw=sw, open_head=open_head)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        cap = max(62, math.hypot(x2 - x1, y2 - y1) - 18)
        _txt(g, mx, my + 5, _fit(label, F, label_size, cap), label_size, F, INK, "middle")


def _orth_arrow(g: Group, x1: float, y1: float, x2: float, y2: float, *,
                via_x: float | None = None, via_y: float | None = None,
                color=INK, sw=.95, dashed=False, label: str | None = None,
                open_head=True, head=True, label_size=7.0):
    """Orthogonal connector with one bend pair and one terminal arrow head."""
    if via_x is not None:
        pts = [(x1, y1), (via_x, y1), (via_x, y2), (x2, y2)]
    elif via_y is not None:
        pts = [(x1, y1), (x1, via_y), (x2, via_y), (x2, y2)]
    else:
        via_x = (x1 + x2) / 2
        pts = [(x1, y1), (via_x, y1), (via_x, y2), (x2, y2)]
    for a, b in zip(pts[:-1], pts[1:]):
        _line(g, a[0], a[1], b[0], b[1], color, sw, dashed)
    a, b = pts[-2], pts[-1]
    if head:
        _arrow_head(g, a[0], a[1], b[0], b[1], color=color, sw=sw,
                    open_head=open_head, size=6.2)
    if label:
        # Label the longest segment, not the bend.
        segs = list(zip(pts[:-1], pts[1:]))
        a, b = max(segs, key=lambda p: math.hypot(p[1][0]-p[0][0], p[1][1]-p[0][1]))
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if abs(a[0]-b[0]) >= abs(a[1]-b[1]):
            _txt(g, mx, my + 5, _fit(label, F, label_size, max(70, abs(a[0]-b[0])-12)),
                 label_size, F, MUTED, "middle")
        else:
            _txt(g, mx + 5, my, _fit(label, F, label_size, 100), label_size, F, MUTED)


def _rounded(g: Group, x, y, w, h, label, *, stroke=GREEN, fill=PANEL,
             size=8.5, radius=10, font=F, max_lines=2):
    g.add(Rect(x, y, w, h, rx=radius, ry=radius, fillColor=fill,
               strokeColor=stroke, strokeWidth=1.25))
    _text_block(g, x+w/2, y+h/2, label, maxw=w-14, size=size, font=font,
                max_lines=max_lines)


def _diamond(g: Group, cx, cy, w, h, label, *, stroke=ORANGE, fill=WHITE, size=7.5):
    pts = [cx, cy+h/2, cx+w/2, cy, cx, cy-h/2, cx-w/2, cy]
    g.add(Polygon(points=pts, fillColor=fill, strokeColor=stroke, strokeWidth=1.25))
    _text_block(g, cx, cy, label, maxw=w*.64, size=size, max_lines=2)


def _start(g: Group, cx, cy, r=7):
    g.add(Circle(cx, cy, r, fillColor=BLACK, strokeColor=BLACK))


def _end(g: Group, cx, cy, r=8):
    g.add(Circle(cx, cy, r, fillColor=WHITE, strokeColor=BLACK, strokeWidth=1.5))
    g.add(Circle(cx, cy, r-3, fillColor=BLACK, strokeColor=BLACK))


def _stick_figure(g: Group, cx, cy, label, scale=1.0):
    r = 7*scale
    g.add(Circle(cx, cy+25*scale, r, fillColor=WHITE, strokeColor=BLACK, strokeWidth=1.25))
    _line(g, cx, cy+18*scale, cx, cy-4*scale, BLACK, 1.25)
    _line(g, cx-16*scale, cy+8*scale, cx+16*scale, cy+8*scale, BLACK, 1.25)
    _line(g, cx, cy-4*scale, cx-14*scale, cy-24*scale, BLACK, 1.25)
    _line(g, cx, cy-4*scale, cx+14*scale, cy-24*scale, BLACK, 1.25)
    _txt(g, cx, cy-39*scale, _fit(label, F, 8.5*scale, 110*scale), 8.5*scale, F, INK, "middle")


def _explicit_decision(step: str) -> bool:
    text = str(step or "")
    return (bool(re.search(r"\b(if|whether|when)\b|\?", text, re.I))
            and bool(re.search(r"\b(else|otherwise|if not|on failure|on rejection)\b", text, re.I)))


def _tokenize(text: str) -> set[str]:
    stop = {"the","a","an","and","or","to","of","in","on","for","with","using","manage","management",
            "system","application","app","page","screen","module","data","api","service","customer","user","admin"}
    vals = re.findall(r"[a-z0-9]+", str(text or "").lower())
    out = set()
    for v in vals:
        if len(v) < 3 or v in stop:
            continue
        # Light singularisation for relationship matching only
        if len(v) > 4 and v.endswith("ies"):
            v = v[:-3] + "y"
        elif len(v) > 4 and v.endswith("s"):
            v = v[:-1]
        out.add(v)
    return out


def _overlap(a: str, b: str) -> int:
    return len(_tokenize(a) & _tokenize(b))


def _not_applicable(width: float, title: str, reason: str) -> Drawing:
    H = 160
    d, g = _drawing(width, H)
    x, y, w, h = 34, 32, width-68, 92
    g.add(Rect(x, y, w, h, rx=8, ry=8, fillColor=PANEL_2, strokeColor=LINE, strokeWidth=1.1))
    _txt(g, x+18, y+h-28, "Not applicable to the current specification", 10, FB, INK)
    _text_block(g, x+w/2, y+h/2-10, reason, maxw=w-36, size=8.5, color=MUTED, max_lines=3)
    d.add(g)
    return d


# Use Case Diagram — UML behavioral.
