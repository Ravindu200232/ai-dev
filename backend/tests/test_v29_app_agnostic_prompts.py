"""Every prompt must use the app's own language."""
import re
import tempfile
import unittest
from pathlib import Path

from agents.planner.app_vocab import Vocab, derive, plural, render, singular
from agents.builder.orchestration.agent import ArchitectAgent
from agents.builder.orchestration.turns import ArchitectTurnMixin
from qa_agent.e2e_grounding import E2EGroundingMixin
from qa_agent.flows import FIELD_CSS

# Industry terms that must not be baked into prompts.
DOMAIN = re.compile(
    r"(?i)(?<!-)\b(hotels?|bookings?|reservations?|guests?|nightly|"
    r"restaurants?|cafes?|bistro|dishes?|cuisine|"
    r"doctors?|patients?|clinics?|nurses?|appointments?|prescriptions?|"
    r"courses?|students?|teachers?|lecturers?|"
    r"flights?|passengers?|movies?|cinemas?|gyms?|trainers?|"
    r"landlords?|tenants?|pets?|dogs?|vets?|shops?|products?|carts?)\b")

AMBIGUOUS = re.compile(
    r"(?i)(?<!clean-)\brooms?\b(?! *\(\))|"
    r"(?<!router )(?<!testing-)(?<!a )(?<!no )\blibrar(?:y|ies)\b")

PROMPT_FILES = [
    "agents/planner/prompt_a.py",
    "agents/planner/prompt_b.py",
    "agents/builder/prompts/part_a.py",
    "agents/builder/prompts/part_b.py",
]

IDEAS = [
    "a clinic app where patients book appointments with doctors",
    "an online bookshop, customers browse books and place orders",
    "a school portal: students enrol in courses taught by teachers",
    "hotel management with rooms and bookings for guests",
    "a gym where members reserve classes with trainers",
    "a plain crud tool",
]


def architect_for(idea, plan=None):
    a = ArchitectAgent.__new__(ArchitectAgent)
    a.plan = plan or {}
    a.user_prompt = idea
    a._vocab_cache = None
    a._app_noun_cache = None
    a.stack = "next"
    a.project_dir = Path(tempfile.gettempdir(), "agentforge-v29-tests")
    a.cb = None
    return a


class VocabularyTests(unittest.TestCase):
    def test_it_picks_the_nouns_the_request_pluralises(self):
        v = derive("a clinic app where patients book appointments with doctors")
        self.assertEqual({v.things, v.children}, {"appointments", "doctors"})
        self.assertEqual(v.actor, "patient")

    def test_the_plan_outranks_the_idea_once_it_exists(self):
        v = derive("some vague idea", {
            "collections": ["users", "invoices", "clients", "audit_logs"],
            "signup_role": "client"})
        self.assertEqual(v.things, "invoices")
        self.assertEqual(v.actor, "client")

    def test_infrastructure_collections_are_never_the_subject(self):
        v = derive("", {"collections": ["users", "sessions", "roles", "tickets"]})
        self.assertEqual(v.things, "tickets")

    def test_an_app_that_says_nothing_gets_neutral_nouns(self):
        v = derive("", {})
        self.assertEqual((v.things, v.children, v.actor),
                         ("items", "entries", "user"))

    def test_plurals_survive_the_awkward_words(self):
        self.assertEqual(plural("class"), "classes")
        self.assertEqual(plural("box"), "boxes")
        self.assertEqual(plural("category"), "categories")
        self.assertEqual(plural("orders"), "orders")
        self.assertEqual(singular("classes"), "class")

    def test_render_fills_only_the_vocabulary_slots(self):
        out = render("<<things>> and <<children>> for the <<actor>>; {other} stays",
                     Vocab(things="books", children="orders", actor="reader"))
        self.assertEqual(out, "books and orders for the reader; {other} stays")


# Code-shaped context: identifiers, paths, JSON, quoted names.
CODE_CONTEXT = re.compile(r"`[^`\n]{1,120}`|\"[^\"\n]{1,80}\"|'[^'\n]{1,80}'"
                          r"|(?:app|components|lib)/[\w./\[\]-]+")


