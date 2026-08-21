"""Five designs of the whole application, before a line of it is written."""
from __future__ import annotations

import html as _html
import random
import re

from .theme_contract import theme_facts_markdown

COUNT = 5

# Random directions Wide pools
_PALETTES = [
    "warm paper and ink — off-white ground, near-black text, one hot accent",
    "cool greys with a single electric blue",
    "deep navy surfaces with pale text, one amber accent",
    "cream and terracotta, earthy and warm",
    "near-black ground, white text, one acid-green accent",
    "sage and forest greens on bone white",
    "plum and dusty rose, low saturation",
    "slate blue-greys with a coral accent",
    "pure white, hairline greys, one saturated red used sparingly",
    "sand, ochre and charcoal — desert tones",
    "midnight and teal, glowing accents on dark",
    "soft pastels — mint, butter, sky — on white, with charcoal text",
    "monochrome black and white with ONE colour used as a field",
    "olive, mustard and cream, retro and warm",
    "ice white and cobalt, clinical and precise",
    "chocolate browns and peach on cream",
]
_TYPE = [
    "a heavy grotesque sans for headings, set tight; small plain body",
    "a serif for headings (Georgia), sans body — editorial",
    "everything in one sans at few sizes; weight and colour do the work",
    "large light-weight display headings, generous body leading",
    "condensed uppercase headings with wide tracking; roomy body",
    "monospace labels and numbers, sans everything else — instrument panel",
    "big friendly rounded-feeling sans, bold, high x-height",
    "small type throughout, dense, tabular — built for daily use",
    "serif body text (Georgia) with sans UI labels — bookish",
    "oversized numerals and headings, tiny captions — poster-like contrast",
]
_SHAPE = [
    "square corners everywhere, no radius at all",
    "small 4px radius, restrained",
    "generous 12-16px radius on cards and fields, pill buttons",
    "fully round pills and circles for every interactive thing",
    "square outer containers, rounded inner controls",
    "hard edges with thick 2px borders as the main separator",
    "no borders — separation by background fill and spacing only",
    "hairline 1px rules between everything, table-like",
]
_DENSITY = [
    "roomy — lots of white space, few things per screen",
    "normal — comfortable, balanced",
    "tight — dense rows, small padding, many things per screen",
    "very roomy — one idea per viewport, big margins",
    "dense data with roomy headers — magazine header, spreadsheet body",
]
_MOVES = [
    "a full-bleed hero band in the accent colour",
    "a left sidebar navigation that stays put",
    "a top bar with the page title in enormous type",
    "cards with an offset hard shadow (no blur)",
    "an accent-coloured left rule on every active or important row",
    "numbered sections, big numerals in the margin",
    "a sticky footer bar carrying the primary action",
    "generous photography — a large image at the top of every content page",
    "iconless — words and rules only, no icons anywhere",
    "chips and pills for every status and filter",
    "two-column layout: a narrow index on the left, content on the right",
    "underlined links and underlined active tabs, nothing filled",
    "a coloured background field behind the whole page, white cards on it",
    "big rounded avatars and photography as the primary visual",
    "a dark shell (header, nav) over a light content area",
    "boxed, centred content column no wider than 900px, like a document",
]


def random_directions(n: int = COUNT, rng: random.Random | None = None) -> list[dict]:
    """`n` random starting directions, no two sharing a palette or type voice."""
    rng = rng or random.Random()
    n = max(1, min(n, len(_PALETTES), len(_TYPE)))
    pals = rng.sample(_PALETTES, n)
    types = rng.sample(_TYPE, n)
    shapes = rng.sample(_SHAPE, min(n, len(_SHAPE)))
    moves = rng.sample(_MOVES, min(n, len(_MOVES)))
    out = []
    for i in range(n):
        out.append({
            "index": i + 1,
            "seed": rng.randrange(1000, 9999),
            "palette": pals[i],
            "type": types[i],
            "shape": shapes[i % len(shapes)],
            "density": rng.choice(_DENSITY),
            "move": moves[i % len(moves)],
        })
    return out


def direction_text(d: dict) -> str:
    return "\n".join([
        f"- palette: {d['palette']}",
        f"- type: {d['type']}",
        f"- shape: {d['shape']}",
        f"- density: {d['density']}",
        f"- one signature move: {d['move']}",
    ])


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
- If the plan has 3 or more screens, draw EXACTLY THREE representative screens
  in the same design system. If it has only 1-2 real screens, draw all of them.
  Choose screens that together prove the design works for the whole product:
    1) the primary/public or landing/list screen,
    2) the main task screen where the core workflow actually happens, and
    3) a role-specific management/dashboard/detail/completion screen when the
       product has one.
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
    return "\n".join([
        f"APP: {app_name or 'the application'}",
        "",
        "THE PLAN — this is what will be built; draw all of it:",
        "─" * 60,
        (brief or "").strip()[:9000],
        "─" * 60,
        "",
        f"YOUR STARTING DIRECTION (design {index} of {total}, seed {direction.get('seed', '')}):",
        direction_text(direction),
        "",
        "Now write the document — one shell, one visual system, and up to three "
        "substantial representative screens that cover the primary surface, the "
        "core workflow, and the management/detail side of the approved plan.",
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
    slugs = re.findall(r"<section[^>]*data-page=[\"']([^\"']+)[\"']", html or "", re.I)
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
        out.append(labels.get(s) or s.replace("-", " ").strip().title() or "Page")
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
