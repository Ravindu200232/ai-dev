import io
import tokenize
import unittest
from pathlib import Path


class AgentSourceCommentStyleTests(unittest.TestCase):
    def test_generated_app_comment_rewriter_is_not_installed(self):
        self.assertFalse(Path("agents/comment_style.py").exists())
        text = Path("agents/architect_writes.py").read_text(encoding="utf-8")
        self.assertNotIn("compact_source_comments", text)

    def test_agentforge_comments_are_compact(self):
        for root in (Path("agents"), Path("qa_agent")):
            for path in root.glob("*.py"):
                source = path.read_text(encoding="utf-8")
                lines = source.splitlines(True)
                for token in tokenize.generate_tokens(io.StringIO(source).readline):
                    if token.type != tokenize.COMMENT:
                        continue
                    row, col = token.start
                    if lines[row - 1][:col].strip():
                        continue
                    note = token.string[1:].strip()
                    if note and not set(note) <= set("-_=*#~."):
                        self.assertLessEqual(
                            len(note), 100,
                            f"long AgentForge comment in {path}:{row}",
                        )

    def test_security_and_api_qa_live_under_qa_agent(self):
        self.assertTrue(Path("qa_agent/security.py").is_file())
        self.assertTrue(Path("qa_agent/api_verification.py").is_file())


if __name__ == "__main__":
    unittest.main()
