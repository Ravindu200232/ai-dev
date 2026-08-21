"""The agents keep their own notebook and can run things, not just read."""
import tempfile
import unittest
from pathlib import Path

from agents.agent_memory import KINDS, AgentMemory, memory_for
from agents.workspace import TOOL_HELP, WorkspaceTools


class Arch:
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.files = {"app/page.jsx": "export default function P(){return null}"}
        self.plan = {}
        self.agent_name = "architect"


class AgentMemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mem = AgentMemory(self.dir, "architect")

    def test_a_note_survives_a_reload(self):
        self.assertTrue(self.mem.remember("decided", "the seed owns the keys"))
        self.assertEqual(len(AgentMemory(self.dir).entries()), 1)

    def test_the_same_thought_is_not_written_twice(self):
        self.assertTrue(self.mem.remember("tried", "rewrote the guard in app/admin/page.jsx"))
        self.assertFalse(self.mem.remember("tried", "Rewrote the guard in  app/admin/page.jsx"))
        self.assertEqual(len(self.mem.entries()), 1)

    def test_an_unknown_kind_lands_somewhere_sensible(self):
        self.mem.remember("nonsense", "a note with no valid kind at all")
        self.assertIn(self.mem.entries()[0]["kind"], KINDS)

    def test_the_prompt_block_leads_with_goal_then_what_to_avoid(self):
        self.mem.remember("tried", "changed the locator and it still failed")
        self.mem.remember("goal", "make the list page render its photos")
        self.mem.remember("avoid", "do not touch lib/auth.js for this")
        block = self.mem.block()
        self.assertLess(block.index("[goal]"), block.index("[avoid]"))
        self.assertLess(block.index("[avoid]"), block.index("[tried]"))

    def test_an_empty_notebook_adds_nothing_to_the_prompt(self):
        self.assertEqual(self.mem.block(), "")

    def test_recall_finds_the_note_that_shares_wording(self):
        self.mem.remember("learned", "the guard lives in the layout, not the page")
        self.mem.remember("learned", "seed keys are derived from the row name")
        rows = self.mem.recall("guard")
        self.assertEqual(len(rows), 1)
        self.assertIn("guard", rows[0]["text"])


class AgenticToolTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.arch = Arch(self.dir)
        self.tools = WorkspaceTools(self.arch)

    def test_the_help_offers_running_and_remembering(self):
        for tag in ("<run_command", "<remember", "<recall"):
            self.assertIn(tag, TOOL_HELP)

    def test_a_command_outside_the_allow_list_is_refused(self):
        for bad in ("rm -rf /", "curl http://x.test", "python -c 'import os'"):
            self.assertTrue(self.tools.run_command(bad).startswith("refused:"), bad)

    def test_notes_in_a_reply_are_written_as_the_model_wrote_them(self):
        kept = self.tools.remember_from(
            '<remember kind="avoid">do not rewrite lib/auth.js</remember>'
            '<remember kind="tried">swapped the locator, no change</remember>')
        self.assertEqual(kept, 2)
        kinds = {r["kind"] for r in memory_for(self.arch).entries()}
        self.assertEqual(kinds, {"avoid", "tried"})

    def test_serving_a_reply_both_answers_and_remembers(self):
        observations, _ = self.tools.serve(
            '<remember kind="learned">the keys come from the seed</remember>'
            '<recall query="keys"/>')
        self.assertIn("note(s) written", observations)
        self.assertIn("the keys come from the seed", observations)

    def test_the_notebook_is_shared_between_agents_on_one_project(self):
        self.tools.remember_from('<remember kind="decided">use the seed rows</remember>')
        self.assertTrue(WorkspaceTools(Arch(self.dir)).recall("seed").startswith("[decided]"))


if __name__ == "__main__":
    unittest.main()
