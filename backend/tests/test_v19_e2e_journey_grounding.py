"""v19 — the journey is written against the app that shipped, and the whole
prompt reaches the model that writes it.

Every case here pins one measured way a correctly generated app still failed
its end-to-end gate: the journey was decided before the code was read, the
proof contract named files the build never produced, the route table was cut
off at forty rows, the prompt was three times the context window it was sent
into, and the grounding check judged the scenario against a smaller view of
the app than the author had been given.
"""
import importlib
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from qa_agent.e2e import E2EAgent
from qa_agent.e2e_common import SYSTEM
from qa_agent.e2e_contract import capability_contract
from qa_agent.flows import Scenario, Selector, Step


class Arch:
    """Enough architect for the E2E agent, with the context window it reports."""

    def __init__(self, root, *, files=None, plan=None, plan_md="",
                 num_ctx=16_384, qa_ctx=None):
        self.project_dir = Path(root)
        self.files = files or {}
        self.plan = plan or {}
        self.plan_md = plan_md
        self.convo = []
        self.model = "local-build"
        self.num_ctx = num_ctx
        self._qa_ctx = qa_ctx
        self.prompts = []

    def ctx_for(self, model=""):
        if model and self._qa_ctx and model != self.model:
            return self._qa_ctx
        return self.num_ctx

    def _stream(self, convo, on_delta, **kw):
        self.prompts.append(convo[-1]["content"])
        on_delta("TITLE :: demo\nGOTO :: /\n")


class AZ:
    def __init__(self, routes):
        self.routes = routes

    def enumerate_routes(self):
        return self.routes


def agent_for(root, **kw):
    routes = kw.pop("routes", None)
    qa_model = kw.pop("qa_model", "")
    arch = Arch(root, **kw)
    return E2EAgent(arch, Path(root),
                    analyzer=AZ(routes) if routes else None,
                    session=SimpleNamespace(model=qa_model) if qa_model else None)


class PreJourneyPreparationTests(unittest.TestCase):
    """The code is loaded and repaired before `journeys()` decides anything."""

    def test_source_is_loaded_repaired_and_reloaded_before_journeys(self):
        server = importlib.import_module("server")
        trace = []

        class Arch2:
            project_dir = None
            files = {"app/page.jsx": "export default function P(){return null}"}

            def load_existing(self):
                trace.append("load")

        class Analyzer:
            def scan(self):
                trace.append("scan")
                return SimpleNamespace(findings=[
                    SimpleNamespace(severity="blocker", code="DEAD_LINK",
                                    path="app/page.jsx")])

            def repair(self, report):
                trace.append("repair")
                return 1

        class Agent:
            invalidated = False

            def invalidate_runtime_evidence(self):
                trace.append("invalidate")
                self.invalidated = True

        with TemporaryDirectory() as td:
            arch = Arch2()
            arch.project_dir = Path(td)
            (Path(td) / "app").mkdir()
            (Path(td) / "app" / "page.jsx").write_text("x", encoding="utf-8")
            agent = Agent()
            paths, baseline = server._prepare_app_before_journeys(
                agent, arch, Path(td), Analyzer())

        # Read, repair, read again — in that order, before any journey exists.
        self.assertEqual(["load", "scan", "repair", "load", "invalidate"], trace)
        self.assertEqual(["app/page.jsx"], paths)
        self.assertTrue(agent.invalidated)
        self.assertIsNotNone(baseline)

    def test_journeys_are_decided_after_the_preparation_step(self):
        stage = (Path("server_modules/qa/e2e_stage.py")
                 .read_text(encoding="utf-8"))
        prepare = stage.index("_prepare_app_before_journeys(")
        decide = stage.index("journeys = agent.journeys()")
        self.assertLess(prepare, decide)


