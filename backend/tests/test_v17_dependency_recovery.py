import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server
from agents.architect import ArchitectAgent
from agents.architect_boundaries import ArchitectBoundaryMixin


class DependencyGapTests(unittest.TestCase):
    def test_declared_but_missing_package_is_still_a_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(json.dumps({
                "dependencies": {"lucide-react": "^0.441.0"}
            }))

            class Dummy(ArchitectBoundaryMixin):
                def __init__(self):
                    self.project_dir = root
                    self.files = {
                        "app/page.jsx": "import { Search } from 'lucide-react'\nexport default function Page(){}"
                    }

            self.assertEqual(Dummy().unresolved_packages(), ["lucide-react"])

    def test_installed_but_undeclared_package_is_still_a_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(json.dumps({"dependencies": {}}))
            pkg = root / "node_modules" / "lucide-react"
            pkg.mkdir(parents=True)
            (pkg / "package.json").write_text("{}")

            class Dummy(ArchitectBoundaryMixin):
                def __init__(self):
                    self.project_dir = root
                    self.files = {
                        "app/page.jsx": "import { Search } from 'lucide-react'\nexport default function Page(){}"
                    }

            self.assertEqual(Dummy().unresolved_packages(), ["lucide-react"])

    def test_sync_declares_known_side_effect_imports(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(json.dumps({"dependencies": {}}))
            arch = ArchitectAgent.__new__(ArchitectAgent)
            arch.project_dir = root
            arch.stack = "next"
            arch.files = {"app/page.jsx": "import 'react-hot-toast'\nexport default function Page(){}"}
            arch.write_seq = 0
            arch._log = lambda *_args: None
            self.assertEqual(arch.sync_dependencies(), 1)
            pkg = json.loads((root / "package.json").read_text())
            self.assertIn("react-hot-toast", pkg["dependencies"])


class CompilerNamedPackageTests(unittest.TestCase):
    def _arch(self, root):
        arch = ArchitectAgent.__new__(ArchitectAgent)
        arch.project_dir = root
        arch.stack = "next"
        arch._log = lambda *_args: None
        return arch

    def test_compiler_named_package_is_returned_even_with_stale_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stale = root / "node_modules" / "lucide-react"
            stale.mkdir(parents=True)
            (stale / "package.json").write_text("{}")
            arch = self._arch(root)
            text = "Module not found: Can't resolve 'lucide-react'"
            self.assertEqual(arch.packages_named_in(text), ["lucide-react"])

    def test_install_replaces_stale_package_and_keeps_pinned_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(json.dumps({
                "dependencies": {"lucide-react": "^0.441.0"}
            }))
            stale = root / "node_modules" / "lucide-react"
            stale.mkdir(parents=True)
            (stale / "package.json").write_text("{}")
            arch = self._arch(root)

            seen = {}
            class Cmd:
                def run(self, command):
                    seen["command"] = command
                    seen["stale_removed"] = not stale.exists()
                    stale.mkdir(parents=True)
                    (stale / "package.json").write_text("{}")
                    return SimpleNamespace(ok=True)
            arch.cmd = Cmd()

            landed = arch.install_packages(["lucide-react"])
            self.assertEqual(landed, ["lucide-react"])
            self.assertTrue(seen["stale_removed"])
            self.assertEqual(seen["command"], "npm install lucide-react@^0.441.0")

    def test_build_reconciles_missing_package_before_llm_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(json.dumps({"dependencies": {}}))
            state = {"installed": False, "updated": False}

            class Arch:
                def sync_dependencies(self):
                    return 0
                def unresolved_packages(self):
                    return []
                def packages_named_in(self, errors):
                    return ["lucide-react"] if "lucide-react" in errors else []
                def install_packages(self, names):
                    state["installed"] = True
                    return list(names)
                def update(self, _instruction):
                    state["updated"] = True
                    raise AssertionError("LLM rewrite should not run for a package-only failure")

            def build_errors(_root, _stack):
                if not state["installed"]:
                    return ("Module not found: Can't resolve 'lucide-react'", True)
                return ("", True)

            with patch.object(server, "align_tailwind", return_value=None), \
                 patch.object(server, "ensure_node_deps", return_value=True), \
                 patch.object(server, "_npm_build_errors", side_effect=build_errors), \
                 patch.object(server, "_toolchain_break", return_value=[]), \
                 patch.object(server, "estep", return_value=None), \
                 patch.object(server, "eprog", return_value=None), \
                 patch.object(server, "ephase", return_value=None), \
                 patch.object(server, "elog", return_value=None), \
                 patch.object(server, "emit", return_value=None):
                ok = server.run_build_fix_loop(Arch(), root, True, max_rounds=1)

            self.assertTrue(ok)
            self.assertTrue(state["installed"])
            self.assertFalse(state["updated"])


if __name__ == "__main__":
    unittest.main()
