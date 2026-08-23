"""The project layout every agent is handed, and stopping a looping turn."""
import importlib
from pathlib import Path
from types import SimpleNamespace

from agents.builder.orchestration.agent import ArchitectAgent
from agents.core.workspace import (STRUCTURE_NOTE, project_structure,
                                   structure_block)

ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "app/layout.jsx": "x", "app/page.jsx": "x",
    "app/admin/page.jsx": "x", "app/admin/sale/page.jsx": "x",
    "app/api/products/route.js": "x",
    "app/api/auth/[...all]/route.js": "x",
    "components/Navbar.jsx": "x", "components/SaleTerminal.jsx": "x",
    "lib/auth.js": "x", "lib/seed.js": "x",
    "node_modules/react/index.js": "x", ".next/build.js": "x",
    "package.json": "{}",
}


# ------------------------------------------------------------- the listing
def test_the_listing_groups_files_under_their_directory():
    tree = project_structure(FILES)

    assert tree.startswith("11 file(s) in 8 directories")
    assert "app/api/auth/[...all]/" in tree
    # One line per directory, its files together, so it stays cheap to send.
    line = next(l for l in tree.splitlines() if l.startswith("components/"))
    assert "Navbar.jsx, SaleTerminal.jsx" in line


def test_build_output_and_dependencies_are_not_part_of_the_app_layout():
    tree = project_structure(FILES)

    assert "node_modules" not in tree
    assert ".next" not in tree


def test_a_long_listing_is_truncated_honestly_not_silently():
    wide = {f"app/section{n}/page.jsx": "x" for n in range(400)}

    tree = project_structure(wide, max_chars=600)

    assert tree.startswith("400 file(s) in 400 directories")
    assert tree.rstrip().splitlines()[-1].startswith("… ")
    assert "more directories" in tree


def test_an_empty_project_contributes_no_prompt_section():
    assert project_structure({}) == ""
    assert structure_block({}) == ""
    assert structure_block(SimpleNamespace(files={})) == ""


def test_the_block_is_a_ready_prompt_section_taken_from_an_agent():
    block = structure_block(SimpleNamespace(files=FILES))

    assert block.startswith("## The files this project is made of\n")
    assert "components/" in block
    assert STRUCTURE_NOTE in block
    # It names the tools that can act on the paths it just listed.
    assert "read_file" in STRUCTURE_NOTE


# ------------------------------------------------- who is handed the layout
def test_every_requested_agent_is_handed_the_project_layout():
    surfaces = {
        "feature_plan": "agents/feature/planning.py",
        "feature_audit": "agents/feature/audit.py",
        "feature_apply": "agents/feature/apply.py",
        "pencil": "server_modules/agent/pencil/page.py",
        "selection": "server_modules/agent/selection/scope_map.py",
    }
    for role, rel in surfaces.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "structure_block(" in source, role


def test_the_qa_surfaces_are_handed_it_too():
    pairs = {
        "e2e_author": "qa_agent/e2e_journeys.py",
        "e2e_debugger": "qa_agent/debugger_investigate.py",
        "unit_fixer": "agents/repair/prompt.py",
        "runtime_fixer": "agents/repair/apply.py",
    }
    for role, rel in pairs.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "project_structure(" in source, role
        assert "files this app is made of" in source, role


def test_the_scenario_author_can_also_list_files_as_a_tool():
    from qa_agent.e2e_journeys import E2E_AUTHOR_TOOLS

    assert "list_files" in E2E_AUTHOR_TOOLS


# --------------------------------------------------------------- loop guard
def _agent(chunks):
    agent = ArchitectAgent.__new__(ArchitectAgent)
    agent.model = "m"
    agent.num_ctx = 8_192
    agent._ctx_cache = {"m": 8_192}
    agent.tokens_in = agent.tokens_out = 0
    agent.said = []
    agent._log = lambda lvl, txt: agent.said.append(txt)
    agent.client = SimpleNamespace(
        chat_stream=lambda *a, **k: iter(chunks))
    return agent


def _text(body):
    return {"message": {"content": body}}


def test_a_model_repeating_one_passage_is_cut_off_at_three(monkeypatch):
    block = ("the same paragraph over and over " * 8)[:240]
    agent = _agent([_text(block) for _ in range(20)])
    got = []

    agent._stream_once([], got.append, tools=None, temperature=0.5,
                       model="m", timeout=None, think=False)

    # Three arrivals of the block, not six: the fourth never streams.
    assert len(got) == 3
    assert any("It is looping, not working" in t for t in agent.said)


def test_a_long_cycle_is_caught_by_how_much_text_it_repeats(monkeypatch):
    # Every block is distinct within one pass, so no single passage reaches
    # three repeats quickly — the duplicated character count is what catches it.
    cycle = [f"{n:04d}" + "a" * 236 for n in range(60)]
    agent = _agent([_text(part) for _ in range(4) for part in cycle])
    got = []

    agent._stream_once([], got.append, tools=None, temperature=0.5,
                       model="m", timeout=None, think=False)

    assert len(got) < len(cycle) * 4
    stopped = next(t for t in agent.said if "looping" in t)
    assert "already written" in stopped


def test_text_that_never_repeats_is_never_cut_off():
    parts = [f"paragraph {n} " + "b" * 230 for n in range(120)]
    agent = _agent([_text(p) for p in parts])
    got = []

    agent._stream_once([], got.append, tools=None, temperature=0.5,
                       model="m", timeout=None, think=False)

    assert len(got) == len(parts)
    assert not any("looping" in t for t in agent.said)


def test_the_progress_line_reports_repeated_characters_not_a_block_count():
    runtime = (ROOT / "agents" / "builder" / "orchestration"
               / "runtime.py").read_text(encoding="utf-8")

    assert "LOOP_REPEATS = 3" in runtime
    assert "LOOP_DUP_CHARS = 20_000" in runtime
    assert "already sent, so it is repeating itself" in runtime
