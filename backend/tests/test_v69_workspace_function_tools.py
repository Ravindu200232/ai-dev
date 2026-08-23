"""Standard workspace function tools are shared across agent surfaces."""
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agents.builder.orchestration.agent import ArchitectAgent
from agents.core.workspace import (READ_TOOL_NAMES, WORKSPACE_TOOL_NAMES,
                                   WorkspaceTools)


def _workspace(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    page = app / "page.jsx"
    page.write_text("first line\nexport default function Page(){}\n", encoding="utf-8")
    arch = SimpleNamespace(
        project_dir=tmp_path,
        files={"app/page.jsx": page.read_text(encoding="utf-8"),
               "components/Card.jsx": "export default function Card(){}"},
        plan={}, plan_md="", agent_name="architect",
    )
    return WorkspaceTools(arch)


def _call(name, arguments, call_id="call_1"):
    return {"id": call_id,
            "function": {"name": name, "arguments": arguments}}


def test_workspace_schemas_cover_reads_commands_and_memory():
    schemas = WorkspaceTools.schemas()
    names = {schema["function"]["name"] for schema in schemas}

    assert set(READ_TOOL_NAMES) <= names
    assert set(WORKSPACE_TOOL_NAMES) == names
    listing = next(s for s in schemas if s["function"]["name"] == "list_files")
    assert {"path", "depth"} <= set(
        listing["function"]["parameters"]["properties"])


def test_native_listing_reports_directories_sizes_and_permissions(tmp_path):
    tools = _workspace(tmp_path)

    listing = tools.list_files(path="", depth=4)

    assert "dir" in listing and "app/" in listing
    assert "file" in listing and "app/page.jsx" in listing
    assert str(len(tools.files["app/page.jsx"].encode("utf-8"))) in listing
    assert re.search(r"-[rwx-]{9}\s+app/page\.jsx", listing)
    for unsafe in ("../secret", "/etc", "C:/Windows"):
        assert tools.list_files(path=unsafe).startswith("refused")


def test_standard_calls_return_role_tool_messages_and_line_windows(tmp_path):
    tools = _workspace(tmp_path)
    calls = [
        _call("list_files", {"path": "app", "depth": 2}, "list_1"),
        _call("read_file", '{"path":"app/page.jsx","start":2,"limit":1}',
              "read_1"),
    ]

    messages, used = tools.serve_calls(calls, names=READ_TOOL_NAMES)

    assert used == 2 and len(messages) == 2
    assert all(message["role"] == "tool" for message in messages)
    assert messages[0]["tool_call_id"] == "list_1"
    assert "app/page.jsx" in messages[0]["content"]
    assert "    2 export default" in messages[1]["content"]
    assert "first line" not in messages[1]["content"]


def test_every_function_call_receives_a_response_even_past_turn_limit(tmp_path):
    tools = _workspace(tmp_path)
    calls = [_call("list_files", {"path": ""}, f"call_{i}") for i in range(3)]

    messages, used = tools.serve_calls(calls, max_calls=1)

    assert len(messages) == len(calls)
    assert used == 1
    assert all(message.get("tool_call_id") for message in messages)
    assert "at most 1" in messages[-1]["content"]


def test_stream_retries_without_function_tools_when_model_rejects_them():
    agent = ArchitectAgent.__new__(ArchitectAgent)
    agent.think = False
    agent.model = "model"
    agent._log = Mock()
    calls = []

    def stream_once(_messages, _sink, **kwargs):
        calls.append(kwargs.get("tools"))
        if kwargs.get("tools"):
            raise RuntimeError("tools unsupported")
        return [], False

    agent._stream_once = stream_once

    assert agent._stream([], lambda _text: None,
                         tools=WorkspaceTools.schemas(READ_TOOL_NAMES)) == []
    assert calls[0] and calls[1] is None


def test_all_requested_agents_offer_and_serve_standard_workspace_calls():
    root = Path(__file__).resolve().parents[1]
    paths = {
        "builder": "agents/builder/orchestration/turns.py",
        "qa_analyzer": "agents/gates/analyzer_runtime.py",
        "qa_debugger": "qa_agent/debugger_investigate.py",
        "feature_plan": "agents/feature/planning.py",
        "feature_audit": "agents/feature/audit.py",
        "feature_apply": "agents/feature/apply.py",
        "selection": "server_modules/agent/selection/scope_map.py",
        "pencil": "server_modules/agent/pencil/page.py",
    }
    for role, rel in paths.items():
        source = (root / rel).read_text(encoding="utf-8")
        assert "workspace.schemas(" in source, role
        assert "serve_calls(" in source, role
        assert "assistant_message(" in source, role