class PromptNeutralityTests(unittest.TestCase):
    def test_no_prompt_file_puts_an_industry_into_a_code_example(self):
        for rel in PROMPT_FILES:
            text = Path(rel).read_text(encoding="utf-8")
            for span in CODE_CONTEXT.findall(text):
                found = sorted({m.group(0).lower() for m in DOMAIN.finditer(span)})
                self.assertEqual(found, [], f"{rel} templates {found} in {span!r}")

    def test_no_single_industry_dominates_the_prose(self):
        text = "".join(Path(rel).read_text(encoding="utf-8")
                       for rel in PROMPT_FILES)
        counts = {}
        for m in DOMAIN.finditer(text):
            w = singular(m.group(0).lower())
            counts[w] = counts.get(w, 0) + 1
        heavy = {w: n for w, n in counts.items() if n > 2}
        self.assertEqual(heavy, {}, f"prompts lean on {heavy}")

    def test_every_app_gets_prompts_in_its_own_words(self):
        for idea in IDEAS:
            arch = architect_for(idea)
            vocab = arch.vocab
            both = arch._planner_sys() + arch._builder_sys()
            own = {vocab.thing, vocab.things, vocab.child,
                   vocab.children, vocab.actor}
            for span in CODE_CONTEXT.findall(both):
                leaks = {m.group(0).lower()
                         for m in DOMAIN.finditer(span)} - own
                self.assertEqual(leaks, set(),
                                 f"{idea!r} templated {sorted(leaks)}")

    def test_no_vocabulary_slot_is_left_unfilled(self):
        arch = architect_for(IDEAS[0])
        both = arch._planner_sys() + arch._builder_sys()
        self.assertEqual(re.findall(r"<<(?:thing|things|child|children|actor)>>",
                                    both), [])

    def test_the_app_own_nouns_reach_the_prompt(self):
        arch = architect_for("an online bookshop where customers order books")
        text = arch._planner_sys()
        self.assertIn(arch.vocab.things, text)
        self.assertIn(arch.vocab.actor, text)


