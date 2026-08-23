"""Counting context without a tokenizer, and cutting text down to a budget."""
from __future__ import annotations

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD = 4      # role, delimiters and the model's own framing


def estimate(text) -> int:
    """Tokens in a string, near enough to budget with."""
    return max(1, len(str(text or "")) // CHARS_PER_TOKEN)


def message_tokens(message: dict) -> int:
    """One message, including its tool-call payload."""
    total = estimate(message.get("content") or "") + MESSAGE_OVERHEAD
    for call in message.get("tool_calls") or []:
        function = (call or {}).get("function") or {}
        total += estimate(function.get("name")) + estimate(function.get("arguments"))
    return total


def total_tokens(messages) -> int:
    """The whole conversation."""
    return sum(message_tokens(m) for m in messages or [])


def clip(text, max_tokens: int, *, tail: bool = False) -> str:
    """Trim to a token budget and say how much went. Keeps the end if asked."""
    body = str(text or "")
    limit = max(1, int(max_tokens)) * CHARS_PER_TOKEN
    if len(body) <= limit:
        return body
    cut = len(body) - limit
    if tail:
        return f"… {cut} characters cut …\n" + body[-limit:]
    return body[:limit] + f"\n… {cut} characters cut"


def head_lines(text, count: int) -> str:
    """The first `count` lines, with a note about the rest."""
    lines = str(text or "").splitlines()
    if len(lines) <= count:
        return "\n".join(lines)
    return "\n".join(lines[:count]) + f"\n… {len(lines) - count} more lines"
