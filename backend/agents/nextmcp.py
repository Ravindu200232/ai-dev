"""The Next.js dev server's own MCP endpoint, read for the fix prompt."""
import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("nextmcp")

TIMEOUT = 15


_HEADERS = {"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}


def call(base_url: str, tool: str, arguments: dict = None) -> dict:
    """Invoke one MCP tool. Returns {} on any failure — never raises."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    }).encode()
    try:
        req = urllib.request.Request(
            base_url.rstrip("/") + "/_next/mcp", data=payload, headers=_HEADERS)
        body = urllib.request.urlopen(req, timeout=TIMEOUT) \
                             .read().decode("utf-8", "replace")
    except Exception as e:
        log.debug(f"mcp {tool}: {e}")
        return {}

    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            line = line[6:]
        if not line.startswith("{"):
            continue
        try:
            result = json.loads(line).get("result", {})
        except json.JSONDecodeError:
            continue
        text = " ".join(c.get("text", "") for c in result.get("content", []))
        if not text.strip():
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return {}


def available(base_url: str) -> bool:
    """True when this dev server serves an MCP endpoint at all."""
    return bool(call(base_url, "get_project_metadata"))


def _frame(fr: dict) -> str:
    f = str(fr.get("file", "")).replace("\\", "/")
    where = f"{f}:{fr.get('line', '?')}:{fr.get('column', '?')}"
    fn = fr.get("methodName")
    return f"    at {fn} ({where})" if fn else f"    at {where}"


def errors(base_url: str, limit: int = 8) -> str:
    """Next's own error state, formatted for a fix prompt."""
    d = call(base_url, "get_errors")
    if not d or "error" in d and len(d) == 1:

        return ""

    out = []
    for c in (d.get("configErrors") or [])[:2]:
        msg = str(c.get("message", "")).strip()
        if msg:
            out.append(f"next.config: {msg}")

    for s in (d.get("sessionErrors") or []):
        url = s.get("url", "?")
        build = s.get("buildError")
        if build:
            msg = build if isinstance(build, str) else build.get("message", "")
            out.append(f"{url} — build error: {str(msg).strip()[:600]}")
        for e in (s.get("runtimeErrors") or []):
            name = e.get("errorName") or e.get("type") or "Error"
            head = f"{url} — {name}: {str(e.get('message', '')).strip()[:300]}"
            frames = [_frame(f) for f in (e.get("stack") or [])[:4]

                      if "node_modules" not in str(f.get("file", ""))]
            out.append("\n".join([head] + frames))
        if len(out) >= limit:
            break

    if not out:
        return ""
    return ("## What Next.js itself reports is broken\n"
            "Source-mapped, straight from the dev server — the file and line "
            "are exact.\n\n```\n" + "\n".join(out[:limit]) + "\n```")