class AgentDeHardcodingTests(unittest.TestCase):
    def test_global_lessons_drop_project_specific_fixes(self):
        from agents.repair.lessons import prompt_block, record

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "lessons.json")
            entries = [
                ("MISSING_PLANNED_DATA",
                 "read rooms, then call app/api/bookings/route.js"),
                ("CUSTOM_PROJECT_FIX", "render the hotel's rooms"),
            ]
            record("one", entries, path)
            record("two", entries, path)
            block = prompt_block(path)

        self.assertIn("Wire each planned read", block)
        self.assertNotRegex(block, r"(?i)hotel|rooms|bookings")
        self.assertNotIn("CUSTOM_PROJECT_FIX", block)

    def test_transport_ids_are_detected_by_origin_not_domain_name(self):
        from agents.gates.analyzer_ui import AnalyzerUIMixin

        class Probe(AnalyzerUIMixin):
            def plan_text(self):
                return "## Data Model\n- `parent_ref`: ObjectId\n"

            def code_files(self):
                return {
                    "app/api/items/route.js": """
const refValue = searchParams.get('ref')
const row = await col.findOne({ parent_ref: refValue })
"""
                }

        findings = Probe().mongo_id_type_findings()
        self.assertEqual([f.code for f in findings], ["MONGO_ID_TYPE"])

    def test_generic_checks_name_no_hotel_fields(self):
        files = [
            "agents/gates/analyzer_ui.py",
            "agents/builder/prompts/part_b.py",
            "qa_agent/author_common.py",
            "qa_agent/flows.py",
            "server_modules/agent/picture/images.py",
        ]
        text = "\n".join(Path(rel).read_text(encoding="utf-8") for rel in files)
        leaked = re.findall(
            r"(?i)roomId|room_type|guestCount|nightlyRate|BookingForm", text)
        self.assertEqual(leaked, [])

    def test_short_domain_words_survive_the_capability_stopword_filter(self):
        arch = architect_for("clinic app: patients book appointments with doctors")
        self.assertIn("doctors", arch._app_nouns())
        missing, _, _ = arch._capability_gaps(
            {"capabilities": [{"id": "CAP-1", "who": "patient",
                               "requirement": "patient can book an appointment",
                               "proof": "the row persists"}]},
            "# X\n## Core Features\n- Patients book appointments\n- Admin sets pricing\n")
        self.assertEqual(missing, ["Admin sets pricing"])

    def test_a_list_page_is_recognised_in_any_domain(self):
        for phrase in ("a page that lists all appointments", "the doctors table",
                       "invoices grid", "the classes feed", "inventory results"):
            self.assertTrue(ArchitectTurnMixin.LIST_RE.search(phrase), phrase)
        self.assertFalse(ArchitectTurnMixin.LIST_RE.search("a login form"))

    def test_qa_field_aliases_carry_no_industry_word(self):
        blob = " ".join(w for group in E2EGroundingMixin._FIELD_ALIASES.values()
                        for w in group) + " " + " ".join(FIELD_CSS)
        self.assertIsNone(DOMAIN.search(blob))

    def test_role_detection_covers_titles_no_list_could_name(self):
        from agents.builder.scaffolding.base import ArchitectScaffoldMixin
        rx = ArchitectScaffoldMixin.PRIVILEGED_ROLE
        for known in ("admin", "manager", "cashier", "doctor", "librarian"):
            self.assertTrue(rx.search(known), known)
        for unseen in ("pharmacist", "dispatcher", "warden", "curator",
                       "storekeeper", "engineer"):
            self.assertTrue(rx.search(unseen), unseen)
        for public in ("customer", "patient", "student", "guest", "member",
                       "visitor", "buyer", "reader"):
            self.assertFalse(rx.search(public), public)


class NoSingleAppCarriesThePromptTests(unittest.TestCase):
    """No industry may take over shared prompts."""

    FILES = PROMPT_FILES + [
        "agents/builder/prompts/stack.py", "qa_agent/e2e_common.py",
        "qa_agent/e2e_journeys.py", "qa_agent/author_common.py",
        "qa_agent/harness_install.py", "server_modules/qa/verification.py",
        "agents/feature/common.py", "agents/core/workspace.py",
        "agents/repair/common.py", "qa_agent/debugger_common.py",
        "qa_agent/debugger_investigate.py", "qa_agent/flows.py",
    ]

    def _text(self):
        return "".join(Path(rel).read_text(encoding="utf-8")
                       for rel in self.FILES if Path(rel).exists())

    def test_no_industry_word_survives_anywhere_the_model_reads(self):
        found = sorted({m.group(0).lower()
                        for m in DOMAIN.finditer(self._text())})
        self.assertEqual(found, [], f"still names {found}")

    def test_the_ambiguous_words_are_only_used_in_their_other_sense(self):
        for line in self._text().split("\n"):
            m = AMBIGUOUS.search(line)
            self.assertIsNone(m, f"{m and m.group(0)!r} in {line.strip()[:80]!r}")


if __name__ == "__main__":
    unittest.main()


