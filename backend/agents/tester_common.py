# !/usr/bin/env python3
"""Tester Agent — Python Playwright API only."""
import subprocess, sys, time, logging, re
from pathlib import Path
from urllib.parse import urlparse

from . import nextmcp

log = logging.getLogger("tester")
_emit = None

def set_emit(fn):
    global _emit
    _emit = fn

def elog(lvl, txt):
    if _emit:
        _emit({"type": "log", "level": lvl, "text": txt})
    log.info(f"[{lvl}] {txt}")

def etest(status, msg, detail=""):
    """Emit a structured test result event to the UI."""
    if _emit:
        _emit({"type": "test_result", "status": status, "msg": msg, "detail": detail})


_VITE_NOISE = [
    "favicon", "Warning:", "DevTools", "Download the React",
    "ReactDOM.render", "StrictMode", "[HMR]", "[vite]", "vite",
    "hot update", "connecting", "react-refresh",
    "net::ERR_", "Failed to load resource",
    "Cross-Origin", "Content-Security-Policy",
]
_VITE_SIGNALS = [
    "is not defined", "is not a function",
    "Cannot read prop", "Cannot read properties",
    "SyntaxError", "ReferenceError", "TypeError",
    "Failed to resolve import", "does not provide an export",
]

STACKS = {
    "vite": {
        "label": "Vite",
        "ready_timeout": 30, "req_timeout": 5, "poll": 1.5,
        "goto_timeout": 30000, "mount_timeout": 8000,
        "mount_selectors": ["#root > *", "#app > *", "canvas", "svg", "main"],
        "overlay_tag": "vite-error-overlay",
        "noise": _VITE_NOISE,
        "signals": _VITE_SIGNALS,
    },
    "next": {
        "label": "Next.js",

        "ready_timeout": 180, "req_timeout": 90, "poll": 2.0,
        "goto_timeout": 120000, "mount_timeout": 20000,

        "mount_selectors": ["main", "header", "nav", "section", "table",
                            "body > div"],
        "overlay_tag": "nextjs-portal",
        "noise": [
            "favicon", "Warning:", "DevTools", "Download the React",
            "[Fast Refresh]", "Fast Refresh", "react-refresh", "hot-reloader",
            "webpack-hmr", "next-dev", "<Suspense>", "Image with src",
            "net::ERR_", "Failed to load resource",
            "Cross-Origin", "Content-Security-Policy",
        ],
        "signals": [
            "is not defined", "is not a function",
            "Cannot read prop", "Cannot read properties",
            "SyntaxError", "ReferenceError", "TypeError",

            "Module not found", "Can't resolve", "is not exported from",
            "only works in a Client Component", "Hydration failed",
            "Text content does not match", "Only plain objects",
            "Unhandled Runtime Error",
            "MongoServerSelectionError", "MongoNetworkError", "ECONNREFUSED",
        ],
    },
}


OVERLAY_SIGNAL_RE = (r"Build Error|Runtime Error|Console Error|"
                     r"Failed to compile|Unhandled|Module not found|"
                     r"Only plain objects|Functions cannot be passed|"
                     r"Event handlers cannot be passed|"
                     r"cannot be passed directly|Error:")

_OVERLAY_JS = """(re) => {
    const rx = new RegExp(re, 'i');
    for (const p of document.querySelectorAll('nextjs-portal')) {
        const r = p.shadowRoot; if (!r) continue;
        const dlg = r.querySelector('[data-nextjs-dialog],'
            + '#nextjs__container_errors_desc,'
            + '[data-nextjs-terminal],[data-nextjs-dialog-body]');
        if (!dlg) continue;
        const t = (dlg.innerText || dlg.textContent || '').trim();
        if (!rx.test(t)) continue;
        return t.slice(0, 800);
    }
    return '';
}"""

_VITE_OVERLAY_JS = """() => {
    const ov = document.querySelector('vite-error-overlay');
    if (ov && ov.shadowRoot) {
        const el = ov.shadowRoot.querySelector('.message-body,.message,pre,.err-message');
        return el ? el.textContent.trim().slice(0,600)
                  : ov.shadowRoot.textContent.trim().slice(0,600);
    }
    return '';
}"""


def overlay_error(page, stack: str = "next") -> str:
    """The dev server's error dialog for whatever page is now open."""
    try:
        if stack == "next":
            return page.evaluate(_OVERLAY_JS, OVERLAY_SIGNAL_RE) or ""
        return page.evaluate(_VITE_OVERLAY_JS) or ""
    except Exception:
        return ""


class TesterAgentBase:
    MAX_EXTRA_ROUTES = 20

    def __init__(self, project_dir: Path, port: int = 5173,
                 stack: str = "vite", smoke_only: bool = False):
        self.project_dir = project_dir
        self.port        = port
        self.base_url    = f"http://localhost:{port}"
        self.stack       = stack if stack in STACKS else "vite"
        self.cfg         = STACKS[self.stack]
        self.smoke_only  = bool(smoke_only)

        self._mcp_parts  = []
        self._runtime_dynamic_links = []

    def test(self) -> list:
        errors = []
        label = self.cfg["label"]

        elog("INFO", f"⏳ Waiting for {label} at {self.base_url}...")
        ok, err = self._wait_for_server(timeout=self.cfg["ready_timeout"])
        if not ok:
            elog("WARN", f"❌ {label} not reachable: {err}")
            etest("fail", "HTTP check", f"Server not reachable: {err}")
            errors.append(f"HTTP check failed: {err}")
            return errors
        elog("INFO", f"✅ HTTP 200 — {label} is serving")
        etest("pass", "HTTP 200 OK")

        if not self._ensure_playwright():
            elog("WARN", "⚠ Playwright unavailable — skipping browser tests")
            etest("skip", "Playwright unavailable")
            return []

        errors.extend(self._run_browser_tests())
        return errors

    def _wait_for_server(self, timeout=30):
        import urllib.request, urllib.error
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = urllib.request.urlopen(
                    self.base_url, timeout=self.cfg["req_timeout"])
                if resp.status == 200:
                    return True, None
            except urllib.error.HTTPError:

                return True, None
            except Exception:
                pass
            time.sleep(self.cfg["poll"])
        return False, f"timeout after {timeout}s"

    def _body_text(self, page) -> str:
        try:
            return page.inner_text("body")[:400]
        except Exception:
            return ""

    def _overlay_error(self, page) -> str:
        """The dev server's error dialog for the page now open."""
        return overlay_error(page, self.stack)


__all__ = [name for name in globals() if not name.startswith("__")]