class ContractShippedFilesTests(unittest.TestCase):
    """A planned file the builder skipped is named as absent, not as evidence."""

    def test_contract_separates_generated_files_from_planned_ones(self):
        with TemporaryDirectory() as td:
            arch = Arch(td, files={"app/rooms/page.jsx": "export default ()=>null"},
                        plan={"capabilities": [{
                            "id": "C1", "who": "admin", "requirement": "edit a room",
                            "proof": "the row changes",
                            "files": ["app/rooms/page.jsx", "app/admin/rooms/page.jsx"],
                        }]})
            contract = capability_contract(
                arch, {"title": "Price Management", "steps": ["update a room price"],
                       "role": "admin", "covers": ["C1"]})

        self.assertEqual(["app/rooms/page.jsx"], contract["source_files"])
        self.assertEqual(["app/admin/rooms/page.jsx"],
                         contract["missing_source_files"])

    def test_a_file_only_on_disk_still_counts_as_shipped(self):
        with TemporaryDirectory() as td:
            fp = Path(td) / "app" / "orders" / "page.jsx"
            fp.parent.mkdir(parents=True)
            fp.write_text("export default ()=>null", encoding="utf-8")
            arch = Arch(td, plan={"capabilities": [{
                "id": "C1", "requirement": "see orders", "proof": "a row",
                "files": ["app/orders/page.jsx"]}]})
            contract = capability_contract(
                arch, {"title": "Orders", "steps": ["open orders"],
                       "role": "", "covers": ["C1"]})

        self.assertEqual(["app/orders/page.jsx"], contract["source_files"])
        self.assertEqual([], contract["missing_source_files"])


class JourneyRouteGroundingTests(unittest.TestCase):
    """Routes the plan invented are named, never silently walked into."""

    ROUTES = {
        "/": {"kind": "page", "file": "app/page.jsx", "dynamic": False},
        "/admin/dashboard": {"kind": "page", "file": "app/admin/dashboard/page.jsx",
                             "dynamic": False},
        "/rooms/[id]": {"kind": "page", "file": "app/rooms/[id]/page.jsx",
                        "dynamic": True},
    }

    def test_planned_route_the_app_does_not_serve_is_reported(self):
        with TemporaryDirectory() as td:
            agent = agent_for(td, routes=self.ROUTES)
            journey = agent.ground_journey_routes({
                "title": "Price Management",
                "steps": ["go to /admin/rooms", "then reach /rooms/7"]})

        self.assertIn("/rooms/7", journey["served_routes"])
        self.assertEqual(["/admin/rooms"], journey["unserved_routes"])

    def test_a_url_inside_an_absolute_link_is_not_called_a_route(self):
        with TemporaryDirectory() as td:
            agent = agent_for(td, routes=self.ROUTES)
            journey = agent.ground_journey_routes(
                {"title": "Visit", "steps": ["open https://example.com/pricing"]})

        self.assertEqual([], journey["unserved_routes"])

    def test_journeys_carry_the_grounding_and_the_contract(self):
        with TemporaryDirectory() as td:
            agent = agent_for(
                td, routes=self.ROUTES,
                plan={"workflows": [{"name": "Booking", "who": "guest",
                                     "steps": ["go to /admin/rooms"]}],
                      "demo_accounts": [{"email": "g@x.io", "password": "pw",
                                         "role": "guest"}]})
            journeys = agent.journeys()

        first = journeys[0]
        self.assertEqual("Booking", first["title"])
        self.assertEqual(["/admin/rooms"], first["unserved_routes"])
        self.assertIn("missing_source_files", first["contract"])


