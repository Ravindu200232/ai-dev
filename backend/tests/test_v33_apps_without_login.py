"""An app with no sign-in must not be told to sign in."""
import unittest
from pathlib import Path

from qa_agent.e2e_common import (AUTH_RULES_NO_ACCOUNTS, auth_rules,
                                 system_prompt)

WITH = [{"email": "admin@demo.com", "password": "p", "role": "admin"},
        {"email": "guest@demo.com", "password": "p", "role": "guest"}]


class NoLoginPromptTests(unittest.TestCase):
    def test_an_app_with_no_accounts_is_told_there_is_no_sign_in(self):
        rules = auth_rules([])
        self.assertEqual(rules, AUTH_RULES_NO_ACCOUNTS)
        self.assertIn("SIGNED OUT", rules)
        self.assertIn("Never write a sign-in step", rules)

    def test_no_rule_tells_a_login_less_app_to_sign_in(self):
        prompt = system_prompt([])
        # The selector grammar still documents field=email/password — that is
        # a menu, not an instruction. What must be gone is the instruction.
        self.assertNotIn("sign in, reach the page", prompt)
        self.assertNotIn("Roles this app really has", prompt)
        self.assertIn("Never write a sign-in step", prompt)
        self.assertIn("every journey runs as `AS :: SIGNED OUT`", prompt)

    def test_the_two_versions_are_mutually_exclusive(self):
        without, With = system_prompt([]), system_prompt(WITH)
        self.assertNotIn("THIS APP HAS NO SIGN-IN", With)
        self.assertNotIn("Name the role on the AS line", without)

    def test_an_app_with_accounts_still_gets_the_sign_in_guidance(self):
        prompt = system_prompt(WITH)
        self.assertIn("sign in, reach the page", prompt)
        self.assertIn("admin, guest", prompt)

    def test_half_written_accounts_do_not_count_as_a_sign_in(self):
        for broken in ([{"email": "a@x"}], [{"password": "p"}], [{}]):
            self.assertEqual(auth_rules(broken), AUTH_RULES_NO_ACCOUNTS, broken)

    def test_accounts_without_a_named_role_still_work(self):
        rules = auth_rules([{"email": "a@x", "password": "p"}])
        self.assertIn("one signed-in role", rules)

    def test_the_slot_is_always_filled(self):
        for accounts in ([], WITH):
            self.assertNotIn("{auth_rules}", system_prompt(accounts))


class RoleSweepTests(unittest.TestCase):
    def test_the_sweep_needs_two_roles_before_it_says_anything(self):
        text = Path("qa_agent/e2e_evidence.py").read_text(encoding="utf-8")
        self.assertIn("if len(accs) < 2:", text)

    def test_the_stage_says_plainly_when_there_is_nothing_to_separate(self):
        text = Path("server_modules/qa/e2e_stage.py").read_text(encoding="utf-8")
        self.assertIn("no role separation to check", text)
        self.assertIn("no sign-in at all", text)


if __name__ == "__main__":
    unittest.main()
