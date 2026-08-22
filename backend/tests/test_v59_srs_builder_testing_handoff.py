"""The approved SRS gives Builder deterministic unit and E2E inputs."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SRS_AGENT = BACKEND / "srs-agent"
if str(SRS_AGENT) not in sys.path:
    sys.path.insert(0, str(SRS_AGENT))

from srs_agent.app.generators.builder_contract import build_handoff  # noqa: E402


def _document() -> dict:
    return {
        "project_name": "Orders",
        "functional_requirements": [{
            "id": "FR-001",
            "module": "Orders",
            "priority": "high",
            "requirement": "Customers can view one order and its current status",
        }],
        "public_pages": [{
            "page_name": "Login",
            "route": "/login",
            "login_required": False,
            "functions": ["Sign in"],
        }],
        "protected_pages": [{
            "page_name": "Order Details",
            "route": "/orders/{id}",
            "login_required": True,
            "allowed_roles": ["customer"],
            "functions": ["View the order status"],
        }],
        "database_design": {
            "tables": [{
                "table_name": "orders",
                "fields": [
                    {"name": "_id", "type": "ObjectId", "primary_key": True},
                    {"name": "status", "type": "String", "nullable": False},
                ],
            }],
            "relationships": [],
        },
        "api_design": [{
            "method": "GET",
            "path": "/api/orders/{id}",
            "description": "Get one order and its status",
            "auth_required": True,
            "allowed_roles": ["customer"],
        }],
        "roles": [{"role_name": "customer", "role_key": "customer"}],
        "role_access_matrix": [{
            "role": "customer",
            "allowed_pages": ["Order Details"],
            "restricted_pages": [],
            "allowed_functions": ["View the order status"],
            "restricted_functions": [],
        }],
        "requirement_traceability_matrix": [{
            "requirement_id": "FR-001",
            "module": "Orders",
            "pages": ["Order Details"],
            "tables": ["orders"],
            "test_case": "The details page shows the persisted order status",
        }],
        "business_workflows": [{
            "workflow_name": "Customer checks an order",
            "actor": "customer",
            "covers": ["FR-001"],
            "preconditions": ["The customer owns the requested order"],
            "steps": ["Open the Order Details page", "Read the current status"],
            "expected_result": "The persisted status is visibly shown",
        }],
        "acceptance_criteria": [{
            "id": "FR-001",
            "criterion": "The order status remains visible after a reload",
        }],
        "validation_rules": [{"field": "id", "rule": "must be a valid ObjectId"}],
    }


class SrsBuilderTestingHandoffTests(unittest.TestCase):
    def test_srs_passes_complete_pre_journey_and_unit_input_to_builder(self):
        handoff = build_handoff(plan={}, srs_document=_document(), auth=True)

        self.assertEqual(handoff["handoff_version"], 5)
        testing = handoff["testing_contract"]
        unit = next(case for case in testing["unit"] if case["covers"] == ["CAP-FR-001"])
        self.assertIn("app/orders/[id]/page.jsx", unit["targets"])
        self.assertIn("app/api/orders/[id]/route.js", unit["targets"])
        self.assertIn("wrong_role_forbidden", unit["cases"])

        journey = next(case for case in testing["e2e"] if "CAP-FR-001" in case["covers"])
        pre = journey["pre_journey"]
        self.assertEqual(journey["actor"], "customer")
        self.assertEqual(pre["initial_route"], "/login")
        self.assertEqual(pre["account"]["role"], "customer")
        self.assertTrue(pre["account"]["never_reuse_another_role"])
        self.assertIn("orders", pre["seed_entities"])
        self.assertIn("GET /api/orders/[id]", pre["required_apis"])
        self.assertTrue(pre["required_records"][0]["stable_real_id"])
        self.assertIn("The customer owns the requested order", pre["explicit_preconditions"])
        self.assertIn("The persisted status is visibly shown", journey["proofs"])
        self.assertEqual(testing["coverage"]["requirements_without_unit"], [])
        self.assertEqual(testing["coverage"]["browser_capabilities_without_e2e"], [])

        prompt = handoff["prompt"]
        self.assertIn("AGENTFORGE BUILD HANDOFF v5", prompt)
        self.assertIn("TEST-READY BUILD INPUT", prompt)
        self.assertIn("PRE-JOURNEY", prompt)
        self.assertIn("one distinct seeded account for this exact role", prompt)

    def test_browser_requirement_without_workflow_gets_a_real_journey(self):
        doc = _document()
        doc["business_workflows"] = []
        handoff = build_handoff(plan={}, srs_document=doc, auth=True)
        testing = handoff["testing_contract"]

        journey = next(case for case in testing["e2e"] if "CAP-FR-001" in case["covers"])
        self.assertTrue(journey["synthesized"])
        self.assertIn(journey["id"], testing["coverage"]["synthesized_e2e"])
        self.assertIn("Customers can view one order", " ".join(journey["steps"]))
        self.assertEqual(testing["coverage"]["browser_capabilities_without_e2e"], [])


if __name__ == "__main__":
    unittest.main()
