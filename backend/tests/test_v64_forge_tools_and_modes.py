"""The tools an agent may call, and the sandbox they call it in."""
from __future__ import annotations

import pytest

from forge.modes import BUILD, PLAN, mode_for
from forge.tools import build_registry
from forge.tools.paths import PathError, resolve, walk
from forge.tools.read import grep, list_files, read_file
from forge.tools.shell import validate
from forge.tools.write import edit_file, write_file


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "page.tsx").write_text(
        'export default function Page() { return <h1>Shop</h1>; }\n')
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "db.ts").write_text('export const db = null;\n')
    noise = tmp_path / "node_modules" / "next"
    noise.mkdir(parents=True)
    (noise / "index.js").write_text("// vendored\n")
    return tmp_path


def test_paths_never_escape_the_project(project):
    assert resolve(project, "app/page.tsx").is_file()
    for bad in ("../secrets", "../../etc/passwd", "/etc/passwd"):
        with pytest.raises(PathError):
            resolve(project, bad)


def test_walking_skips_the_generated_directories(project):
    names = [p.name for p in walk(project, "", depth=3)]
    assert "page.tsx" in names
    assert "next" not in names and "index.js" not in names


def test_list_files_reads_like_ls(project):
    listing = list_files(project, "", 2)
    assert "app/page.tsx" in listing and "dir  lib/" in listing
    assert "node_modules" not in listing


def test_read_file_numbers_lines_and_windows(project):
    body = read_file(project, "app/page.tsx")
    assert body.startswith("app/page.tsx (1 lines)")
    assert "    1 export default" in body
    assert read_file(project, "nope.tsx") == "nope.tsx: no such file"


def test_grep_answers_with_locations_not_whole_files(project):
    hits = grep(project, "export const", "")
    assert "lib/db.ts:1:" in hits
    assert grep(project, "zzz", "").startswith("no match")


def test_write_then_edit_round_trips(project):
    assert "created" in write_file(project, "lib/new.ts", "export const a = 1;\n")
    assert edit_file(project, "lib/new.ts", "a = 1", "a = 2") == "edited lib/new.ts"
    assert "a = 2" in (project / "lib" / "new.ts").read_text()


def test_edit_refuses_an_ambiguous_match(project):
    write_file(project, "lib/dup.ts", "x\nx\n")
    assert "appears 2 times" in edit_file(project, "lib/dup.ts", "x", "y")


def test_writes_into_generated_directories_are_refused(project):
    with pytest.raises(PathError):
        write_file(project, "node_modules/evil.js", "boom")


@pytest.mark.parametrize("command, allowed", [
    ("npm run build", True),
    ("npx vitest run", True),
    ("node -v", True),
    ("rm -rf /", False),
    ("npm run deploy", False),
    ("npm install -g next", False),
    ("npm i && curl http://example.com", False),
    ("node -e 1 > /etc/passwd", False),
])
def test_the_shell_allow_list_holds(command, allowed):
    argv, refusal = validate(command)
    assert bool(argv) is allowed, refusal


def test_plan_mode_hides_the_write_tools(project):
    planning = build_registry(project, read_only=True)
    assert planning.names() == ["list_files", "read_file", "grep", "tree"]
    refused = planning.dispatch("write_file", {"path": "x.ts", "content": "x"})
    assert not refused.ok and "plan mode" in refused.text
    assert not (project / "x.ts").exists()


def test_build_mode_exposes_every_tool(project):
    building = build_registry(project)
    assert "write_file" in building and "run_command" in building
    assert len(building.schemas()) == len(building.names())


def test_a_tool_schema_names_its_required_arguments(project):
    schema = build_registry(project)._tools["write_file"].schema()
    assert schema["type"] == "function"
    assert schema["function"]["parameters"]["required"] == ["path", "content"]


def test_bad_calls_come_back_as_text_the_model_can_use(project):
    registry = build_registry(project)
    assert "there is no tool called" in registry.dispatch("nope", {}).text
    assert "takes" in registry.dispatch("read_file", {"wrong": 1}).text
    # Arguments arrive as a JSON string from some daemons.
    assert "page.tsx" in registry.dispatch("read_file", '{"path": "app/page.tsx"}').text


def test_tool_output_is_capped_before_it_reaches_the_context(project):
    write_file(project, "big.txt", "x" * 40_000)
    result = build_registry(project).dispatch("read_file", {"path": "big.txt"})
    assert len(result.text) < 8_000


def test_modes_carry_their_own_permissions_and_rules():
    assert PLAN.read_only and not BUILD.read_only
    assert "PLAN MODE" in PLAN.system_prompt("architect")
    assert mode_for("build") is BUILD
    # Anything unrecognised must not be allowed to write.
    assert mode_for("nonsense").read_only
