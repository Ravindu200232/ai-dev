from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server
from agents.repair.agent import BugFixerAgent
from qa_agent.author import UnitTestAuthor
from qa_agent.session_common import add_helper_imports, normalize_objectid_helpers


class _QA:
    def __init__(self, sources):
        self.sources = sources

    def read_source(self, rel):
        return self.sources.get(rel, "")


def _author(sources):
    arch = SimpleNamespace(project_dir=Path("."), files=dict(sources))
    return UnitTestAuthor(arch, session=_QA(sources))


def test_test_local_consumer_markup_can_back_its_own_selectors():
    target = "components/ToastProvider.jsx"
    sources = {
        target: (
            "export default function ToastProvider({children}){return "
            "<div role=\"status\">{children}</div>}"
        )
    }
    test = """
      function Consumer(){
        return <div><button>Show toast</button><span data-testid="child" /></div>
      }
      screen.getByRole('button', { name: /show toast/i })
      screen.getByTestId('child')
    """
    assert _author(sources)._invented_selectors(test, target) == ""


def test_unnamed_form_is_not_treated_as_an_accessible_form_role():
    target = "components/PaymentForm.jsx"
    sources = {target: "export default function F(){return <form><button>Pay</button></form>}"}
    advice = _author(sources)._invented_selectors(
        "screen.getByRole('form')", target)
    assert "unnamed <form>" in advice


def test_named_form_can_be_queried_by_form_role():
    target = "components/PaymentForm.jsx"
    sources = {
        target: (
            "export default function F(){return "
            "<form aria-label=\"Payment\"><button>Pay</button></form>}"
        )
    }
    assert _author(sources)._invented_selectors(
        "screen.getByRole('form', { name: /payment/i })", target) == ""


def test_dynamic_route_preflight_requires_context_params_on_every_call():
    target = "app/api/orders/[id]/route.js"
    sources = {target: "export async function GET(request, {params}){return params}"}
    author = _author(sources)
    bad = "await getJson(GET, 'http://localhost:5173/api/orders/' + id)"
    assert "params: { id }" in author._missing_dynamic_params(bad, target)

    good = (
        "await getJson(GET, 'http://localhost:5173/api/orders/' + id, "
        "{ params: { id: String(id) } })"
    )
    assert author._missing_dynamic_params(good, target) == ""


def test_generated_tests_use_the_supported_objectid_factory():
    body = "const stored = new ObjectId(userId)"
    normalized = add_helper_imports(normalize_objectid_helpers(body))
    assert "new ObjectId" not in normalized
    assert "oid(userId)" in normalized
    assert "from '../../helpers/request.js'" in normalized


def test_parallel_unit_rounds_keep_good_files_and_isolate_bad_ones():
    def failure(path, name):
        return SimpleNamespace(test_file=path, name=name, target="")

    previous = [
        failure("tests/unit/a.test.js", "a1"),
        failure("tests/unit/a.test.js", "a2"),
        failure("tests/unit/b.test.js", "b1"),
        failure("tests/unit/c.test.js", "c1"),
    ]
    current = [
        failure("tests/unit/a.test.js", "a2"),
        failure("tests/unit/b.test.js", "b2"),
        failure("tests/unit/c.test.js", "c1"),
    ]
    improved, regressed, unchanged = server._round_file_outcomes(
        previous, current)
    assert improved == {"tests/unit/a.test.js"}
    assert regressed == {"tests/unit/b.test.js"}
    assert unchanged == {"tests/unit/c.test.js"}


def test_source_snapshot_restore_resynchronises_arch_memory():
    with TemporaryDirectory() as td:
        root = Path(td)
        path = root / "components" / "Card.jsx"
        path.parent.mkdir(parents=True)
        path.write_text("export default function Card(){return null}",
                        encoding="utf-8")
        arch = SimpleNamespace(files={"components/Card.jsx": "stale"})
        server._sync_restored_sources(arch, root, ["components/Card.jsx"])
        assert arch.files["components/Card.jsx"].startswith("export default")


def test_unit_repair_prompt_gets_mechanical_evidence_without_another_guess():
    failures = [SimpleNamespace(
        message='ReferenceError: ObjectId is not defined; role "form" missing',
        stack="", name="uses the route", dom="<form><button>Pay</button></form>",
        hint="", target="app/api/orders/[id]/route.js",
    )]
    notes = BugFixerAgent._mechanical_failure_notes(
        failures, failures[0].target)
    joined = "\n".join(notes)
    assert "oid(value)" in joined
    assert "querySelector('form')" in joined
    assert "params: { ... }" in joined


def test_shutdown_logging_falls_back_when_windows_console_rejects_emoji():
    error = UnicodeEncodeError("cp1252", "🛑", 0, 1, "not supported")
    with patch("builtins.print", side_effect=[error, None]) as mocked:
        server._shutdown_line("🛑 stopping")
    assert mocked.call_count == 2
    assert mocked.call_args_list[1].args == (" stopping",)