class RouteTableTests(unittest.TestCase):
    """The author is shown the whole route table, or told what was cut."""

    @staticmethod
    def many_routes(n):
        routes = {f"/p{i:03d}": {"kind": "page", "file": f"app/p{i:03d}/page.jsx",
                                 "dynamic": False} for i in range(n)}
        routes["/api/orders"] = {"kind": "api", "file": "app/api/orders/route.js",
                                 "dynamic": False, "methods": ["GET", "POST"]}
        return routes

    def test_sixty_routes_are_no_longer_cut_to_forty(self):
        with TemporaryDirectory() as td:
            agent = agent_for(td, routes=self.many_routes(60))
            table = agent._routes()

        self.assertIn("/p000", table)
        self.assertIn("/p059", table)
        self.assertEqual(61, len(table.splitlines()))

    def test_api_methods_are_named(self):
        with TemporaryDirectory() as td:
            agent = agent_for(td, routes=self.many_routes(1))
            self.assertIn("/api/orders  (api)  [GET, POST]", agent._routes())

    def test_a_capped_table_says_how_much_it_left_out(self):
        with TemporaryDirectory() as td:
            agent = agent_for(td, routes=self.many_routes(60))
            table = agent._routes(cap=10)

        self.assertIn("and 51 more route(s)", table)

    def test_routes_are_read_off_disk_without_an_analyzer(self):
        with TemporaryDirectory() as td:
            for rel in ("app/page.jsx", "app/orders/page.jsx",
                        "app/api/orders/route.js"):
                fp = Path(td) / rel
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text("export default ()=>null", encoding="utf-8")
            agent = agent_for(td)
            table = agent._routes()

        self.assertIn("/  (page)", table)
        self.assertIn("/orders  (page)", table)
        self.assertIn("/api/orders  (api)", table)


