"""Playwright MCP stays isolated, optional, and evidence-only."""
from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import qa_agent.playwright_mcp as mcp
from qa_agent.e2e import E2EAgent


class _FakeProc:
    def __init__(self):
        responses = [
            {"jsonrpc": "2.0", "id": 1, "result": {
                "protocolVersion": mcp.MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "Playwright", "version": "test"},
            }},
            {"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text", "text": "navigated"}] }},
        ]
        self.stdout = io.StringIO("".join(json.dumps(x) + "\n" for x in responses))
        self.stderr = io.StringIO("")
        self.stdin = io.StringIO()
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_stdio_client_initializes_and_calls_tools_without_shell(tmp_path):
    seen = {}

    def spawn(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _FakeProc()

    client = mcp.PlaywrightMCPClient(
        tmp_path, "http://localhost:3000", popen_factory=spawn)
    client.start()
    assert client.call("browser_navigate", {"url": "http://localhost:3000/"}) == "navigated"
    client.close()

    assert seen["kwargs"].get("shell") is not True
    assert mcp.PLAYWRIGHT_MCP_PACKAGE in seen["argv"]
    assert "--isolated" in seen["argv"]
    assert "--headless" in seen["argv"]
    assert "--allowed-hosts" in seen["argv"]


def test_bad_package_override_cannot_become_a_shell_command(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTFORGE_PLAYWRIGHT_MCP_PACKAGE", "x & remove-everything")
    client = mcp.PlaywrightMCPClient(tmp_path, "http://localhost:3000")
    assert client.package == mcp.PLAYWRIGHT_MCP_PACKAGE


def test_role_storage_fingerprint_separates_mcp_evidence_cache(tmp_path, monkeypatch):
    arch = SimpleNamespace(project_dir=tmp_path, files={}, plan={})
    agent = E2EAgent(arch, tmp_path)
    monkeypatch.setattr(mcp, "_server_reachable", lambda _url: True)
    monkeypatch.setattr(mcp, "playwright_mcp_enabled", lambda: True)

    calls = []

    class FakeClient:
        def __init__(self, _project, _base, storage_state=None):
            calls.append(storage_state)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def call(self, tool, _args, timeout=None):
            return {"browser_snapshot": "- button Save",
                    "browser_console_messages": "No console messages",
                    "browser_network_requests": "No requests"}.get(tool, "ok")

    monkeypatch.setattr(mcp, "PlaywrightMCPClient", FakeClient)
    owner = {"cookies": [{"name": "session", "value": "owner"}]}
    customer = {"cookies": [{"name": "session", "value": "customer"}]}
    one = agent.playwright_mcp_evidence("/dashboard", storage_state=owner)
    again = agent.playwright_mcp_evidence("/dashboard", storage_state=owner)
    two = agent.playwright_mcp_evidence("/dashboard", storage_state=customer)

    assert "button Save" in one and one == again and "button Save" in two
    assert calls == [owner, customer]


def test_mcp_failure_is_empty_evidence_not_an_e2e_failure(tmp_path, monkeypatch):
    arch = SimpleNamespace(project_dir=tmp_path, files={}, plan={})
    agent = E2EAgent(arch, tmp_path)
    monkeypatch.setattr(mcp, "_server_reachable", lambda _url: True)
    monkeypatch.setattr(mcp, "playwright_mcp_enabled", lambda: True)

    class BrokenClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("browser unavailable")

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(mcp, "PlaywrightMCPClient", BrokenClient)
    assert agent.playwright_mcp_evidence("/", purpose="healer") == ""
    assert agent.playwright_mcp_evidence("/another", purpose="healer") == ""


def test_failed_initialization_terminates_the_sidecar(tmp_path):
    class SilentProc(_FakeProc):
        def __init__(self):
            super().__init__()
            self.stdout = io.StringIO("")

    proc = SilentProc()
    client = mcp.PlaywrightMCPClient(
        tmp_path, "http://localhost:3000", start_timeout=0.01,
        popen_factory=lambda *_args, **_kwargs: proc)
    try:
        client.start()
    except mcp.PlaywrightMCPError:
        pass
    else:
        raise AssertionError("silent MCP process should time out")
    assert proc.returncode == 0


def test_unit_runner_and_generated_project_do_not_mix_in_mcp_alpha_dependency():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "server_modules" / "qa" / "unit_stage.py").read_text("utf-8")
    scaffold = (root / "agents" / "builder" / "scaffolding" / "base.py").read_text("utf-8")
    stage = (root / "server_modules" / "qa" / "e2e_stage.py").read_text("utf-8")

    assert "@playwright/mcp" not in unit
    assert "@playwright/mcp" not in scaffold
    assert "close_playwright_mcp" in stage
    assert '"@playwright/test": "^1.62.1"' in scaffold
