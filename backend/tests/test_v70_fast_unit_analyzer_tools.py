"""Fast, source-grounded unit authoring and semantic analysis regressions."""
import re
from pathlib import Path
from types import SimpleNamespace

from agents.gates.analyzer_repair_findings import AnalyzerRepairFindingsMixin
from qa_agent.author import UnitTestAuthor
from qa_agent.session import QASession
from qa_agent.spec import TestTarget as UnitTarget


def _call(name, arguments, call_id="call_1"):
    return {"id": call_id,
            "function": {"name": name, "arguments": arguments}}


def test_stale_unit_tests_refresh_in_four_file_batches_before_vitest(tmp_path):
    session = QASession.__new__(QASession)
    session.manifest = {}
    session.sources = {}
    batches = []

    for index in range(9):
        target = f"components/Card{index}.jsx"
        test = f"tests/unit/components/Card{index}.test.jsx"
        session.sources[target] = f"export default function Card{index}(){{}}"
        session.manifest[test] = {
            "target": target, "phase": index + 1, "tier": 2, "stale": True,
        }

    session.read_source = lambda rel: session.sources.get(rel, "")
    session._log = lambda *_args: None

    class Author:
        def write_for(self, targets, phase=0):
            batches.append(list(targets))
            for target in targets:
                session.manifest[target.test_path]["stale"] = False
            return [target.test_path for target in targets]

    session.author = Author()

    written = session.refresh_stale()

    assert [len(batch) for batch in batches] == [4, 4, 1]
    assert len(written) == 9
    assert [target.phase for batch in batches for target in batch] == list(range(1, 10))
    assert not any(meta["stale"] for meta in session.manifest.values())


def test_unit_author_uses_native_read_tool_then_writes_from_observation(tmp_path):
    source = "export default function Card(){return <button>Save</button>}"
    calls_seen = []
    conversations = []

    class Arch:
        project_dir = Path(tmp_path)
        files = {"components/Card.jsx": source}
        plan = {}
        plan_md = ""
        model = "qa-model"
        num_ctx = 16_384

        def _stream(self, convo, sink, **kwargs):
            conversations.append(list(convo))
            calls_seen.append(kwargs.get("tools"))
            if len(conversations) == 1:
                return [_call("dependency_closure",
                              {"path": "components/Card.jsx"})]
            assert any(message.get("role") == "tool" for message in convo)
            sink(
                '<write_file path="tests/unit/components/Card.test.jsx">\n'
                "import { render, screen } from '@testing-library/react'\n"
                "import Card from '@/components/Card'\n"
                "it('renders its action', () => {\n"
                "  render(&lt;Card /&gt;)\n"
                "  expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument()\n"
                "})\n"
                "</write_file>")
            return []

    written_bodies = {}
    def write_test(rel, body, **_kwargs):
        written_bodies[rel] = body
        return True

    qa = SimpleNamespace(
        model="qa-model", reasoning=False, defer_execution=True, cmd=None,
        report=SimpleNamespace(suspects=[]),
        read_source=lambda rel: source if rel == "components/Card.jsx" else
        written_bodies.get(rel, ""),
        write_test_file=write_test,
    )
    author = UnitTestAuthor(Arch(), session=qa)
    target = UnitTarget("components/Card.jsx",
                        "tests/unit/components/Card.test.jsx", 2)

    written = author.write_for([target])

    assert written == ["tests/unit/components/Card.test.jsx"]
    assert calls_seen[0]
    assert {schema["function"]["name"] for schema in calls_seen[0]} >= {
        "read_file", "dependency_closure", "importers", "tests_for",
    }


def test_semantic_analyzer_uses_function_observation_before_verdict(tmp_path):
    conversations = []
    capability = "Customer can save a product and see it in the catalogue."

    class Arch:
        project_dir = Path(tmp_path)
        files = {
            "app/catalogue/page.jsx":
                "export default function Page(){return <main>Catalogue</main>}"
        }
        plan_md = capability
        plan = {"capabilities": [{
            "id": "CAP-001", "requirement": capability,
            "files": ["app/catalogue/page.jsx"], "e2e": True,
        }]}

        def _stream(self, messages, sink, **kwargs):
            conversations.append(list(messages))
            if len(conversations) == 1:
                assert kwargs.get("tools")
                return [_call("dependency_closure",
                              {"path": "app/catalogue/page.jsx"})]
            assert any(message.get("role") == "tool" for message in messages)
            sink("NONE")
            return []

    class Audit(AnalyzerRepairFindingsMixin):
        READ_RE = re.compile(r'<read_file\s+path=["\']([^"\']+)["\']\s*/?>')

        def __init__(self):
            self.arch = Arch()
            self.project_dir = Path(tmp_path)

        def plan_text(self):
            return capability

        def code_files(self):
            return dict(self.arch.files)

        def orphan_components(self):
            return []

        def inventory(self):
            return "app/catalogue/page.jsx"

        def _budget_chars(self):
            return 40_000

        def _read_for_model(self, rel):
            return self.arch.files.get(rel, "")

        def _log(self, *_args):
            pass

    assert Audit().unbuilt_promises() == []
    assert len(conversations) == 2


def test_pipeline_refreshes_stale_tests_before_constructing_vitest_runner():
    source = Path("server_modules/qa/unit_stage.py").read_text(encoding="utf-8")
    assert source.index("qa.refresh_stale(") < source.index("runner = VitestRunner(")


def test_analyzer_repair_and_unit_fixer_offer_standard_function_tools():
    root = Path(__file__).resolve().parents[1]
    expectations = {
        "agents/gates/analyzer_repair_apply.py": "stream_with_read_tools(",
        "agents/repair/apply.py": "workspace.schemas(",
        "qa_agent/author_write.py": "workspace.schemas(",
    }
    for rel, entrypoint in expectations.items():
        source = (root / rel).read_text(encoding="utf-8")
        assert entrypoint in source, rel
        assert "workspace.serve_calls(" in source, rel
        assert "workspace.assistant_message(" in source, rel