class PromptBudgetTests(unittest.TestCase):
    """The prompt is sized for the model that receives it."""

    def test_budget_follows_the_qa_model_not_the_build_model(self):
        with TemporaryDirectory() as td:
            local = agent_for(td)
            self.assertLess(local.prompt_budget_chars(), 40_000)

            arch = Arch(td, num_ctx=16_384, qa_ctx=131_072)
            cloud = E2EAgent(arch, Path(td),
                             session=SimpleNamespace(model="big-cloud"))
            self.assertGreater(cloud.prompt_budget_chars(), 200_000)

    def test_a_local_authoring_prompt_fits_the_context_window(self):
        with TemporaryDirectory() as td:
            agent = self._loaded_agent(td, num_ctx=16_384)
            agent.author(journey=self._journey())
            prompt = agent.arch.prompts[-1]

        budget = agent.prompt_budget_chars()
        self.assertLessEqual(len(prompt) + len(SYSTEM), budget)
        # The head is what used to be dropped: the journey and the routes.
        self.assertIn("THE JOURNEY THIS SCENARIO WALKS", prompt)
        self.assertIn("Pages and endpoints it serves", prompt)

    def test_a_cloud_prompt_keeps_the_whole_source_bundle(self):
        with TemporaryDirectory() as td:
            agent = self._loaded_agent(td, num_ctx=16_384, qa_ctx=131_072,
                                       qa_model="big-cloud")
            self.assertGreater(agent.prompt_budget_chars(), 200_000)
            agent.author(journey=self._journey())
            prompt = agent.arch.prompts[-1]

        self.assertIn("JOURNEY SOURCE CONTRACT", prompt)
        self.assertNotIn("did not fit the model context window", prompt)

    def test_planned_but_missing_files_are_named_in_the_prompt(self):
        with TemporaryDirectory() as td:
            agent = self._loaded_agent(td, num_ctx=16_384, qa_ctx=131_072,
                                       qa_model="big-cloud")
            journey = self._journey()
            journey["contract"] = {"missing_source_files": ["app/admin/rooms/page.jsx"]}
            agent.author(journey=journey)
            prompt = agent.arch.prompts[-1]

        self.assertIn("PLANNED FILES THIS BUILD DID NOT PRODUCE", prompt)
        self.assertIn("app/admin/rooms/page.jsx", prompt)

    def test_unserved_routes_are_named_in_the_prompt(self):
        with TemporaryDirectory() as td:
            agent = self._loaded_agent(td, num_ctx=16_384, qa_ctx=131_072,
                                       qa_model="big-cloud")
            journey = self._journey()
            journey["steps"] = ["go to /rooms", "go to /admin/rooms"]
            agent.author(journey=journey)
            prompt = agent.arch.prompts[-1]

        self.assertIn("ROUTES THIS JOURNEY NAMES THAT THE APP DOES NOT SERVE",
                      prompt)
        self.assertIn("/admin/rooms", prompt)

    def test_a_route_an_earlier_journey_created_is_no_longer_called_missing(self):
        """A repair between journeys must not leave a stale "does not serve"."""
        with TemporaryDirectory() as td:
            agent = self._loaded_agent(td, num_ctx=16_384, qa_ctx=131_072,
                                       qa_model="big-cloud")
            journey = self._journey()
            journey["steps"] = ["go to /rooms", "go to /admin/rooms"]
            agent.ground_journey_routes(journey)
            self.assertEqual(["/admin/rooms"], journey["unserved_routes"])

            # An earlier journey's repair writes the page the plan wanted.
            agent.az.routes["/admin/rooms"] = {
                "kind": "page", "file": "app/admin/rooms/page.jsx",
                "dynamic": False}
            agent.author(journey=journey)
            prompt = agent.arch.prompts[-1]

        self.assertEqual([], journey["unserved_routes"])
        self.assertNotIn("DOES NOT SERVE", prompt)

    def test_a_file_an_earlier_journey_created_is_no_longer_called_missing(self):
        with TemporaryDirectory() as td:
            agent = self._loaded_agent(td, num_ctx=16_384, qa_ctx=131_072,
                                       qa_model="big-cloud")
            journey = self._journey()
            journey["contract"] = {
                "source_files": [],
                "missing_source_files": ["app/admin/rooms/page.jsx"]}

            fp = Path(td) / "app" / "admin" / "rooms" / "page.jsx"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text("export default ()=>null", encoding="utf-8")

            agent.author(journey=journey)
            prompt = agent.arch.prompts[-1]

        self.assertEqual([], journey["contract"]["missing_source_files"])
        self.assertEqual(["app/admin/rooms/page.jsx"],
                         journey["contract"]["source_files"])
        self.assertNotIn("DID NOT PRODUCE", prompt)

    def test_the_journeys_own_component_outranks_an_unrelated_page(self):
        """The form the journey fills beats a marketing page it never opens."""
        with TemporaryDirectory() as td:
            agent = self._loaded_agent(td, num_ctx=16_384)
            journey = self._journey()
            agent.ground_journey_routes(journey)
            bundle = agent.journey_source_bundle(journey, budget=12_000)

        included = re.findall(r"--- (.+?) ---", bundle)
        self.assertEqual("app/rooms/page.jsx", included[0])
        self.assertIn("components/BookingForm.jsx", included)
        self.assertNotIn("app/p00/page.jsx", included[:2])

    def test_the_testid_allowlist_is_never_dropped_for_evidence(self):
        with TemporaryDirectory() as td:
            agent = self._loaded_agent(td, num_ctx=16_384)
            agent.author(journey=self._journey())
            prompt = agent.arch.prompts[-1]

        # The rule that forbids inventing a testid is useless without the list.
        self.assertIn("Controls this app labels for you", prompt)
        self.assertIn("Never invent one that is not on", prompt)

    def test_a_rewrite_keeps_the_markup_the_note_tells_it_to_match(self):
        with TemporaryDirectory() as td:
            agent = self._loaded_agent(td, num_ctx=16_384)
            agent.author(journey=self._journey())
            first = agent.arch.prompts[-1]
            agent.author(previous=Scenario(title="x",
                                           steps=[Step("GOTO", value="/")]),
                         why="the button did not match",
                         journey=self._journey())
            rewrite = agent.arch.prompts[-1]
            budget = agent.prompt_budget_chars()

        for prompt in (first, rewrite):
            self.assertLessEqual(len(prompt) + len(SYSTEM), budget)
            self.assertIn("## The real markup", prompt)
            self.assertIn("Controls this app labels for you", prompt)
        self.assertIn("Your previous scenario did not work", rewrite)

    @staticmethod
    def _journey():
        return {"title": "Booking", "steps": ["go to /rooms", "reserve one"],
                "role": "", "covers": [], "contract": {}}

    @staticmethod
    def _loaded_agent(td, **kw):
        files = {f"components/C{i:02d}.jsx": "x" * 3_000 for i in range(12)}
        files["components/BookingForm.jsx"] = (
            "export default function F(){ return <form>\n"
            '  <input name="fullName" data-testid="full-name" />\n'
            + "  <span>detail</span>\n" * 120 + "</form> }\n")
        files["app/rooms/page.jsx"] = (
            "import BookingForm from '@/components/BookingForm'\n"
            '<div data-testid="room-row" />\n'
            + "// room page\n" * 400)
        for rel, body in files.items():
            fp = Path(td) / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(body, encoding="utf-8")
        routes = {"/rooms": {"kind": "page", "file": "app/rooms/page.jsx",
                             "dynamic": False}}
        agent = agent_for(td, routes=routes, files=files,
                          plan_md="A hotel booking app." + " detail." * 400, **kw)
        agent.action_id_block = lambda journey=None: ""
        agent.runtime_evidence = lambda journey=None, limit=4: "DOM /rooms\n" + "row\n" * 2_000
        return agent


