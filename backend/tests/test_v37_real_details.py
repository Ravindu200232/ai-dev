"""The customer's own name, contacts and links, gathered and kept verbatim."""
import sys
import unittest
from pathlib import Path

SRS = Path("srs-agent")
if SRS.is_dir() and str(SRS.resolve()) not in sys.path:
    sys.path.insert(0, str(SRS.resolve()))

from srs_agent.app.knowledge.offline_srs import (apply_real_details,  # noqa: E402
                                                 build_offline_srs)
from srs_agent.app.knowledge.real_details import (classify_link,  # noqa: E402
                                                  is_domain, parse_details)
from srs_agent.app.knowledge.topics_queue import (MAX_VISIBLE_QUESTIONS,  # noqa: E402
                                                  TOPICS_BY_KEY,
                                                  question_budget,
                                                  validate_question_budget)

BUSINESS = """Otier Lundora Boutique Hotel
No. 12/3, Galle Road, Colombo 04
hello@otierlundora.lk · +94 77 123 4567
otierlundora.lk · facebook.com/otierlundora
https://www.instagram.com/otierlundora"""

PORTFOLIO = """Kamal Perera
Full-stack developer
kamal.perera@gmail.com
+94 71 555 0199
https://github.com/kamalperera
https://linkedin.com/in/kamal-perera
kamalperera.dev"""


class ParsingTests(unittest.TestCase):
    def test_a_business_block_gives_every_field(self):
        got = parse_details(BUSINESS)
        self.assertEqual(got["name"], "Otier Lundora Boutique Hotel")
        self.assertEqual(got["email"], "hello@otierlundora.lk")
        self.assertEqual(got["phone"], "+94 77 123 4567")
        self.assertIn("Galle Road", got["address"])
        self.assertIn("facebook", got["links"])
        self.assertIn("instagram", got["links"])
        self.assertIn("website", got["links"])

    def test_a_portfolio_block_keeps_the_person_and_their_profiles(self):
        got = parse_details(PORTFOLIO)
        self.assertEqual(got["name"], "Kamal Perera")
        self.assertIn("github", got["links"])
        self.assertIn("linkedin", got["links"])
        self.assertEqual(got["links"]["website"], "https://kamalperera.dev")
        self.assertNotIn("address", got)

    def test_labels_in_front_of_the_value_are_stripped(self):
        got = parse_details("Business: FreshMart\nWebsite: freshmart.lk\n"
                            "Tel: 011 234 5678\nEmail: hi@freshmart.lk")
        self.assertEqual(got["name"], "FreshMart")
        self.assertEqual(got["phone"], "011 234 5678")
        self.assertEqual(got["email"], "hi@freshmart.lk")

    def test_each_link_is_recognised_for_what_it_is(self):
        self.assertEqual(classify_link("https://github.com/x"), "github")
        self.assertEqual(classify_link("https://www.linkedin.com/in/x"), "linkedin")
        self.assertEqual(classify_link("https://fb.com/x"), "facebook")
        self.assertEqual(classify_link("https://wa.me/94771234567"), "whatsapp")
        self.assertEqual(classify_link("https://freshmart.lk"), "website")

    def test_a_filename_is_not_a_website(self):
        self.assertFalse(is_domain("Next.js"))
        self.assertFalse(is_domain("page.jsx"))
        self.assertTrue(is_domain("kamalperera.dev"))
        self.assertNotIn("links", parse_details("Built with Next.js and Node.js"))

    def test_nothing_given_means_nothing_invented(self):
        for blank in ("", "   ", "\n\n", "n/a", "none"):
            self.assertEqual(parse_details(blank), {}, blank)


class OneQuestionTests(unittest.TestCase):
    def test_it_is_a_single_optional_question(self):
        topic = TOPICS_BY_KEY["real_details"]
        self.assertTrue(topic.optional)
        self.assertTrue(topic.multiline)
        self.assertEqual(topic.kind, "text")

    def test_it_is_asked_for_every_kind_of_app(self):
        self.assertEqual(TOPICS_BY_KEY["real_details"].profiles, ())

    def test_no_question_set_went_over_budget(self):
        validate_question_budget()
        for profile in ("blog", "ecommerce", "pos", "saas", "portfolio",
                        "landing", "utility", "dashboard", "other"):
            self.assertLessEqual(question_budget(profile), MAX_VISIBLE_QUESTIONS,
                                 profile)

    def test_the_prompt_forbids_inventing_details(self):
        self.assertIn("never invent", TOPICS_BY_KEY["real_details"].intent.lower())


class IntoTheSrsTests(unittest.TestCase):
    def answered(self, text):
        return build_offline_srs(
            project={"title": "Hotel", "domain_key": "custom"},
            answers=[{"question_key": "real_details", "value": text}],
        )["srs_document"]

    def test_the_details_reach_the_document(self):
        doc = self.answered(BUSINESS)
        org = doc["app_summary"]["organisation"]
        self.assertEqual(org["email"], "hello@otierlundora.lk")
        self.assertIn("Galle Road", org["address"])

    def test_the_build_is_told_to_use_them_as_given(self):
        doc = self.answered(BUSINESS)
        rows = [r for r in doc["integration_requirements"]
                if r.get("name") == "Real contact details"]
        self.assertEqual(len(rows), 1)
        self.assertIn("do not", rows[0]["description"])
        self.assertIn("hello@otierlundora.lk", rows[0]["description"])

    def test_skipping_the_question_changes_nothing(self):
        doc = build_offline_srs(project={"title": "Hotel", "domain_key": "custom"},
                                answers=[])["srs_document"]
        self.assertNotIn("organisation", doc["app_summary"])
        self.assertFalse([r for r in doc.get("integration_requirements", [])
                          if r.get("name") == "Real contact details"])

    def test_applying_twice_is_safe_on_a_document_with_nothing(self):
        doc = {}
        self.assertIs(apply_real_details(doc, []), doc)
        self.assertEqual(doc, {})


if __name__ == "__main__":
    unittest.main()