class RefinerIsShapeNotIndustryTests(unittest.TestCase):
    """Site types describe the shape of a site, never one trade."""

    def test_no_industry_has_a_type_of_its_own(self):
        from agents.planner.refiner import KEYWORD_WEIGHTS, SECTION_MAP, SITE_TYPES
        for table in (SITE_TYPES, SECTION_MAP, KEYWORD_WEIGHTS):
            found = sorted(k for k in table if DOMAIN.search(k))
            self.assertEqual(found, [], f"site type named after a trade: {found}")

    def test_places_people_visit_all_share_one_shape(self):
        from agents.planner.refiner import RefinerAgent
        agent = RefinerAgent.__new__(RefinerAgent)
        for idea in ("a restaurant website with our menu",
                     "a hair salon, our prices and book an appointment",
                     "a dental clinic, our services and find us",
                     "a garage, price list and opening hours"):
            self.assertEqual(agent._detect_type(idea), "venue", idea)

    def test_wording_does_not_have_to_match_the_keyword_exactly(self):
        from agents.planner.refiner import RefinerAgent
        agent = RefinerAgent.__new__(RefinerAgent)
        self.assertEqual(agent._detect_type("build me a habit tracking app"), "app")
        self.assertEqual(agent._detect_type("a todo list app"), "app")
        self.assertEqual(agent._detect_type("some random thing"), "general")

    def test_a_spec_written_before_the_rename_still_resolves(self):
        from agents.planner.refiner import SECTION_MAP, normalise_type
        self.assertEqual(normalise_type("restaurant"), "venue")
        self.assertIn(normalise_type("restaurant"), SECTION_MAP)


FAMILIES = {
    "hospitality": r"hotels?|bookings?|reservations?|guests?|restaurants?|"
                   r"cafes?|bistro|dish(?:es)?|cuisine|concierge",
    "health": r"doctors?|patients?|clinics?|nurses?|appointments?|prescriptions?",
    "education": r"courses?|students?|teachers?|lecturers?|tuition",
    "travel": r"flights?|passengers?|airlines?",
    "retail": r"shops?|carts?|checkouts?|storefronts?|merchants?",
    "property": r"landlords?|tenants?|leases?",
    "fitness": r"gyms?|trainers?|workouts?",
    "animals": r"\bpets?\b|\bvets?\b|\bdogs?\b",
}
FAMILY_RE = {name: re.compile(r"(?i)(?<!-)\b(?:" + pat + r")\b")
             for name, pat in FAMILIES.items()}


class WholeAgentFolderTests(unittest.TestCase):
    """No file under agents/ may lean on one kind of app."""

    def _families(self, text):
        return {name for name, rx in FAMILY_RE.items() if rx.search(text)}

    def test_no_file_leans_on_a_single_industry(self):
        biased = {}
        for path in sorted(Path("agents").rglob("*.py")):
            fams = self._families(path.read_text(encoding="utf-8"))
            if 0 < len(fams) < 3:
                biased[str(path)] = sorted(fams)
        self.assertEqual(biased, {}, f"one-industry files: {biased}")

    def test_the_folder_as_a_whole_is_spread_across_industries(self):
        text = "".join(p.read_text(encoding="utf-8")
                       for p in Path("agents").rglob("*.py"))
        self.assertGreaterEqual(len(self._families(text)), 5)

    def test_the_model_facing_files_still_name_nothing(self):
        for rel in PROMPT_FILES:
            self.assertEqual(self._families(
                Path(rel).read_text(encoding="utf-8")), set(), rel)


class ServerAndRuntimeTests(unittest.TestCase):
    """server.py and server_modules/ must not assume one kind of app either."""

    ROOTS = ["server.py", "server_runtime.py"]

    def _files(self):
        out = [Path(r) for r in self.ROOTS if Path(r).exists()]
        for folder in ("server_modules", "qa_agent"):
            out.extend(sorted(Path(folder).rglob("*.py")))
        return out

    def _families(self, text):
        return {name for name, rx in FAMILY_RE.items() if rx.search(text)}

    def test_no_server_file_leans_on_a_single_industry(self):
        biased = {}
        for path in self._files():
            fams = self._families(path.read_text(encoding="utf-8"))
            if 0 < len(fams) < 3:
                biased[str(path)] = sorted(fams)
        self.assertEqual(biased, {}, f"one-industry files: {biased}")

    def test_a_session_is_judged_by_route_shape_not_by_a_route_list(self):
        from qa_agent.e2e_common import needs_session
        for public in ("/", "/login", "/signup", "/about", "/contact/"):
            self.assertFalse(needs_session(public), public)
        for private in ("/checkout", "/inventory", "/appointments",
                        "/admin/doctors", "/courses/12", "/my-orders"):
            self.assertTrue(needs_session(private), private)