class GroundingSymmetryTests(unittest.TestCase):
    """The gate judges the scenario against what the author was actually shown."""

    def test_a_field_taken_from_the_source_bundle_is_not_called_invented(self):
        with TemporaryDirectory() as td:
            agent = agent_for(td)
            agent.runtime_evidence = lambda journey=None, limit=4: ""
            agent._journey_pages = lambda journey=None: []
            agent.journey_source_bundle = lambda journey=None, page="": (
                '--- components/BookingForm.jsx ---\n'
                '<input name="fullName" placeholder="Your full name" />')
            sc = Scenario(title="booking", steps=[
                Step("GOTO", value="/book"),
                Step("FILL", Selector(kind="field", pattern="name"), "Ravindu"),
            ])
            issue = agent.grounding_issue(
                sc, {"title": "Booking", "steps": ["reserve a room"]})

        self.assertEqual("", issue)

    def test_a_field_in_no_evidence_at_all_is_still_rejected(self):
        with TemporaryDirectory() as td:
            agent = agent_for(td)
            agent.runtime_evidence = lambda journey=None, limit=4: (
                "fields: input type=email name=email")
            agent._journey_pages = lambda journey=None: []
            agent.journey_source_bundle = lambda journey=None, page="": (
                '--- app/book/page.jsx ---\n<input type="email" name="email" />')
            sc = Scenario(title="booking", steps=[
                Step("GOTO", value="/book"),
                Step("FILL", Selector(kind="field", pattern="phone"), "0771234567"),
            ])
            issue = agent.grounding_issue(
                sc, {"title": "Booking", "steps": ["reserve a room"]})

        self.assertIn("ungrounded form field", issue)
        self.assertIn("field=phone", issue)


class BorrowedModelContextTests(unittest.TestCase):
    """A call on a borrowed model asks for that model's context window."""

    def test_a_qa_model_is_not_sent_the_build_models_window(self):
        from agents import architect_runtime as ar

        arch = object.__new__(ar.ArchitectRuntimeMixin)
        arch.model = "local-build"
        arch.num_ctx = 16_384
        arch._ctx_cache = {"local-build": 16_384}

        real = ar.max_context
        ar.max_context = lambda name: 131_072 if name == "big-cloud" else 4_096
        try:
            self.assertEqual(16_384, arch.ctx_for(""))
            self.assertEqual(16_384, arch.ctx_for("local-build"))
            self.assertEqual(131_072, arch.ctx_for("big-cloud"))
            # Asked once per model, then remembered.
            ar.max_context = lambda name: (_ for _ in ()).throw(AssertionError)
            self.assertEqual(131_072, arch.ctx_for("big-cloud"))
        finally:
            ar.max_context = real

    def test_the_stream_options_carry_the_borrowed_window(self):
        source = (Path("agents/architect_runtime.py")
                  .read_text(encoding="utf-8"))
        self.assertIn('"num_ctx": self.ctx_for(model)', source)
        self.assertNotIn('"num_ctx": self.num_ctx', source)


if __name__ == "__main__":
    unittest.main()
