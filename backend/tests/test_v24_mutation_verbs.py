"""v24 — one ledger decides what changes data.

Three lists used to answer that question and disagreed, and a library build
lost all five journeys between them: a read-only public browse journey was
required to persist a business change because "Book detail shows one book"
matched `book`, and two journeys that clicked 'Return' and 'Sign Up' were
rejected before they ran because neither verb was in the other list.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace

from qa_agent.e2e_contract import capability_contract
from qa_agent.e2e_steps import E2EStepsMixin
from qa_agent.e2e_verbs import capability_mutates, label_mutates, prose_mutates


def click(label, role="button"):
    return SimpleNamespace(verb="CLICK",
                           selector=SimpleNamespace(pattern=label, role=role))


class ProseTests(unittest.TestCase):
    """A noun that reads like a verb does not make a sentence a mutation."""

    def test_the_three_that_broke_the_browse_journey(self):
        for text in ("Book detail shows one book",
                     "/books - click a book card - /books/[id]",
                     "Home shows hero and newest books"):
            self.assertFalse(prose_mutates(text), text)

    def test_booking_something_is_still_a_mutation(self):
        for text in ("Guest can book a room",
                     "Member books the last available slot"):
            self.assertTrue(prose_mutates(text), text)

    def test_the_vocabulary_that_was_missing(self):
        for text in ("Member can return a book", "Member can borrow a book",
                     "Public can register", "A member can sign up",
                     "Librarian can issue a loan", "Staff can renew a loan"):
            self.assertTrue(prose_mutates(text), text)


class CapabilityTests(unittest.TestCase):
    def test_a_showing_capability_is_read_only(self):
        self.assertFalse(capability_mutates(
            {"requirement": "Catalogue lists all books and availability",
             "proof": "three book cards are visible"}))

    def test_a_changing_capability_is_not(self):
        self.assertTrue(capability_mutates(
            {"requirement": "Member can return a book",
             "proof": "the loan disappears from my loans"}))


class ContractTests(unittest.TestCase):
    """The journey's own capabilities decide, not its prose."""

    PLAN = {"capabilities": [
        {"id": "CAP-001", "who": "public", "requirement": "Home shows hero and newest books",
         "proof": "the hero is visible"},
        {"id": "CAP-002", "who": "public", "requirement": "Catalogue lists all books",
         "proof": "book cards are visible"},
        {"id": "CAP-004", "who": "member", "requirement": "Member can borrow a book",
         "proof": "the loan appears in my loans"},
    ]}

    def _contract(self, covers, steps):
        arch = SimpleNamespace(plan=self.PLAN, files={}, project_dir=Path("."))
        return capability_contract(
            arch, {"title": "j", "role": "", "steps": steps, "covers": covers})

    def test_a_read_only_journey_is_not_required_to_mutate(self):
        c = self._contract(["CAP-001", "CAP-002"],
                           ["/ - click 'Browse Catalogue' - /books",
                            "/books - click a book card - /books/[id]"])
        self.assertFalse(c["expects_mutation"])

    def test_a_journey_that_borrows_still_is(self):
        c = self._contract(["CAP-004"],
                           ["/books/[id] - click 'Borrow' - success message"])
        self.assertTrue(c["expects_mutation"])

    def test_a_journey_covering_one_of_each_is(self):
        c = self._contract(["CAP-002", "CAP-004"], ["walk it"])
        self.assertTrue(c["expects_mutation"])

    def test_a_journey_covering_nothing_falls_back_to_its_steps(self):
        self.assertTrue(self._contract([], ["submit the form"])["expects_mutation"])
        self.assertFalse(self._contract([], ["open the page"])["expects_mutation"])


class BusinessStepTests(unittest.TestCase):
    """Every click that commits, and only those."""

    def test_the_two_clicks_that_were_not_recognised(self):
        self.assertTrue(E2EStepsMixin._is_business_step(click("Return")))
        self.assertTrue(E2EStepsMixin._is_business_step(click("Sign Up")))

    def test_the_ones_that_already_were(self):
        for label in ("Borrow", "Mark Returned", "Confirm", "Save", "Book Now"):
            self.assertTrue(E2EStepsMixin._is_business_step(click(label)), label)

    def test_navigation_and_auth_are_not_business_steps(self):
        for label in ("Browse Catalogue", "Login", "View details", "Next"):
            self.assertFalse(E2EStepsMixin._is_business_step(click(label)), label)

    def test_a_select_is_still_not_one(self):
        st = click("Return")
        st.verb = "SELECT"
        self.assertFalse(E2EStepsMixin._is_business_step(st))

    def test_label_and_prose_agree_on_the_shared_verbs(self):
        for verb in ("return", "borrow", "register", "submit", "cancel"):
            self.assertTrue(label_mutates(f"{verb} it"), verb)
            self.assertTrue(prose_mutates(f"a member can {verb} a thing"), verb)


if __name__ == "__main__":
    unittest.main()
