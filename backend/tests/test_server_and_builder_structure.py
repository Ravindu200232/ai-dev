import importlib
import unittest
from pathlib import Path


SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css", ".html"}


class ServerAndBuilderStructureTests(unittest.TestCase):
    def test_server_entrypoint_and_runtime_assembler_are_thin(self):
        self.assertLess(len(Path("server.py").read_text(encoding="utf-8").splitlines()), 60)
        self.assertLess(len(Path("server_runtime.py").read_text(encoding="utf-8").splitlines()), 100)
        server = importlib.import_module("server")
        self.assertTrue(server.__file__.endswith("server_runtime.py"))
        self.assertTrue(callable(server.run_feature))
        self.assertTrue(callable(server._e2e_one_flow))
        self.assertIs(server.run_feature.__globals__, server.__dict__)
        self.assertIs(server._e2e_one_flow.__globals__, server.__dict__)

    def test_server_runtime_is_split_by_domain(self):
        required = {
            "server_modules/core",
            "server_modules/srs",
            "server_modules/qa",
            "server_modules/agent",
            "server_modules/deploy",
            "server_modules/ui",
        }
        for rel in required:
            self.assertTrue(Path(rel).is_dir(), rel)
        self.assertTrue(Path("server_modules/srs/srs_runtime.py").is_file())
        self.assertTrue(Path("server_modules/qa/e2e_stage.py").is_file())
        self.assertTrue(Path("server_modules/agent/feature/actions.py").is_file())
        self.assertTrue(Path("server_modules/deploy/deploy_runtime.py").is_file())

    def test_runtime_module_names_do_not_use_numeric_prefixes(self):
        roots = ("core", "srs", "qa", "agent", "deploy", "ui")
        numbered = []
        for root in roots:
            for path in (Path("server_modules") / root).glob("*.py"):
                if path.name[:1].isdigit():
                    numbered.append(str(path))
        self.assertEqual([], numbered)

    def test_builder_is_split_and_the_legacy_vite_generator_is_gone(self):
        root = Path("agents/builder")
        self.assertEqual([], [p.name for p in root.glob("*.py")])
        for rel in ("orchestration", "workflow", "scaffolding", "prompts"):
            self.assertTrue((root / rel).is_dir(), rel)
        for rel in (
            "server_modules/core/build_entry.py",
            "server_modules/agent/pipeline/runner.py",
            "pipeline.py",
        ):
            self.assertFalse(Path(rel).exists(), rel)
        http = Path("server_modules/ui/http_handler.py").read_text(encoding="utf-8")
        socket = Path("server_modules/agent/builder/project_ops.py").read_text(encoding="utf-8")
        prompts = Path("agents/builder/prompts/catalog.py").read_text(encoding="utf-8")
        self.assertNotIn('path == "/build"', http)
        self.assertNotIn('path == "/update"', http)
        self.assertNotIn('msg.get("type") == "build"', socket)
        self.assertNotIn('msg.get("type") == "update"', socket)
        self.assertNotIn('"vite": {', prompts)

    def test_agent_domains_are_navigable(self):
        for rel in (
            "agents/planner", "agents/builder", "agents/gates",
            "agents/repair", "agents/feature", "agents/pencil",
            "agents/picture", "agents/selection",
            "server_modules/agent/builder", "server_modules/agent/design",
            "server_modules/agent/gates", "server_modules/agent/planner",
            "server_modules/agent/repair", "server_modules/agent/feature",
            "server_modules/agent/pencil", "server_modules/agent/picture",
            "server_modules/agent/selection",
        ):
            self.assertTrue(Path(rel).is_dir(), rel)

    def test_e2e_budget_policy_lives_in_qa_domain(self):
        text = Path("server_modules/qa/e2e_policy.py").read_text(encoding="utf-8")
        self.assertIn("E2E_BASE_FIX", text)
        self.assertIn("E2E_HARD_FIX", text)
        stage = Path("server_modules/qa/unit_stage.py").read_text(encoding="utf-8")
        self.assertIn("server_modules.qa.e2e_policy", stage)

    def test_maintained_source_files_stay_under_1000_lines(self):
        offenders = []
        for path in Path(".").rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            if any(part in {"node_modules", ".next", "dist", "build"} for part in path.parts):
                continue
            lines = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
            if lines > 1000:
                offenders.append(f"{path}: {lines}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
