import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from qa_agent.e2e import E2EAgent
from qa_agent.flows import Scenario, Selector, Step
from qa_agent.debugger import AgenticE2EDebugger


class V6FieldGroundingTests(unittest.TestCase):
    def make_agent(self):
        td = TemporaryDirectory()
        root = Path(td.name)
        arch = SimpleNamespace(project_dir=root, files={}, plan={})
        agent = E2EAgent(arch, root)
        agent._tmp = td
        return agent

    def test_discount_textbox_recovers_to_observed_field_name(self):
        agent = self.make_agent()
        sel = Selector(kind="role", role="textbox", pattern="discount", flags="i")
        rows = [
            {"tag":"input", "type":"number", "name":"discountPct", "id":"",
             "placeholder":"", "aria":"", "label":"DISCOUNT (%)", "visualLabel":"",
             "autocomplete":"", "testid":"", "disabled":False},
            {"tag":"input", "type":"text", "name":"code", "id":"",
             "placeholder":"Coupon code", "aria":"", "label":"CODE", "visualLabel":"",
             "autocomplete":"", "testid":"", "disabled":False},
        ]
        got = agent._field_selector_from_rows(sel, rows)
        self.assertIsNotNone(got)
        self.assertEqual(got.kind, "field")
        self.assertEqual(got.pattern, "discountPct")

    def test_generic_name_recovers_when_only_one_name_field_is_plausible(self):
        agent = self.make_agent()
        sel = Selector(kind="role", role="textbox", pattern="name", flags="i")
        rows = [
            {"tag":"input", "type":"text", "name":"productName", "id":"product-name",
             "placeholder":"", "aria":"", "label":"NAME", "visualLabel":"",
             "autocomplete":"", "testid":"", "disabled":False},
            {"tag":"input", "type":"number", "name":"price", "id":"price",
             "placeholder":"", "aria":"", "label":"PRICE", "visualLabel":"",
             "autocomplete":"", "testid":"", "disabled":False},
        ]
        got = agent._field_selector_from_rows(sel, rows)
        self.assertIsNotNone(got)
        self.assertEqual(got.kind, "field")
        self.assertEqual(got.pattern, "productName")

    def test_ambiguous_name_fields_are_not_guessed(self):
        agent = self.make_agent()
        sel = Selector(kind="role", role="textbox", pattern="name", flags="i")
        rows = [
            {"tag":"input", "type":"text", "name":"firstName", "id":"",
             "placeholder":"", "aria":"", "label":"First name", "visualLabel":"",
             "autocomplete":"", "testid":"", "disabled":False},
            {"tag":"input", "type":"text", "name":"lastName", "id":"",
             "placeholder":"", "aria":"", "label":"Last name", "visualLabel":"",
             "autocomplete":"", "testid":"", "disabled":False},
        ]
        self.assertIsNone(agent._field_selector_from_rows(sel, rows))

    def test_test_fix_patch_is_built_without_llm_from_failure_dom(self):
        agent = self.make_agent()
        sc = Scenario(role="owner", steps=[
            Step("FILL", Selector(kind="role", role="textbox", pattern="discount", flags="i"), "15")
        ])
        failure = SimpleNamespace(name=sc.steps[0].describe(), kind="SELECTOR",
                                  target="app/category-manager/page.jsx", message="nothing matched")
        agent._last_run_evidence = {"failed_index": 0, "fields": [
            {"tag":"input", "type":"number", "name":"discount", "id":"discount",
             "placeholder":"", "aria":"", "label":"DISCOUNT (%)", "visualLabel":"",
             "autocomplete":"", "testid":"", "disabled":False}
        ]}
        patch = agent.dom_grounded_test_patch(failure, sc)
        self.assertEqual(patch, "FILL :: field=discount :: 15")


class V6DebuggerClassificationTests(unittest.TestCase):
    def test_successful_earlier_mutation_does_not_turn_selector_miss_into_app_fix(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            arch = SimpleNamespace(project_dir=root, files={"app/inventory-manager/page.jsx":"export default function Page(){}"}, plan={})
            class Agent:
                _last_run_evidence = {"last_good_index": 5}
                def incident_evidence(self, failures, journey=None, dev_trace=""):
                    return ("POST http://localhost:5173/api/products :: HTTP 201 POST "
                            "http://localhost:5173/api/products\n"
                            "## DOM at failure\nDOM at /inventory-manager:\n  headings: Inventory")
                def _render(self, scenario): return ""
                def _page_for(self, route): return "app/inventory-manager/page.jsx"
            class Debugger(AgenticE2EDebugger):
                def _ask(self, prompt):
                    # Reproduce the bad cloud diagnosis from the real run:
                    # earlier POST 201 causes APP_FIX even though current failure
                    # is only a selector miss.
                    return ("VERDICT :: APP_FIX\nROOT :: the required mutation succeeded, but downstream UI/state proof did not materialize\n"
                            "FILES :: app/inventory-manager/page.jsx\nTEST_STEP :: NONE\nTEST_PATCH :: NONE\n"
                            "HYPOTHESIS :: persistence succeeded; refresh path failed\n"
                            "NEXT :: rewrite app\nCONFIDENCE :: high")
            f = SimpleNamespace(kind="SELECTOR", target="app/inventory-manager/page.jsx",
                                name="FILL :: textbox role /name/i", message="nothing matched textbox role /name/i")
            result = Debugger(Agent(), arch).investigate([f])
            self.assertEqual(result["verdict"], "TEST_FIX")


if __name__ == "__main__":
    unittest.main()
