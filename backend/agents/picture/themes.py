"""Whole-application visual designs generated before implementation."""
from __future__ import annotations

import html as _html
import random
import re

from .theme_contract import theme_facts_markdown

COUNT = 5
SLOW_MODEL_COUNT = 3
_SLOW_HTML_FAMILIES = ("minimax", "qwen3")


def design_count(model: str) -> int:
    """Use fewer parallel previews only for the known slow reasoning families."""
    name = str(model or "").strip().lower()
    return SLOW_MODEL_COUNT if any(key in name for key in _SLOW_HTML_FAMILIES) else COUNT

# Coherent studio-grade systems. An earlier version combined palette, type,
# shape and move at random; the results were different but not good together,
# so each direction is now written as one coherent whole.
_PRO_DIRECTIONS = [
    {
        "palette": "porcelain white, graphite ink, mineral grey and one precise cobalt accent",
        "type": "crisp neo-grotesque hierarchy with a 48-64px display and compact UI labels",
        "shape": "8px controls, 12px primary surfaces, hairline separators and no decorative pills",
        "density": "balanced editorial spacing over a disciplined 12-column grid",
        "move": "an architectural left rail and strong shared alignment lines",
        "quality": "Swiss editorial precision — quiet, expensive and information-led",
    },
    {
        "palette": "ink-black and deep navy shell, warm white content, restrained champagne accent",
        "type": "high-contrast serif display paired with a neutral sans interface",
        "shape": "sharp outer geometry, subtly rounded 6px controls, low-opacity depth",
        "density": "roomy executive overview with compact operational details",
        "move": "cinematic dark navigation framing a bright, highly legible work surface",
        "quality": "premium executive product — authoritative without dashboard clichés",
    },
    {
        "palette": "warm bone, espresso ink, clay neutrals and a sparing vermilion action colour",
        "type": "confident editorial serif headlines with highly readable sans body copy",
        "shape": "soft 10px surfaces, tactile 4px controls and fine warm-grey rules",
        "density": "generous content rhythm with compact transactional modules",
        "move": "large product storytelling moments balanced by rigorous utility panels",
        "quality": "premium consumer craft — warm, tactile and conversion-aware",
    },
    {
        "palette": "ice white, carbon text, steel greys and one clinical teal signal",
        "type": "one disciplined sans family with tabular numerals and a decisive weight range",
        "shape": "4px radius, exact 1px rules and state colour used only where meaningful",
        "density": "dense daily-use data with spacious headers and clear scan lines",
        "move": "precision status rails and exceptionally legible tables/forms",
        "quality": "high-trust operations UI — fast to scan, calm under heavy data",
    },
    {
        "palette": "soft chalk, charcoal, muted olive and a single burnished-gold highlight",
        "type": "oversized light display type, restrained sans text and tiny uppercase metadata",
        "shape": "16px feature surfaces, 8px controls and depth created mainly by spacing",
        "density": "very roomy primary moments with deliberately compact supporting content",
        "move": "asymmetric editorial composition with one memorable focal object",
        "quality": "quiet luxury — art-directed, minimal and free of generic card grids",
    },
    {
        "palette": "clean white, blue-black ink, cool grey fields and one vivid signal-red accent",
        "type": "bold grotesque display headings with compact, practical body typography",
        "shape": "square containers, 6px controls and purposeful solid action blocks",
        "density": "compact product utility with generous section boundaries",
        "move": "strong numbered workflow steps and a persistent contextual action area",
        "quality": "modern product utility — direct, energetic and highly usable",
    },
    {
        "palette": "paper white, dark forest ink, sage neutrals and one civic orange accent",
        "type": "humanist sans typography with strong reading sizes and plain-language labels",
        "shape": "6px universal radius, visible focus rings and separation by rules rather than shadow",
        "density": "accessible, calm spacing designed for mixed-confidence users",
        "move": "clear wayfinding bands and exceptionally strong form hierarchy",
        "quality": "public-service clarity — inclusive, credible and deliberately uncomplicated",
    },
    {
        "palette": "near-black canvas, soft white type, smoke surfaces and one electric lime signal",
        "type": "large geometric display type with monospace metadata and restrained body text",
        "shape": "hard-edged frames, 4px controls and one soft-radius media treatment",
        "density": "dramatic primary composition with disciplined supporting information",
        "move": "a gallery-like index paired with a focused detail/work area",
        "quality": "creative-direction polish — expressive but still production-usable",
    },
]


