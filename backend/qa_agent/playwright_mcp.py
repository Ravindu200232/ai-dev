"""Small, fail-safe client for the official Playwright MCP sidecar.

The generated application keeps stable ``@playwright/test``.  MCP runs through
``npx`` in its own process because the current MCP package may depend on a
different Playwright release.  The sidecar is evidence-only: the deterministic
Python runner remains responsible for executing and judging E2E scenarios.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse


log = logging.getLogger("qa.playwright_mcp")

PLAYWRIGHT_MCP_PACKAGE = "@playwright/mcp@0.0.79"
MCP_PROTOCOL_VERSION = "2025-06-18"
_PACKAGE_RE = re.compile(r"^@playwright/mcp@(?:latest|\d+(?:\.\d+){2})$")
_ENABLED = {"1", "true", "yes", "on", "auto"}


class PlaywrightMCPError(RuntimeError):
    """An optional sidecar could not provide evidence."""


def playwright_mcp_enabled() -> bool:
    """MCP is on by default, with a single environment switch for CI/offline use."""
    return os.getenv("AGENTFORGE_PLAYWRIGHT_MCP", "auto").strip().lower() in _ENABLED


def _npx_prefix(package: str) -> list[str]:
    """Return an argv-only npx command, including on Windows (no shell)."""
    if not _PACKAGE_RE.fullmatch(str(package or "")):
        return []
    node = shutil.which("node") or shutil.which("node.exe")
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not node or not npx:
        return []

    # A .cmd file cannot be started directly by CreateProcess.  Invoke npm's
    # JavaScript entry point with node so paths and arguments never pass through
    # cmd.exe string parsing.
    if os.name == "nt" or str(npx).lower().endswith((".cmd", ".bat")):
        candidates = (
            Path(npx).resolve().parent / "node_modules" / "npm" / "bin" / "npx-cli.js",
            Path(node).resolve().parent / "node_modules" / "npm" / "bin" / "npx-cli.js",
        )
        cli = next((p for p in candidates if p.is_file()), None)
        if cli is None:
            return []
        return [str(node), str(cli), "--yes", package]
    return [str(npx), "--yes", package]


def _server_reachable(base_url: str, timeout: float = 0.35) -> bool:
    """Avoid starting npx when a test/dev server is not actually listening."""
    try:
        parsed = urlparse(base_url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except Exception:
        return False


class PlaywrightMCPClient:
    """Minimal newline-delimited JSON-RPC client for one MCP browser session."""

    def __init__(self, project_dir: Path, base_url: str, *,
                 storage_state: dict | None = None, package: str = "",
                 start_timeout: float = 12.0, call_timeout: float = 8.0,
                 popen_factory=None):
        self.project_dir = Path(project_dir).resolve()
        self.base_url = str(base_url or "").rstrip("/")
        requested = (package or os.getenv("AGENTFORGE_PLAYWRIGHT_MCP_PACKAGE", "")
                     or PLAYWRIGHT_MCP_PACKAGE).strip()
        self.package = requested if _PACKAGE_RE.fullmatch(requested) else PLAYWRIGHT_MCP_PACKAGE
        self.storage_state = storage_state if isinstance(storage_state, dict) else None
        self.start_timeout = max(1.0, float(start_timeout))
        self.call_timeout = max(1.0, float(call_timeout))
        self._popen = popen_factory or subprocess.Popen
        self._proc = None
        self._messages: queue.Queue = queue.Queue()
        self._stderr: list[str] = []
        self._request_id = 0
        self._lock = threading.Lock()
        self._state_path = ""

    def _command(self) -> list[str]:
        prefix = _npx_prefix(self.package)
        if not prefix:
            raise PlaywrightMCPError("node/npx is unavailable")
        parsed = urlparse(self.base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        command = prefix + [
            "--headless", "--isolated", "--browser", "chrome",
            "--block-service-workers", "--image-responses", "omit",
            "--snapshot-mode", "none", "--codegen", "none",
            "--console-level", "error", "--timeout-action", "4000",
            "--timeout-navigation", "20000", "--timeout-settle", "150",
            "--allowed-hosts", "localhost,127.0.0.1",
        ]
        if origin:
            command += ["--allowed-origins", origin]
        if self._state_path:
            command += ["--storage-state", self._state_path]
        return command

    def _write_storage_state(self) -> None:
        state = self.storage_state or {}
        if not (state.get("cookies") or state.get("origins")):
            return
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json",
            prefix="agentforge-playwright-mcp-", delete=False)
        try:
            json.dump(state, handle, ensure_ascii=False)
            self._state_path = handle.name
        finally:
            handle.close()

    def start(self) -> None:
        if self._proc is not None:
            return
        self._write_storage_state()
        kwargs = {
            "cwd": str(self.project_dir),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = self._popen(self._command(), **kwargs)
        except Exception as exc:
            self.close()
            raise PlaywrightMCPError(f"could not start sidecar: {exc}") from exc

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            self._request(
                "initialize",
                {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                 "clientInfo": {"name": "AgentForge QA", "version": "1"}},
                timeout=self.start_timeout,
            )
            self._notify("notifications/initialized", {})
        except Exception:
            self.close()
            raise

    def _read_stdout(self) -> None:
        stream = getattr(self._proc, "stdout", None)
        if stream is None:
            return
        try:
            for line in stream:
                try:
                    message = json.loads(line)
                except Exception:
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
        except Exception as exc:
            log.debug("playwright mcp stdout: %s", exc)

    def _read_stderr(self) -> None:
        stream = getattr(self._proc, "stderr", None)
        if stream is None:
            return
        try:
            for line in stream:
                text = str(line or "").strip()
                if text:
                    self._stderr.append(text[:500])
                    del self._stderr[:-12]
        except Exception:
            pass

    def _send(self, message: dict) -> None:
        if self._proc is None or self._proc.poll() is not None:
            detail = self._stderr[-1] if self._stderr else "process exited"
            raise PlaywrightMCPError(detail)
        try:
            self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except Exception as exc:
            raise PlaywrightMCPError(f"sidecar input failed: {exc}") from exc

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict, *, timeout: float | None = None) -> dict:
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            self._send({"jsonrpc": "2.0", "id": request_id,
                        "method": method, "params": params})
            deadline = time.monotonic() + (timeout or self.call_timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = self._stderr[-1] if self._stderr else "no response"
                    raise PlaywrightMCPError(f"{method} timed out: {detail}")
                try:
                    message = self._messages.get(timeout=remaining)
                except queue.Empty as exc:
                    detail = self._stderr[-1] if self._stderr else "no response"
                    raise PlaywrightMCPError(f"{method} timed out: {detail}") from exc
                if message.get("id") != request_id:
                    continue
                if message.get("error"):
                    err = message.get("error") or {}
                    raise PlaywrightMCPError(str(err.get("message") or err)[:500])
                result = message.get("result") or {}
                return result if isinstance(result, dict) else {"value": result}

    @staticmethod
    def _content_text(result: dict) -> str:
        rows = []
        for item in (result.get("content") or []):
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                rows.append(str(item["text"]))
        return "\n".join(rows).strip()

    def call(self, tool: str, arguments: dict | None = None,
             *, timeout: float | None = None) -> str:
        result = self._request(
            "tools/call", {"name": tool, "arguments": arguments or {}},
            timeout=timeout)
        if result.get("isError"):
            raise PlaywrightMCPError(self._content_text(result) or f"{tool} failed")
        return self._content_text(result)

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self._state_path:
            try:
                Path(self._state_path).unlink(missing_ok=True)
            except Exception:
                pass
            self._state_path = ""

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _typ, _value, _trace):
        self.close()


class E2EPlaywrightMCPMixin:
    """Read-only planner/healer evidence from an isolated MCP browser."""

    MCP_SNAPSHOT_CHARS = 9_000
    MCP_DIAGNOSTIC_CHARS = 3_500

    def _mcp_cache(self) -> dict:
        cache = getattr(self, "_playwright_mcp_cache", None)
        if cache is None:
            cache = {}
            self._playwright_mcp_cache = cache
        return cache

    @staticmethod
    def _storage_fingerprint(storage_state: dict | None) -> str:
        try:
            raw = json.dumps(storage_state or {}, sort_keys=True, separators=(",", ":"))
        except Exception:
            raw = ""
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]

    def playwright_mcp_evidence(self, route: str, *, storage_state: dict | None = None,
                                purpose: str = "planner") -> str:
        """Snapshot one local route. Returns empty evidence on every MCP failure."""
        route = str(route or "/").strip()
        if not route.startswith("/") or route.startswith("//"):
            return ""
        if getattr(self, "_playwright_mcp_unavailable", False):
            return ""
        if not playwright_mcp_enabled() or not _server_reachable(self.base_url):
            return ""
        key = (str(purpose), route, self._storage_fingerprint(storage_state))
        cache = self._mcp_cache()
        if key in cache:
            return cache[key]

        url = urljoin(self.base_url + "/", route.lstrip("/"))
        client = PlaywrightMCPClient(
            self.project_dir, self.base_url, storage_state=storage_state)
        try:
            with client:
                client.call("browser_navigate", {"url": url}, timeout=22)
                snapshot = client.call(
                    "browser_snapshot", {"depth": 9, "boxes": False}, timeout=8)
                console = client.call(
                    "browser_console_messages", {"level": "error", "all": True}, timeout=5)
                network = client.call(
                    "browser_network_requests", {"static": False, "filter": "/api/"}, timeout=5)
        except Exception as exc:
            # MCP augments the direct browser runner. Package/browser/network
            # availability must never crash or mark the application as failed.
            log.debug("playwright mcp %s evidence unavailable: %s", purpose, exc)
            self._playwright_mcp_unavailable = True
            cache[key] = ""
            return ""

        parts = [
            f"Playwright MCP {purpose} evidence at {route} (isolated, read-only)",
            snapshot[:self.MCP_SNAPSHOT_CHARS],
        ]
        if console and "no console messages" not in console.lower():
            parts.append("MCP console errors:\n" + console[:self.MCP_DIAGNOSTIC_CHARS])
        if network and "no requests" not in network.lower():
            parts.append("MCP API requests:\n" + network[:self.MCP_DIAGNOSTIC_CHARS])
        evidence = "\n".join(x for x in parts if x).strip()[:16_000]
        cache[key] = evidence
        try:
            self._log("INFO", f"   🔌 Playwright MCP {purpose} evidence captured for {route}")
        except Exception:
            pass
        return evidence

    def invalidate_playwright_mcp_evidence(self) -> None:
        self._mcp_cache().clear()

    def close_playwright_mcp(self) -> None:
        """Public lifecycle hook; all current probes are already one-shot."""
        self.invalidate_playwright_mcp_evidence()


__all__ = [
    "E2EPlaywrightMCPMixin", "MCP_PROTOCOL_VERSION", "PLAYWRIGHT_MCP_PACKAGE",
    "PlaywrightMCPClient", "PlaywrightMCPError", "playwright_mcp_enabled",
]
