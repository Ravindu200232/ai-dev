from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from agents.architect_next_rules import ArchitectNextRulesMixin
from agents.bugfixer import BugFixerAgent
from qa_agent.author import UnitTestAuthor
from qa_agent.spec import TestFailure as Failure


class _QA:
    def __init__(self, sources):
        self.sources = sources

    def read_source(self, rel):
        return self.sources.get(rel, "")


class _Rules(ArchitectNextRulesMixin):
    pass


def test_unit_author_preflight_rejects_invented_testid():
    with TemporaryDirectory() as td:
        sources = {
            "components/Card.jsx": (
                "export default function Card(){return <button "
                "data-testid=\"save-card\">Save</button>}"
            )
        }
        arch = SimpleNamespace(project_dir=Path(td), files=dict(sources))
        author = UnitTestAuthor(arch, session=_QA(sources))
        advice = author._quality_advice(
            "screen.getByTestId('delete-card')",
            "components/Card.jsx",
            "tests/unit/components/Card.test.jsx",
        )
        assert "delete-card" in advice
        assert "not in" in advice


def test_unit_author_selector_guard_sees_local_child_markup():
    with TemporaryDirectory() as td:
        sources = {
            "components/Panel.jsx": (
                "import Child from '@/components/Child'\n"
                "export default function Panel(){return <Child/>}"
            ),
            "components/Child.jsx": (
                "export default function Child(){return <button "
                "data-testid=\"child-action\">Go</button>}"
            ),
        }
        arch = SimpleNamespace(project_dir=Path(td), files=dict(sources))
        author = UnitTestAuthor(arch, session=_QA(sources))
        advice = author._quality_advice(
            "screen.getByTestId('child-action')",
            "components/Panel.jsx",
            "tests/unit/components/Panel.test.jsx",
        )
        assert advice == ""


def test_bugfixer_keeps_mechanical_selector_failure_on_test_side():
    with TemporaryDirectory() as td:
        sources = {
            "components/Card.jsx": (
                "export default function Card(){return <button>Save</button>}"
            ),
            "tests/unit/components/Card.test.jsx": (
                "import Card from '@/components/Card'\n"
                "screen.getByTestId('missing')"
            ),
        }
        arch = SimpleNamespace(project_dir=Path(td), files=dict(sources),
                               NEXT_PROTECTED=frozenset())
        fixer = BugFixerAgent(arch, session=_QA(sources))
        failure = Failure(
            test_file="tests/unit/components/Card.test.jsx",
            target="components/Card.jsx",
            name="finds control",
            message='Unable to find an element by: [data-testid="missing"]',
            kind="ASSERTION",
        )
        verdict, reason, action = fixer.prior([failure], round_no=3)
        assert verdict == "test"
        assert "generated-test mechanics" in reason
        assert action == "model"


def test_builder_contract_lint_catches_data_and_auth_chrome_before_analyzer_stage():
    with TemporaryDirectory() as td:
        root = Path(td)
        files = {
            "app/page.jsx": "export default function Page(){return <main>Home</main>}",
            "app/login/page.jsx": "export default function Login(){return <main>Login</main>}",
            "app/layout.jsx": (
                "import Navbar from '@/components/Navbar'\n"
                "export default function RootLayout({children}){return "
                "<html><body><Navbar/>{children}</body></html>}"
            ),
            "components/Navbar.jsx": "export default function Navbar(){return <nav>Nav</nav>}",
        }
        for rel, body in files.items():
            fp = root / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(body, encoding="utf-8")

        rules = _Rules()
        rules.stack = "next"
        rules.project_dir = root
        rules.files = dict(files)
        rules.write_seq = 1
        rules.plan_md = ""
        rules.plan = {
            "phases": [{"files": [
                {"path": "app/page.jsx", "reads": ["rooms"]},
                {"path": "app/login/page.jsx"},
                {"path": "app/layout.jsx"},
                {"path": "components/Navbar.jsx"},
            ]}],
            "workflows": [],
            "contracts": [],
        }
        problems = rules.lint_plan_contracts()
        joined = "\n".join(problems)
        assert "LAYOUT_CHROME" in joined
        assert "MISSING_PLANNED_DATA" in joined


def test_builder_prompt_prioritizes_first_write_contracts():
    from agents.architect_next_builder_prompt_a import PROMPT_PART_A

    assert "FIRST-WRITE ACCEPTANCE GATE" in PROMPT_PART_A
    assert "Never leave a planned data edge for" in PROMPT_PART_A
    assert "NEVER render Navbar/Header/Sidebar" in PROMPT_PART_A


def test_plan_normalizer_preserves_first_write_invariants():
    from agents.architect_plan_normalize import ArchitectPlanNormalizeMixin

    class N(ArchitectPlanNormalizeMixin):
        def _infer_kind(self, _path):
            return "server"
        def _log(self, *_args, **_kwargs):
            pass

    plan = N()._normalise_plan({"tasks": [{"files": [{
        "path": "lib/seed.js",
        "invariants": ["open all collection handles before first use"],
    }]}]})
    seed = next(f for f in plan["phases"][0]["files"] if f["path"] == "lib/seed.js")
    assert seed["invariants"] == ["open all collection handles before first use"]


