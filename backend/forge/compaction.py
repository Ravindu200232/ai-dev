"""Keeping a long run inside its window: trim, dedupe, then summarise."""
from __future__ import annotations

import re

from .tokens import clip, head_lines, message_tokens, total_tokens

MIN_MESSAGE_TOKENS = 40   # below this a message says nothing worth keeping

KEEP_RECENT = 6           # turns that always stay whole
OLD_TOOL_TOKENS = 120     # what an older tool result is cut down to
SUMMARY_TOKENS = 220      # how much a rolling summary may cost

_WROTE_RE = re.compile(r"^(created|replaced|edited|deleted) (\S+)", re.M)
_RAN_RE = re.compile(r"^\$ (.+)\n\[exit (\d+)\]", re.M)
_ERROR_RE = re.compile(r"^.*(error|failed|refused|cannot find|not allowed).*$",
                       re.I | re.M)


def shrink_tool_output(turns, keep_recent: int = KEEP_RECENT,
                       limit: int = OLD_TOOL_TOKENS):
    """Older tool results keep their opening lines. Recent ones stay whole."""
    edge = max(0, len(turns) - keep_recent)
    out, changed = [], 0
    for index, message in enumerate(turns):
        if (index < edge and message.get("role") == "tool"
                and message_tokens(message) > limit):
            message = dict(message)
            message["content"] = clip(head_lines(message["content"], 12), limit)
            changed += 1
        out.append(message)
    return out, changed


def drop_repeats(turns):
    """A tool asked the same thing twice only needs to be read once."""
    seen, out, changed = {}, list(turns), 0
    for index, message in enumerate(turns):
        if message.get("role") != "tool":
            continue
        key = (message.get("name"), message.get("content"))
        if key in seen:
            older = seen[key]
            out[older] = dict(out[older])
            # Keep the first line: it usually carries the fact worth keeping.
            opening = str(out[older].get("content") or "").splitlines()[:1]
            out[older]["content"] = "\n".join(opening + [
                f"(identical to the later {message.get('name')} result below)"])
            changed += 1
        seen[key] = index
    return out, changed


def safe_tail(turns, keep_recent: int = KEEP_RECENT) -> int:
    """Where the untouched tail may start without orphaning a tool result."""
    start = max(0, len(turns) - keep_recent)
    while start > 0 and turns[start].get("role") == "tool":
        start -= 1
    return start


def fallback_summary(turns) -> str:
    """A summary built from the transcript itself, with no model call."""
    blob = "\n".join(str(m.get("content") or "") for m in turns)
    wrote = [f"{verb} {path}" for verb, path in _WROTE_RE.findall(blob)]
    ran = [f"`{cmd.strip()}` → exit {code}" for cmd, code in _RAN_RE.findall(blob)]
    errors = [line.strip()[:140] for line in _ERROR_RE.findall(blob)][:4]
    said = [str(m.get("content") or "").strip() for m in turns
            if m.get("role") == "assistant" and m.get("content")]
    parts = []
    if wrote:
        parts.append("Files touched: " + ", ".join(dict.fromkeys(wrote))[:600])
    if ran:
        parts.append("Commands: " + "; ".join(dict.fromkeys(ran))[:400])
    if errors:
        parts.append("Problems seen: " + " | ".join(errors))
    if said:
        parts.append("Last conclusion: " + clip(said[-1], 60))
    return "\n".join(parts)


def summarise_middle(convo, summarise=None, keep_recent: int = KEEP_RECENT) -> bool:
    """Fold everything but the recent tail into a pinned summary."""
    start = safe_tail(convo.turns, keep_recent)
    middle, tail = convo.turns[:start], convo.turns[start:]
    if not middle:
        return False
    try:
        text = summarise(middle) if summarise else fallback_summary(middle)
    except Exception:                                              # noqa: BLE001
        text = fallback_summary(middle)
    convo.note(clip(text, SUMMARY_TOKENS))
    convo.turns = tail
    return True


def hard_clip(convo) -> int:
    """Last resort: halve the biggest message until the window fits.

    One tool result can be larger than the whole budget, and neither the
    summary nor dropping older turns can help with that — so cut the message
    itself rather than throwing away turns that still matter.
    """
    cuts = 0
    while convo.tokens() > convo.budget and convo.turns and cuts < 60:
        index = max(range(len(convo.turns)),
                    key=lambda i: message_tokens(convo.turns[i]))
        message = dict(convo.turns[index])
        before = message_tokens(message)
        target = max(MIN_MESSAGE_TOKENS, before // 2)
        message["content"] = clip(str(message.get("content") or ""), target)
        if message_tokens(message) >= before:
            break                      # all that is left is the tool_call payload
        convo.turns[index] = message
        cuts += 1
    return cuts


def compact(convo, summarise=None, keep_recent: int = KEEP_RECENT) -> dict:
    """Bring a conversation back under budget, cheapest step first.

    Truncating tool output and dropping repeats cost nothing, so they run
    first; the summary — which loses detail, and may cost a model call — runs
    only when the cheap steps were not enough.
    """
    before = convo.tokens()
    report = {"before": before, "shrunk": 0, "deduped": 0, "summarised": False}
    if before <= convo.budget:
        report["after"] = before
        return report

    convo.turns, report["deduped"] = drop_repeats(convo.turns)
    convo.turns, report["shrunk"] = shrink_tool_output(convo.turns, keep_recent)

    if convo.tokens() > convo.budget:
        report["summarised"] = summarise_middle(convo, summarise, keep_recent)

    if convo.tokens() > convo.budget:
        report["clipped"] = hard_clip(convo)

    # Whatever is left is bigger than the budget only because the pinned head
    # is. Say so rather than dropping turns that cannot help.
    if convo.tokens() > convo.budget and convo.pinned_tokens() >= convo.budget:
        report["over_pinned"] = True

    # Otherwise fold the oldest turns into the summary rather than dropping
    # them — silent loss is how an agent forgets what it already tried.
    while (convo.tokens() > convo.budget and len(convo.turns) > 1
           and not report.get("over_pinned")):
        cut = convo.turns[:safe_tail(convo.turns, len(convo.turns) - 1) or 1]
        convo.turns = convo.turns[len(cut):]
        convo.note(clip(fallback_summary(cut), SUMMARY_TOKENS // 2))
        report["dropped"] = report.get("dropped", 0) + len(cut)

    report["after"] = convo.tokens()
    report["saved"] = before - report["after"]
    return report


def summariser(model_call, prompt="Summarise this build transcript in under "
                                 "120 words: what was built, what failed, and "
                                 "what is still open."):
    """Wrap a model call so it can be handed to `compact` as `summarise`."""
    def run(turns) -> str:
        transcript = clip("\n".join(
            f"{m.get('role')}: {str(m.get('content') or '')[:600]}"
            for m in turns), 2_000)
        return str(model_call(prompt, transcript) or "").strip()
    return run


__all__ = ["KEEP_RECENT", "compact", "drop_repeats", "fallback_summary",
           "hard_clip", "safe_tail", "shrink_tool_output", "summarise_middle",
           "summariser", "total_tokens"]