def random_directions(n: int = COUNT, rng: random.Random | None = None) -> list[dict]:
    """Choose coherent art directions rather than mixing unrelated style parts."""
    rng = rng or random.Random()
    n = max(1, min(n, len(_PRO_DIRECTIONS)))
    out = []
    for i, source in enumerate(rng.sample(_PRO_DIRECTIONS, n), start=1):
        out.append({**source, "index": i, "seed": rng.randrange(1000, 9999)})
    return out


def direction_text(d: dict) -> str:
    return "\n".join([
        f"- palette: {d['palette']}",
        f"- type: {d['type']}",
        f"- shape: {d['shape']}",
        f"- density: {d['density']}",
        f"- one signature move: {d['move']}",
        f"- quality target: {d.get('quality', 'studio-grade product design')}",
    ])


def _text_values(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def site_map_from_plan(plan: dict | None, doc: dict | None) -> list[dict]:
    """Build one canonical sitemap shared by every visual direction."""
    plan, doc = plan or {}, doc or {}
    candidates = []
    for access, key in (("public", "public_pages"),
                        ("protected", "protected_pages")):
        for page in doc.get(key) or []:
            if not isinstance(page, dict):
                continue
            candidates.append({
                "name": page.get("page_name") or page.get("name"),
                "route": page.get("route"),
                "access": access,
                "roles": page.get("allowed_roles") or [],
                "sections": page.get("sections") or [],
                "functions": page.get("functions") or [],
                "page_type": page.get("page_type") or "",
            })
    if not candidates:
        for screen in plan.get("screens") or []:
            if not isinstance(screen, dict):
                continue
            roles = _text_values(screen.get("who") or screen.get("allowed_roles"))
            candidates.append({
                "name": screen.get("name") or screen.get("page_name"),
                "route": screen.get("route"),
                "access": "protected" if roles else "public",
                "roles": roles,
                "sections": screen.get("sections") or [],
                "functions": (screen.get("functions") or
                              ([screen.get("purpose")] if screen.get("purpose") else [])),
                "page_type": screen.get("page_type") or "",
            })

    pages, seen, used_slugs = [], set(), set()
    for index, page in enumerate(candidates):
        name = " ".join(str(page.get("name") or "").split()).strip()
        if not name:
            continue
        route = str(page.get("route") or "").strip()
        if not route:
            route = "/" if not pages else "/" + _slug(name)
        key = (route.casefold(), name.casefold())
        if key in seen:
            continue
        seen.add(key)
        slug = _slug(name)
        if slug in used_slugs:
            slug = f"{slug}-{index + 1}"
        used_slugs.add(slug)
        pages.append({
            "name": name,
            "slug": slug,
            "route": route,
            "access": str(page.get("access") or "public"),
            "roles": _text_values(page.get("roles")),
            "sections": _text_values(page.get("sections"))[:8],
            "functions": _text_values(page.get("functions"))[:8],
            "page_type": str(page.get("page_type") or ""),
        })
    return pages


def representative_pages(site_map: list[dict], limit: int = 3) -> list[dict]:
    """Choose the same representative surfaces for all five designs."""
    pages = [dict(page) for page in (site_map or []) if page.get("name")]
    if len(pages) <= limit:
        return pages

    auth_words = re.compile(r"\b(?:sign|log)[ -]?(?:in|up)|register|forgot\b", re.I)
    low_value = re.compile(r"\babout|contact|terms|privacy|login|sign in\b", re.I)

    def content_score(page):
        text = " ".join([str(page.get("name") or ""),
                         str(page.get("route") or ""),
                         str(page.get("page_type") or "")])
        score = len(page.get("functions") or []) * 4 + len(page.get("sections") or []) * 2
        if low_value.search(text):
            score -= 20
        if re.search(r"dashboard|console|terminal|board|detail|completion|manage|portal", text, re.I):
            score += 8
        return score

    def core_score(page):
        text = " ".join([str(page.get("name") or ""),
                         str(page.get("route") or "")]).lower()
        penalty = 14 if re.search(r"admin|report|settings|management", text) else 0
        return content_score(page) - penalty

    def role_score(page):
        text = " ".join([str(page.get("name") or ""),
                         str(page.get("route") or ""),
                         str(page.get("page_type") or "")]).lower()
        bonus = 14 if re.search(r"admin|dashboard|console|board|manage|report", text) else 0
        return content_score(page) + bonus

    public = [p for p in pages if p.get("access") == "public"
              and not auth_words.search(str(p.get("name") or ""))]
    first = next((p for p in public if p.get("route") == "/"), None)
    first = first or (max(public, key=content_score) if public else pages[0])

    remaining = [p for p in pages if p is not first]
    core = max(remaining, key=core_score)
    chosen = [first, core]

    role_pages = [p for p in remaining if p is not core and
                  (p.get("access") == "protected" or p.get("roles") or
                   re.search(r"admin|dashboard|console|portal|manage|detail",
                             " ".join([str(p.get("name") or ""),
                                       str(p.get("page_type") or "")]), re.I))]
    if role_pages:
        chosen.append(max(role_pages, key=role_score))
    for page in pages:
        if len(chosen) >= limit:
            break
        if page not in chosen:
            chosen.append(page)
    return chosen[:limit]


def sitemap_prompt(site_map: list[dict], preview_pages: list[dict]) -> str:
    """Render an exact page/slug contract that all directions must share."""
    if not site_map:
        return ""
    lines = [
        "CANONICAL SITE MAP — fixed for every visual direction; do not redesign it:",
    ]
    selected = {page.get("slug") for page in preview_pages}
    for page in site_map:
        roles = ", ".join(page.get("roles") or [])
        detail = f"{page.get('access', 'public')}" + (f"; roles: {roles}" if roles else "")
        marker = " [DRAW]" if page.get("slug") in selected else ""
        lines.append(f"- {page['name']} — {page['route']} — {detail}{marker}")
    lines += ["", "EXACT REPRESENTATIVE SCREENS TO DRAW, IN THIS ORDER:"]
    for index, page in enumerate(preview_pages, 1):
        lines.append(
            f"{index}. <section data-page=\"{page['slug']}\" "
            f"data-page-label=\"{page['name']}\"> for {page['route']}")
    lines += [
        "Use those exact data-page slugs and labels. Draw every [DRAW] page once, "
        "add no fourth page, and do not replace one with a style-guide/sample screen.",
        "Every [DRAW] slug must have a data-goto control in the real shell navigation. "
        "Do not put data-goto on undrawn sitemap pages; that would create a dead preview link.",
    ]
    return "\n".join(lines)


# The one call
SYSTEM = """\
You are the designer of a web application, and you draw its interactive design
preview before any production code is written. The customer approved a PLAN —
what the product does, who uses it, its screens, records and user journeys. Your
preview must make the chosen visual language believable across the APPLICATION,
not only on the landing page.

This is still a picture, not the real application. Write plain HTML + CSS in one
self-contained file. No React, Next.js, framework, fetch, timers, external URLs,
CDNs, web fonts or model-written JavaScript. AgentForge supplies the tiny page
switcher after your response, so you only mark screens and navigation.

WHAT YOU WRITE
- One complete <!DOCTYPE html> document. All CSS in one <style> in <head>.
- You receive a CANONICAL SITE MAP and an exact ordered [DRAW] subset. Every
  direction receives the same contract. Draw those screens exactly once with
  the exact data-page slugs/labels supplied. Do not choose a different sitemap,
  rename pages, add a style-guide screen, or substitute a prettier landing page
  for the core task/role screen.
- Each screen is a real <section data-page="short-slug"> inside one <main>.
  The first may be visible; later sections may start hidden. Use the SAME shell,
  spacing, typography, colours, controls, cards/tables and navigation language
  everywhere so the customer can judge cross-page coherence.
- Navigation controls that switch between preview screens use data-goto="slug".
  Put those controls in the actual nav/sidebar/header where they belong. Do not
  write a <script>; AgentForge injects the only script after generation.
- These are representative FULL screens, not three wireframes. The primary
  screen should be the richest. Secondary screens can be shorter, but must show
  their real forms/tables/cards/actions with plausible content and enough detail
  to prove the same design survives a different page type.
- Every visible action must correspond to something the approved plan says the
  real product can do. NEVER invent a permanently disabled placeholder action,
  “coming soon”, “not implemented”, TODO button, fake tab, or dead control. A
  disabled state is allowed only when it is a genuine transient/state rule the
  plan implies (for example submit while invalid), not as a substitute for a
  missing feature.
- Show useful states only when they are real product states: empty results,
  validation, selected filters, paid/pending status, loading, role context, etc.
  Do not sacrifice an actual screen just to display a decorative state sample.
- Where a photograph belongs, write only a marker such as:
    <img src="{{photo: one thing this app shows}}" alt="…">
  Use 3-6 plain words naming the subject. Never invent a URL or placeholder
  image service. Size/crop with CSS.
- System fonts only: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial,
  sans-serif; Georgia/serif; ui-monospace/Consolas/monospace.
- Use real-looking domain content — the rows, prices, dates, names, statuses —
  never Lorem ipsum, Item 1, ellipses or comments standing in for content.
- Responsive down to 360px with a media query.
- Name the design with:
    <meta name="design-name" content="two or three words">
    <meta name="design-blurb" content="one sentence on what it is like">

THE DESIGN IS YOURS
You receive a random starting direction — palette family, type voice, shape,
density and one signature move. Commit to it. Decide exact colours, type scale,
spacing, radius, borders/shadows and component shapes once, then keep them
consistent on every representative screen. The three screens should clearly
look like one product, not three independently generated templates.

CRAFT
The bar is work a design studio would put its name on, and the difference is
never a nicer adjective — it is these, each of which is checkable:

- ONE type scale, and use its ends. Something on the primary screen should be
  large enough to be the thing you see first, and the smallest label should be
  genuinely small. A page where everything is 14-16px reads as a form, not as a
  product. Set line-height per size: tight on display sizes, loose on body.
- ONE spacing rhythm, from a single unit. Generous outer padding, real air
  between sections. Cramped, evenly-spaced-everything is the single clearest
  tell of a generated page.
- COLOUR is mostly restraint. A ground, an ink, one accent, and the grey steps
  between them. The accent earns its place on the primary action and almost
  nowhere else. Never fill a section with a saturated block because it looks
  empty; empty is a legitimate design decision.
- DEPTH is either shadow or border, chosen once. Do not put a border and a
  shadow and a background tint on the same card. If you use shadows, they are
  soft, large and low-opacity — never the default browser drop-shadow.
- ALIGNMENT. Things line up on a shared edge. Text is left-aligned unless there
  is a reason; a page of centred paragraphs looks like a template.
- STATE is designed, not defaulted. Hover and focus-visible on every control,
  and a real empty state wherever a list can be empty — with the control that
  fills it, not the words "no data".
- CONTENT is plausible and specific. Real names, real prices, real dates, real
  copy for this product. "Lorem ipsum", "Item 1", "John Doe" and "$0.00" undo
  the design around them.
- ICONS are inline SVG you draw, and consistent in weight. Emoji are not icons.
- ACCESSIBILITY is part of the craft: body text at 4.5:1 against its ground,
  never grey-on-grey, and never colour alone to carry a status.

ULTRA-PROFESSIONAL FINISH
- Start from a real 12-column grid: consistent content width, shared column
  edges, an 8px-derived spacing rhythm and no accidental one-off offsets. The shell,
  page header and main content must align across every [DRAW] screen.
- Design the information architecture before decoration. One dominant focal
  point, one obvious primary action, clear section hierarchy, and secondary
  controls that recede. If five things compete for attention, the design fails.
- Avoid the generated-template tells: purple/blue gradient wash, glass cards,
  a giant mostly-empty hero, identical icon-card grids, every label in a pill,
  excessive rounded containers, decorative analytics, and a card around every
  section. Use any such device only when the assigned direction explicitly
  calls for it and the product content earns it.
- Use at most two surface elevations. Prefer alignment, whitespace and type
  hierarchy over containers. A dense product may be compact, but it must never
  be cramped; a premium product may be spacious, but it must never look empty.
- Forms, tables, filters, sidebars, statuses and action groups must be composed
  as designed systems—not browser defaults with padding. Include polished
  hover, active, selected, disabled-when-real and focus-visible treatments.
- Responsive is a redesign, not shrinkage: collapse the navigation, stack the
  correct columns, preserve action priority, and prevent horizontal overflow at
  360px. Use clamp() where it improves the type/space transition.
- Before returning, mentally inspect the first viewport and the longest [DRAW]
  page at 1440px and 390px. Fix clipping, weak contrast, orphan headings,
  inconsistent radius, uneven gaps, and controls without a clear affordance.

Read what you wrote as if it were a screenshot in a portfolio. If the honest
reaction is "this is a generated admin template", change the type scale and the
spacing first — those two carry most of the difference.

HOW MUCH
Spend most of the response on the primary screen, then enough on the other two
to demonstrate a coherent workflow and role/management surface. Do not pad with
repeated rows and do not stop after the hero. The failure is either a tiny
landing-page mockup that says nothing about the app, or many shallow screens
that say nothing about the design. Three substantial representative screens is
the balance.

Keep the complete HTML document under 24,000 characters. Prefer a few complete,
representative cards/rows over repeated markup, and always close the document.

Return ONLY the HTML. No commentary, no fences.\
"""


def brief_from_plan(plan: dict | None, doc: dict | None, idea: str = "") -> str:
    """A brief for the designer when there is no builder handoff yet."""
    plan = plan or {}
    doc = doc or {}
    lines = []
    intent = str(plan.get("product_intent") or idea or "").strip()
    if intent:
        lines += ["WHAT IT IS", intent[:1200], ""]
    users = [str(u.get("role") or "").strip() for u in (plan.get("users") or [])
             if isinstance(u, dict) and u.get("role")]
    if users:
        lines += ["WHO USES IT", ", ".join(users), ""]

    def page_lines(pages, guarded: bool):
        out = []
        for p in pages or []:
            if not isinstance(p, dict):
                continue
            name = str(p.get("page_name") or "").strip()
            if not name:
                continue
            head = f"- {name}"
            if p.get("route"):
                head += f"  ({p['route']})"
            roles = [str(r) for r in (p.get("allowed_roles") or []) if str(r).strip()]
            if guarded and roles:
                head += f"  — {', '.join(roles)} only"
            out.append(head)
            secs = [str(s) for s in (p.get("sections") or []) if str(s).strip()][:8]
            fns = [str(f) for f in (p.get("functions") or []) if str(f).strip()][:8]
            if secs:
                out.append("    on the page: " + "; ".join(secs))
            if fns:
                out.append("    what a person does here: " + "; ".join(fns))
        return out

    pub = page_lines(doc.get("public_pages"), False)
    prot = page_lines(doc.get("protected_pages"), True)
    if pub or prot:
        count = sum(1 for l in pub + prot if l.startswith("- "))
        lines.append(f"PAGES ({count})")
        if pub:
            lines += ["open to everyone:"] + pub
        if prot:
            lines += ["sign-in required:"] + prot
        lines.append("")
    else:
        screens = [str(s.get("name") or "").strip() for s in (plan.get("screens") or [])
                   if isinstance(s, dict) and s.get("name")]
        if screens:
            lines += [f"PAGES ({len(screens)})"] + [f"- {s}" for s in screens] + [""]
    for flow in (plan.get("workflows") or [])[:6]:
        if not isinstance(flow, dict):
            continue
        title = str(flow.get("title") or flow.get("name") or "").strip()
        steps = [str(s) for s in (flow.get("steps") or []) if str(s).strip()]
        if title and steps:
            lines += [f"JOURNEY — {title}", " → ".join(steps[:8]), ""]
    if not lines:
        lines = ["WHAT IT IS", (idea or "a web application")[:1200], "",
                 "There is no page list yet: decide the pages this application "
                 "needs — the one a visitor lands on, the pages where the work "
                 "happens, the page at the end of its main journey — and draw "
                 "them all."]
    return "\n".join(lines).strip()


def user_prompt(brief: str, app_name: str, direction: dict, index: int = 1,
                total: int = COUNT) -> str:
    """The one turn: the plan, this direction, the whole app."""
    site_map = direction.get("site_map") or []
    preview_pages = direction.get("preview_pages") or representative_pages(site_map)
    contract = sitemap_prompt(site_map, preview_pages)
    return "\n".join([
        f"APP: {app_name or 'the application'}",
        "",
        "THE PLAN — this is what will be built; draw all of it:",
        "─" * 60,
        (brief or "").strip()[:9000],
        "─" * 60,
        "",
        contract,
        "" if contract else "",
        f"YOUR STARTING DIRECTION (design {index} of {total}, seed {direction.get('seed', '')}):",
        direction_text(direction),
        "",
        "Now write the document — one premium shell and one exact visual system "
        "across the fixed representative screens. Preserve the canonical sitemap; "
        "only the art direction changes between the five designs.",
    ])


# What comes back
_SWITCH = """
<script>
(function(){
  var pages=[].slice.call(document.querySelectorAll('[data-page]'));
  if(!pages.length)return;
  function slugOf(p){return (p.getAttribute('data-page')||'').toLowerCase();}
  function pick(slug){
    slug=(slug||'').toLowerCase().replace(/^#/,'');
    var exact=pages.filter(function(p){return slugOf(p)===slug;});
    if(exact.length)return exact[0];
    // "admin" for "admin-books", "items" for "my-items": nearest name wins
    var near=pages.filter(function(p){var s=slugOf(p);return s.indexOf(slug)===0||slug.indexOf(s)===0||s.indexOf(slug)>=0;});
    return near.length?near[0]:pages[0];
  }
  function show(target){
    pages.forEach(function(p){p.style.display=(p===target)?'block':'none';});
    window.scrollTo(0,0);
  }
  document.addEventListener('click',function(e){
    var t=e.target.closest('[data-goto]');if(!t)return;
    e.preventDefault();show(pick(t.getAttribute('data-goto')));
  });
  var visible=pages.filter(function(p){return getComputedStyle(p).display!=='none';});
  show(visible.length===1?visible[0]:pages[0]);
})();
</script>
"""

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-") or "page"


_SECTION_TAG_RE = re.compile(r"<section\b[^>]*\bdata-page\s*=\s*['\"][^'\"]+['\"][^>]*>",
                             re.I)


def _attr_value(tag: str, name: str) -> str:
    match = re.search(r"\b" + re.escape(name) + r"\s*=\s*(['\"])(.*?)\1",
                      tag or "", re.I | re.S)
    return _html.unescape(match.group(2)).strip() if match else ""


def align_preview_sitemap(html: str, preview_pages: list[dict]) -> str:
    """Normalize model-chosen page slugs to the one canonical preview sitemap."""
    expected = [page for page in (preview_pages or []) if page.get("slug")]
    tags = list(_SECTION_TAG_RE.finditer(html or ""))
    if not expected or len(tags) != len(expected):
        return html

    mapping = {}
    cursor = 0
    parts = []
    for index, match in enumerate(tags):
        parts.append(html[cursor:match.start()])
        tag = match.group(0)
        old = _attr_value(tag, "data-page")
        page = expected[index]
        mapping[old.casefold()] = str(page["slug"])
        tag = re.sub(r"\bdata-page\s*=\s*(['\"])(.*?)\1",
                     f'data-page="{page["slug"]}"', tag, count=1,
                     flags=re.I | re.S)
        tag = re.sub(r"\s+data-page-label\s*=\s*(['\"])(.*?)\1", "",
                     tag, flags=re.I | re.S)
        tag = tag[:-1] + f' data-page-label="{_html.escape(str(page["name"]), quote=True)}">'
        parts.append(tag)
        cursor = match.end()
    parts.append(html[cursor:])
    aligned = "".join(parts)

    def goto(match):
        old = _html.unescape(match.group(2)).strip()
        target = mapping.get(old.casefold(), old)
        return f'data-goto="{_html.escape(target, quote=True)}"'

    return re.sub(r"\bdata-goto\s*=\s*(['\"])(.*?)\1", goto, aligned,
                  flags=re.I | re.S)


def sitemap_issue(html: str, preview_pages: list[dict]) -> str:
    """A structural reason a design cannot be shown as a complete direction."""
    expected = [str(page.get("slug") or "") for page in (preview_pages or [])
                if page.get("slug")]
    if not expected:
        return ""
    actual = [_attr_value(tag, "data-page") for tag in
              _SECTION_TAG_RE.findall(html or "")]
    if actual != expected:
        return (f"expected {len(expected)} canonical screens "
                f"({', '.join(expected)}), got {len(actual)} "
                f"({', '.join(actual) or 'none'})")
    gotos = {_html.unescape(value).strip() for _quote, value in re.findall(
        r"\bdata-goto\s*=\s*(['\"])(.*?)\1", html or "", re.I | re.S)}
    missing = [slug for slug in expected if slug not in gotos]
    unknown = sorted(target for target in gotos if target not in set(expected))
    if missing:
        return "canonical preview navigation is missing: " + ", ".join(missing)
    if unknown:
        return "preview navigation points to undrawn pages: " + ", ".join(unknown[:5])
    return ""


def finish(raw: str) -> str:
    """Make what the model returned render, whatever state it stopped in."""
    t = (raw or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
    if t.endswith("```"):
        t = t.rsplit("```", 1)[0]
    t = t.strip()
    if "<" not in t:
        return ""
    if "<html" not in t.lower():
        t = "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"></head><body>\n" + t
    low = t.lower()
    # Cut off inside a tag: drop the dangling fragment
    if t.rfind("<") > t.rfind(">"):
        t = t[:t.rfind("<")]
    t = _SCRIPT_RE.sub("", t)
    low = t.lower()
    if "</body>" not in low:
        # Close whatever is open, coarsely — browsers forgive the rest
        opened = ["</section>"] if "<section" in low and low.count("<section") > low.count("</section>") else []
        t += "\n" + "".join(opened) + ("\n</main>" if "<main" in low and "</main>" not in low else "")
        t += _SWITCH + "\n</body></html>"
    else:
        t = t.replace("</body>", _SWITCH + "\n</body>", 1)
    return t


def meta_of(html: str) -> tuple[str, str]:
    """The name and blurb the model gave its design, from the <meta> tags."""
    def get(name: str) -> str:
        m = re.search(
            r"<meta[^>]+name=[\"']" + name + r"[\"'][^>]*content=[\"']([^\"']*)[\"']",
            html or "", re.I)
        if not m:
            m = re.search(
                r"<meta[^>]+content=[\"']([^\"']*)[\"'][^>]*name=[\"']" + name + r"[\"']",
                html or "", re.I)
        return _html.unescape(m.group(1)).strip() if m else ""
    return get("design-name"), get("design-blurb")


def pages_of(html: str) -> list[str]:
    """The pages a design contains, in document order, as readable names."""
    section_rows = [(_attr_value(tag, "data-page"),
                     _attr_value(tag, "data-page-label"))
                    for tag in _SECTION_TAG_RE.findall(html or "")]
    slugs = [slug for slug, _label in section_rows]
    section_labels = {slug: label for slug, label in section_rows if label}
    labels = {}
    navs = re.findall(r"<nav\b[^>]*>(.*?)</nav>", html or "", re.I | re.S)
    for nav in navs:
        for tag, slug, inner in re.findall(
                r"<(a|button)\b[^>]*data-goto=[\"']([^\"']+)[\"'][^>]*>(.*?)</\1>",
                nav, re.I | re.S):
            text = " ".join(re.sub(r"<[^>]+>", " ", inner).split())
            text = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", text)  # "Inbox (2)" → "Inbox"
            if re.search(r"\b(sign|log)\s*out\b|\blogout\b|\bsign\s*in\b|\blogin\b", text, re.I):
                continue
            if text and slug not in labels and len(text) <= 28:
                labels[slug] = text
    out, seen = [], set()
    for s in slugs:
        if s in seen:
            continue
        seen.add(s)
        out.append(section_labels.get(s) or labels.get(s) or
                   s.replace("-", " ").strip().title() or "Page")
    return out


# From the approved design to design.md
DESIGN_SYSTEM = """\
You are reading the demo of a web application that the customer has looked at \
and approved — one document, several pages, one shell. You write the design \
rules the real application will be built to, so that every screen looks like it \
belongs beside what they approved.

Read the actual CSS and the controller-extracted theme receipt. The receipt is deterministic evidence from the approved HTML; never contradict or replace its values with a generic design preference. Do not describe the pages in adjectives — take the real values out of them and state them as rules:

- The exact colours, as hex. Say which is the page background, which is a \
raised surface, which is body text, which is muted text, which is the border, \
and which is the accent. If there is more than one accent, say what each is for.
- The exact radius scale actually present in the CSS and which recurring pieces use each value. Do not collapse multiple real radii into one.
- The type scale: the size and weight actually used for the h1, for section \
headings, and for body text, with line heights.
- The spacing rhythm: the gap between sections and the gap inside them.
- Borders and shadows: whether the design separates things with rules, with \
shadows, with spacing, or with fills — and which one, because mixing them is \
what makes a build look assembled by different people.
- The shape of the recurring pieces: what a button looks like, what a card or \
a row looks like, what a form field looks like, and what the navigation bar \
looks like — its height, its background, its border, how its links are set.
- The content width: the max-width the demo holds its columns to.

WHAT THIS DOCUMENT IS NOT. It describes the LOOK and nothing else. It never \
says which pages the app has, what goes on them, where a link leads, what the \
navigation links TO, who may see what, or what happens when something is \
clicked. Those are the plan's, the plan has already decided them, and this \
document is handed to the builder as rules it must follow — so a sentence here \
about structure is a second plan arguing with the first. Say what a nav bar \
looks like; never say what is in it. Say what a card looks like; never say \
what the cards are of.

Write it as Markdown: a one-line summary of the design's character, then short \
`- ` rules under those headings. Every rule must be checkable by looking at one \
screen — a builder should be able to point at it and say whether it complies. \
No preamble, no fences, no commentary. Under 400 words.\
"""


def design_prompt(html: str, page_name: str = "") -> str:
    """Ask for the design rules of the demo the customer approved."""
    del page_name
    name, blurb = meta_of(html or "")
    head = []
    if name:
        head.append(f"The customer chose the design called '{name}'"
                    + (f" ({blurb})" if blurb else "") + " out of five.")
    head.append("Take the design language out of it — the look, not what the "
                "demo happens to be a demo of.")
    facts = theme_facts_markdown(html or "")
    if facts:
        head += [
            "",
            "CONTROLLER-EXTRACTED CSS FACTS — these are authoritative:",
            facts,
            "",
            "Translate these facts into concise design rules. Never substitute a generic palette, type scale, radius, spacing scale, shadow strategy, or content width.",
        ]
    style = "\n".join(re.findall(r"<style\b[^>]*>.*?</style>", html or "", re.I | re.S))
    body = re.sub(r"<style\b[^>]*>.*?</style>", "", html or "", flags=re.I | re.S)
    excerpt = (style[:22000] + "\n" + body[:9000])[:31000]
    return "\n".join(head) + "\n\n```html\n" + excerpt + "\n```\n"
