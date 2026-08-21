import importlib
import unittest
from pathlib import Path


SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css", ".html"}


class ServerPipelineMaintainabilityTests(unittest.TestCase):
    def test_server_entrypoint_and_runtime_assembler_are_thin(self):
        self.assertLess(len(Path("server.py").read_text(encoding="utf-8").splitlines()), 60)
        self.assertLess(len(Path("server_runtime.py").read_text(encoding="utf-8").splitlines()), 100)
        server = importlib.import_module("server")
        self.assertTrue(server.__file__.endswith("server_runtime.py"))
        self.assertTrue(callable(server.run_feature))
        self.assertTrue(callable(server._e2e_one_flow))
        # Runtime fragments execute in one namespace so old monkeypatch/state
        # behaviour remains identical to the original monolith.
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
        self.assertTrue(Path("server_modules/agent/feature_actions.py").is_file())
        self.assertTrue(Path("server_modules/deploy/deploy_runtime.py").is_file())

    def test_runtime_module_names_do_not_use_numeric_prefixes(self):
        roots = ("core", "srs", "qa", "agent", "deploy", "ui")
        numbered = []
        for root in roots:
            for path in (Path("server_modules") / root).glob("*.py"):
                if path.name[:1].isdigit():
                    numbered.append(str(path))
        self.assertEqual([], numbered)

    def test_pipeline_implementation_lives_under_agent_domain(self):
        self.assertLess(len(Path("pipeline.py").read_text(encoding="utf-8").splitlines()), 40)
        for rel in (
            "server_modules/agent/pipeline/config.py",
            "server_modules/agent/pipeline/dev_server.py",
            "server_modules/agent/pipeline/runner.py",
            "server_modules/agent/pipeline/watcher.py",
        ):
            self.assertTrue(Path(rel).is_file(), rel)
        pipeline = importlib.import_module("pipeline")
        self.assertTrue(callable(pipeline.run_pipeline))
        self.assertTrue(callable(pipeline.start_vite))
        self.assertIsInstance(pipeline.active_proc, dict)
        # Old imports remain valid for external scripts.
        legacy = importlib.import_module("pipeline_core")
        self.assertTrue(callable(legacy.run_pipeline))

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