def test_builder_lint_catches_runtime_only_undefined_collection_handle():
    rules = _Rules()
    rules.stack = "next"
    rules.project_dir = Path(".")
    rules.files = {
        "lib/seed.js": "async function doSeed(){ const reviewsCol = await getCollection('reviews'); const b = await bookingsCol.findOne({}); return reviewsCol && b }"
    }
    problems = rules._undefined_collection_handles("lib/seed.js", rules.files["lib/seed.js"])
    assert any("bookingsCol" in p and "ReferenceError" in p for p in problems)


def test_workflow_action_prefers_explicit_planned_testid():
    from agents.action_ids import workflow_actions

    plan = {
        "phases": [{"files": [{
            "path": "app/my-bookings/page.jsx",
            "actions": ["click 'Submit' sends the review — testid=submit-review"],
        }]}],
        "workflows": [{
            "name": "Tracking", "who": "guest",
            "steps": ["/my-bookings — click 'Submit' — review appears"],
        }],
    }
    rows = workflow_actions(plan)
    assert rows[0]["testid"] == "submit-review"


def test_prompts_include_seed_graph_and_runtime_symbol_closure():
    from agents.architect_next_builder_prompt_a import PROMPT_PART_A as BUILD_A
    from agents.architect_next_builder_prompt_b import PROMPT_PART_B as BUILD_B
    from agents.architect_next_planner_prompt_b import PROMPT_PART_B as PLAN_B

    assert "SYMBOL CLOSURE" in BUILD_A
    assert "SEEDING IS A DEPENDENCY GRAPH" in BUILD_B
    assert "RELATIONSHIP GRAPH" in PLAN_B
    assert "ORDER BY DEPENDENCY" in PLAN_B


def test_builder_lint_catches_missing_known_runtime_helper_import():
    rules = _Rules()
    body = (
        "export default async function RoomPage(){\n"
        "  const user = await getSessionUser()\n"
        "  return <main>{user?.name}</main>\n"
        "}\n"
    )
    problems = rules._undefined_runtime_helpers("app/rooms/[id]/page.jsx", body)
    assert any("getSessionUser" in p and "@/lib/auth" in p and "ReferenceError" in p
               for p in problems)


def test_exact_runtime_reference_error_becomes_source_backed_repair_spec():
    from agents.runtime_repair_spec import exact_runtime_repair_spec

    body = (
        "import { getCollection, serialize } from '@/lib/mongodb'\n"
        "export default async function RoomDetailPage(){\n"
        "  const rooms = await getCollection('rooms')\n"
        "  const room = serialize(await rooms.findOne({}))\n"
        "  const user = await getSessionUser()\n"
        "  return <main>{room?.name}{user?.name}</main>\n"
        "}\n"
    )
    arch = SimpleNamespace(files={"app/rooms/[id]/page.jsx": body})
    evidence = (
        "PageError: ReferenceError: getSessionUser is not defined\n"
        "at RoomDetailPage (app/rooms/[id]/page.jsx:5:22)"
    )
    spec = exact_runtime_repair_spec(
        arch, evidence, focus_paths=["app/rooms/[id]/page.jsx"])
    assert spec is not None and not spec.is_empty()
    assert spec.files[0]["path"] == "app/rooms/[id]/page.jsx"
    assert "@/lib/auth" in spec.files[0]["why"]
    assert spec.context["confidence"] == "high"
    assert spec.context["evidence"][0]["path"] == "app/rooms/[id]/page.jsx"


def test_e2e_source_bundle_contains_page_child_helper_and_called_api():
    from qa_agent.e2e import E2EAgent

    with TemporaryDirectory() as td:
        root = Path(td)
        files = {
            "app/checkout/page.jsx": (
                "import CheckoutForm from '@/components/CheckoutForm'\n"
                "import { getSessionUser } from '@/lib/auth'\n"
                "export default async function Page(){ await getSessionUser(); return <CheckoutForm/> }"
            ),
            "components/CheckoutForm.jsx": (
                "'use client'\n"
                "export default function CheckoutForm(){ async function save(){ "
                "await fetch('/api/bookings',{method:'POST'}) } return <button onClick={save}>Pay</button> }"
            ),
            "lib/auth.js": "export async function getSessionUser(){ return {id:'u1'} }",
            "app/api/bookings/route.js": (
                "import { getCollection } from '@/lib/mongodb'\n"
                "export async function POST(){ const c=await getCollection('bookings'); return Response.json({ok:true}) }"
            ),
            "lib/mongodb.js": "export async function getCollection(name){ return globalThis.db.collection(name) }",
        }
        for rel, body in files.items():
            fp = root / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(body, encoding="utf-8")

        class AZ:
            def enumerate_routes(self):
                return {
                    "/checkout": {"kind": "page", "file": "app/checkout/page.jsx", "dynamic": False},
                    "/api/bookings": {"kind": "api", "file": "app/api/bookings/route.js", "dynamic": False},
                }

        arch = SimpleNamespace(project_dir=root, files=files, plan={})
        agent = E2EAgent(arch, root, analyzer=AZ())
        agent.entry_page = lambda: ""
        agent.landing_page = lambda: "app/checkout/page.jsx"
        bundle = agent.journey_source_bundle(
            {"title": "Booking", "steps": ["/checkout — click Pay — booking created"],
             "role": "", "contract": {"source_files": ["app/checkout/page.jsx"], "handoffs": []}},
        )
        assert "--- app/checkout/page.jsx ---" in bundle
        assert "--- components/CheckoutForm.jsx ---" in bundle
        assert "--- lib/auth.js ---" in bundle
        assert "--- app/api/bookings/route.js ---" in bundle
        assert "--- lib/mongodb.js ---" in bundle
