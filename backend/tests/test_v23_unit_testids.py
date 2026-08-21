"""v23 — the unit author gets the list the E2E author has had all along.

Measured on a hotel build: three invented `data-testid`s in one component test
survived authoring, seven repair rounds and all three escalation tiers, and the
stage ended `3 x invented testid`. The author was shown the component's source
and a rule that says only use a testid "spelled out in the source above" — and
asked to enforce that rule by scanning a 600-line file by eye.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace

from qa_agent.author import UnitTestAuthor


ROW = """
import CancelButton from '@/components/CancelButton'
export default function BookingActions({ booking }) {
  return <div data-testid="booking-actions">
    <CancelButton id={booking._id} />
  </div>
}
"""

CHILD = """
export default function CancelButton({ id }) {
  return <button data-testid="cancel-booking">Cancel</button>
}
"""


class QA:
    """The half of QASession the author actually reaches for."""

    def __init__(self, files):
        self.files = files
        self.defer_execution = True

    def read_source(self, rel):
        return self.files.get(rel, "")


def author_for(files):
    arch = SimpleNamespace(files=dict(files), plan={}, project_dir=Path("."),
                           convo=[], plan_md="")
    a = object.__new__(UnitTestAuthor)
    a.arch = arch
    a.qa = QA(files)
    a.az = None
    a.cb = {}
    a.project_dir = Path(".")
    a._advice = {}
    return a


FILES = {"components/BookingActions.jsx": ROW,
         "components/CancelButton.jsx": CHILD}


class TestIdInventoryTests(unittest.TestCase):
    """What the component really sets, children included."""

    def test_a_childs_testid_counts_as_the_parents(self):
        a = author_for(FILES)
        self.assertEqual(["booking-actions", "cancel-booking"],
                         a.testids_for_target("components/BookingActions.jsx"))

    def test_an_unreadable_target_yields_nothing_rather_than_raising(self):
        self.assertEqual([], author_for({}).testids_for_target("nope.jsx"))


class TestIdNoteTests(unittest.TestCase):
    """The list reaches the prompt, and so does its absence."""

    def test_the_real_ids_are_handed_over(self):
        note = author_for(FILES)._testid_note("components/BookingActions.jsx")
        self.assertIn("booking-actions, cancel-booking", note)
        self.assertIn("Never invent one", note)

    def test_a_component_with_no_ids_says_so_out_loud(self):
        note = author_for({"components/Plain.jsx": "export default () => <p/>"}
                          )._testid_note("components/Plain.jsx")
        self.assertIn("sets NO `data-testid`", note)
        self.assertIn("query by role and accessible name", note)

    def test_write_for_puts_the_note_in_the_block(self):
        source = Path("qa_agent/author_write.py").read_text(encoding="utf-8")
        block = source.split("def write_for")[1].split("\n    def ")[0]
        self.assertIn("self._testid_note(t.path)", block)


class InventedSelectorGuardTests(unittest.TestCase):
    """The forms that really issue a testid query are all checked."""

    def _advice(self, test_src):
        return author_for(FILES)._invented_selectors(
            test_src, "components/BookingActions.jsx")

    def test_a_real_id_is_accepted(self):
        self.assertEqual("", self._advice("getByTestId('cancel-booking')"))

    def test_an_invented_id_is_named(self):
        self.assertIn('testid "confirm-payment"',
                      self._advice("getByTestId('confirm-payment')"))

    def test_a_backtick_query_no_longer_slips_through(self):
        self.assertIn('testid "confirm-payment"',
                      self._advice("getByTestId(`confirm-payment`)"))

    def test_a_css_attribute_query_is_checked_too(self):
        self.assertIn('testid "confirm-payment"',
                      self._advice(
                          "container.querySelector('[data-testid=\"confirm-payment\"]')"))

    def test_an_id_built_from_a_variable_is_refused(self):
        advice = self._advice("getByTestId(TESTIDS.confirm)")
        self.assertIn("not a literal", advice)

    def test_an_unreadable_target_is_reported_not_waved_through(self):
        advice = author_for({})._invented_selectors(
            "getByTestId('confirm')", "components/Gone.jsx")
        self.assertIn("cannot be verified", advice)
        self.assertIn("components/Gone.jsx", advice)


class AllFindingsReachTheReAskTests(unittest.TestCase):
    """The one re-ask carries every static finding, not the first."""

    def test_a_second_finding_is_not_discarded(self):
        source = Path("qa_agent/author_repair.py").read_text(encoding="utf-8")
        body = source.split("def _quality_advice")[1].split("\n    def ")[0]
        self.assertNotIn('return next((x for x in checks if x), "")', body)
        self.assertIn('"; and ".join', body)

    def test_a_styling_assertion_and_an_invented_id_are_both_named(self):
        a = author_for(FILES)
        advice = a._quality_advice(
            "expect(el).toHaveClass('bg-red-500')\n"
            "getByTestId('confirm-payment')",
            "components/BookingActions.jsx", "tests/unit/x.test.jsx")
        self.assertIn("confirm-payment", advice)


if __name__ == "__main__":
    unittest.main()
