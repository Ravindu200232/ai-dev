"""Feature, select and pencil edits run on the forge loop."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge.edit import EditAgent, edit_budget, edit_model, project_dir
from forge.edit.tools import capture_write_tools, edit_registry, host_write_tools
from forge.llm import ScriptedModel, tool_call
from forge.modes import PLAN
from forge.tools.paths import PathError

FEATURE = Path("agents/feature")
SELECT = Path("server_modules/agent/selection/scope_map.py")
PENCIL = Path("server_modules/agent/pencil/page.py")


class Host:
    """The smallest thing that looks like the architect to an edit."""

    EDIT_TIMEOUT = 5

    def __init__(self, tmp_path, files=None):
        self.project_dir = tmp_path
        self.files = dict(files or {})
        self.written = []

    def write_file(self, rel, content):
        if self.files.get(rel) == content:
            return False
        self.files[rel] = content
        self.written.append(rel)
        (self.project_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.project_dir / rel).write_text(content, encoding="utf-8")
        return True

    def _budget_chars(self):
        return 80_000


@pytest.fixture()
def host(tmp_path):
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "Card.jsx").write_text(
        "export default function Card(){return <div>old</div>}", encoding="utf-8")
    return Host(tmp_path, {"components/Card.jsx":
                           "export default function Card(){return <div>old</div>}"})


# --- the tools --------------------------------------------------------------

def test_a_write_goes_through_the_host_not_straight_to_disk(host):
    """The host's writer merges package.json and emits the UI's file event."""
    registry = edit_registry(host, host.project_dir)
    out = registry.dispatch("write_file", {"path": "lib/db.js",
                                           "content": "export const db = 1;\n"})
    assert out.ok and "created lib/db.js" in out.text
    assert host.written == ["lib/db.js"]
    assert host.files["lib/db.js"] == "export const db = 1;\n"


def test_the_result_wording_lets_the_loop_track_what_was_touched(host):
    """`created` / `replaced` / `edited` is how a run knows its own files."""
    model = ScriptedModel([
        tool_call("write_file", path="lib/a.js", content="export const a = 1;"),
        tool_call("edit_file", path="components/Card.jsx", find="old", replace="new"),
        "Done.",
    ])
    result = EditAgent(host, host.project_dir, model).run("role", "task")
    assert result.files == ["lib/a.js", "components/Card.jsx"]


def test_a_rewrite_identical_to_what_is_there_is_not_a_write(host):
    registry = edit_registry(host, host.project_dir)
    same = host.files["components/Card.jsx"]
    out = registry.dispatch("write_file", {"path": "components/Card.jsx",
                                           "content": same})
    assert "already held exactly that" in out.text
    assert host.written == []


def test_an_empty_rewrite_is_refused(host):
    out = edit_registry(host, host.project_dir).dispatch(
        "write_file", {"path": "components/Card.jsx", "content": "   "})
    assert "would be empty" in out.text and host.written == []


def test_an_edit_outside_the_project_is_refused(host):
    out = edit_registry(host, host.project_dir).dispatch(
        "write_file", {"path": "../escape.js", "content": "x"})
    assert not out.ok and "escapes the project" in out.text
    assert host.written == []


def test_edit_file_refuses_an_ambiguous_match(host):
    host.write_file("lib/dup.js", "x\nx\n")
    host.written.clear()
    out = edit_registry(host, host.project_dir).dispatch(
        "edit_file", {"path": "lib/dup.js", "find": "x", "replace": "y"})
    assert "appears 2 times" in out.text and host.written == []


def test_captured_writes_do_not_reach_the_host(host):
    """The element and sketch editors inspect a rewrite before allowing it."""
    writes = {}
    registry = edit_registry(host, host.project_dir, capture=writes)
    registry.dispatch("write_file", {"path": "components/Card.jsx",
                                     "content": "export default () => null;"})
    assert writes == {"components/Card.jsx": "export default () => null;"}
    assert host.written == [], "a captured write must not have happened yet"


def test_a_captured_edit_builds_on_the_captured_copy(host):
    writes = {}
    registry = edit_registry(host, host.project_dir, capture=writes)
    registry.dispatch("write_file", {"path": "a.js", "content": "one two"})
    registry.dispatch("edit_file", {"path": "a.js", "find": "two", "replace": "three"})
    assert writes["a.js"] == "one three"
    assert host.written == []


def test_an_injected_writer_can_guard_and_refuse(host):
    """Feature apply and the page editor guard every write themselves."""
    seen = []

    def guarded(path, content):
        seen.append(path)
        return path.startswith("app/")

    registry = edit_registry(host, host.project_dir, writer=guarded)
    taken = registry.dispatch("write_file", {"path": "app/page.jsx", "content": "x"})
    refused = registry.dispatch("write_file", {"path": "secret.js", "content": "x"})
    assert "created app/page.jsx" in taken.text
    assert "already held exactly that" in refused.text
    assert seen == ["app/page.jsx", "secret.js"]
    assert host.written == [], "the injected writer replaces the host's"


def test_a_host_with_nowhere_to_write_says_so_rather_than_crashing(tmp_path):
    class ReadOnlyHost:
        files = {}
        project_dir = tmp_path

    out = edit_registry(ReadOnlyHost(), tmp_path).dispatch(
        "write_file", {"path": "a.js", "content": "x"})
    assert "nowhere to write" in out.text


def test_plan_mode_offers_no_way_to_write(host):
    registry = edit_registry(host, host.project_dir, read_only=True)
    assert registry.names() == ["list_files", "read_file", "grep", "tree"]
    assert not registry.dispatch("write_file", {"path": "a.js", "content": "x"}).ok
    assert host.written == []


def test_an_edit_has_no_shell(host):
    assert "run_command" not in edit_registry(host, host.project_dir)


# --- the agent --------------------------------------------------------------

def test_the_files_the_caller_names_are_read_in_before_the_model_asks(host):
    model = ScriptedModel(["nothing to do"])
    EditAgent(host, host.project_dir, model).run(
        "role", "task", focus=["components/Card.jsx"])
    grounding = model.seen[0]["messages"][-1]["content"]
    assert "components/Card.jsx" in grounding and "old" in grounding


def test_a_session_keeps_one_conversation_across_rounds(host):
    model = ScriptedModel(["round one", "round two", "round three"])
    loop = EditAgent(host, host.project_dir, model).session("role", "the request")
    loop.run()
    loop.run("that was not enough")
    loop.run("still not enough")
    # The request stays pinned, and each round sees the ones before it.
    last = model.seen[-1]["messages"]
    assert last[1]["content"] == "the request"
    assert any("that was not enough" == m.get("content") for m in last)
    assert len(model.seen) == 3


def test_an_image_rides_on_the_pinned_request(host):
    """The sketch editor sends a screenshot; compaction must never drop it."""
    model = ScriptedModel(["seen"])
    loop = EditAgent(host, host.project_dir, model).session(
        "role", "redesign this", images=["BASE64PNG"])
    loop.run()
    pinned = model.seen[0]["messages"][1]
    assert pinned["images"] == ["BASE64PNG"]


def test_the_skill_matching_the_task_is_loaded(host):
    model = ScriptedModel(["ok"])
    EditAgent(host, host.project_dir, model).run(
        "role", "add an app router page with a form")
    assert "skill: nextjs" in model.seen[0]["messages"][0]["content"]


def test_plan_mode_analysis_writes_nothing(host):
    model = ScriptedModel([
        tool_call("write_file", path="components/Card.jsx", content="sneaky"),
        "## Files to change\n- `components/Card.jsx` — the card\n",
    ])
    plan = EditAgent(host, host.project_dir, model).plan("architect", "what changes?")
    assert plan.paths == ["components/Card.jsx"]
    assert host.written == []


# --- what the host lends ----------------------------------------------------

def test_the_host_can_lend_its_own_model(host):
    lent = ScriptedModel(["from the host"])
    host.forge_model = lent
    assert edit_model(host) is lent


def test_the_hosts_character_budget_becomes_a_token_budget(host):
    assert edit_budget(host) == 20_000                # 80_000 chars / 4
    assert edit_budget(host, chars=400) == 8_000      # never below the floor

    class NoBudget:
        pass

    assert edit_budget(NoBudget()) == 20_000          # a sane default


def test_a_host_without_a_project_directory_falls_back(host):
    class Bare:
        pass

    assert project_dir(Bare()) == "."
    assert project_dir(host) == str(host.project_dir)


# --- the call sites actually moved ------------------------------------------

@pytest.mark.parametrize("path", [
    FEATURE / "planning.py", FEATURE / "audit.py", FEATURE / "apply.py",
    SELECT, PENCIL,
])
def test_no_edit_path_still_hand_builds_a_tool_conversation(path):
    """The XML-tag protocol and the stream parser are gone from these."""
    body = path.read_text(encoding="utf-8")
    assert "TOOL_HELP" not in body, f"{path} still pastes the old tool protocol"
    assert "WorkspaceTools(" not in body or "serve(" not in body, (
        f"{path} still parses tools out of the reply text")
    assert "EditAgent" in body, f"{path} is not on the forge loop"


def test_feature_planning_reads_the_real_window_not_a_character_count():
    body = (FEATURE / "planning.py").read_text(encoding="utf-8")
    assert "loop.convo.pressure()" in body
    assert "used_chars" not in body


def test_the_evidence_helpers_still_use_the_deterministic_reader():
    """`WorkspaceTools` stays where it gathers evidence, not where it loops."""
    for name in ("planning.py", "audit.py"):
        body = (FEATURE / name).read_text(encoding="utf-8")
        assert "WorkspaceTools(self.arch)" in body
