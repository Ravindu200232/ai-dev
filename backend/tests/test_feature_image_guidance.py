import unittest
from pathlib import Path

from agents.source_guidance import feature_image_prompt, feature_image_requested


class FeatureImageGuidanceTests(unittest.TestCase):
    def test_explicit_image_feature_uses_generated_asset_contract(self):
        self.assertTrue(feature_image_requested("add a hero image for the hotel feature"))
        policy = feature_image_prompt("include a room photo")
        self.assertIn("/generated/<semantic-name>.png", policy)
        self.assertIn("Fooocus", policy)

    def test_remove_image_is_not_generation_intent(self):
        self.assertFalse(feature_image_requested("remove the hero image"))

    def test_builder_gets_human_comment_policy_without_posthoc_rewriter(self):
        text = Path("agents/architect_runtime.py").read_text(encoding="utf-8")
        self.assertIn("HUMAN_COMMENT_POLICY", text)
        self.assertFalse(Path("agents/comment_style.py").exists())


class FeatureImageAuditTests(unittest.TestCase):
    def test_explicit_image_without_generated_ref_is_a_semantic_gap(self):
        from types import SimpleNamespace
        from agents.features import FeaturesAgent
        from agents.features_common import FeatureSpec

        class Arch:
            def __init__(self):
                self.files = {"app/page.jsx": "export default function Page(){return <main>Hotel</main>}"}
                self.project_dir = Path(".")
                self.plan = {}
                self.convo = []
            def source_files(self): return dict(self.files)
        arch = Arch()
        analyzer = SimpleNamespace(
            enumerate_routes=lambda: {"/": {"file": "app/page.jsx", "kind": "page"}},
            source_files=lambda: dict(arch.files),
        )
        agent = FeaturesAgent(arch, analyzer=analyzer)
        agent.route_hint = "/"
        spec = FeatureSpec(
            files=[{"path": "app/page.jsx", "action": "edit", "kind": "server", "why": "hero"}],
            written=["app/page.jsx"],
        )
        gap = agent._explicit_image_gap("add a luxury hotel photo", spec)
        self.assertEqual(gap["result"], "FAIL")
        self.assertIn("/generated", gap["gap"])
