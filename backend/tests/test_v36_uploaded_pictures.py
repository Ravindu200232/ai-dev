"""Bring your own pictures: used as-is, never drawn over."""
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch as _patch


class AdoptUploadedImagesTests(unittest.TestCase):
    def setUp(self):
        self.server = importlib.import_module("server")
        self.dir = Path(tempfile.mkdtemp())
        self.src = self.dir / "from-the-person"
        self.src.mkdir()
        for name in ("hero.png", "room-deluxe.png"):
            (self.src / name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        self.proj = self.dir / "project"
        self.out = self.proj / "public" / "generated"

    def adopt(self, uploads):
        with _patch.object(self.server, "elog", lambda *a, **k: None):
            return self.server.adopt_uploaded_images(uploads, self.proj)

    def test_a_picture_lands_where_a_drawn_one_would(self):
        self.assertEqual(self.adopt({"hero": str(self.src / "hero.png")}), 1)
        self.assertTrue((self.out / "hero.png").is_file())

    def test_the_bytes_are_the_ones_that_were_uploaded(self):
        self.adopt({"hero": str(self.src / "hero.png")})
        self.assertEqual((self.out / "hero.png").read_bytes(),
                         (self.src / "hero.png").read_bytes())

    def test_uploading_nothing_changes_nothing(self):
        self.assertEqual(self.adopt(None), 0)
        self.assertEqual(self.adopt({}), 0)
        self.assertFalse(self.out.exists())

    def test_a_name_the_build_cannot_use_is_refused(self):
        self.assertEqual(self.adopt({"bad name!": str(self.src / "hero.png")}), 0)
        self.assertEqual(self.adopt({"../escape": str(self.src / "hero.png")}), 0)

    def test_a_file_that_is_gone_does_not_stop_the_others(self):
        taken = self.adopt({"hero": str(self.src / "hero.png"),
                            "missing": str(self.src / "nope.png")})
        self.assertEqual(taken, 1)
        self.assertTrue((self.out / "hero.png").is_file())

    def test_an_uploaded_key_is_not_drawn_again(self):
        """`_fill_missing_images` skips whatever is already on disk."""
        self.adopt({"room-deluxe": str(self.src / "room-deluxe.png")})

        class Fake:
            def __init__(self): self.jobs = []
            def __getattr__(self, _): return lambda *a, **k: None

        arch = SimpleNamespace(
            project_dir=self.proj, plan={},
            files={"lib/seed.js": "await seedImage('room-deluxe')\n"
                                  "await seedImage('room-suite')"})
        fake = Fake()
        with _patch.object(self.server, "image_agent", lambda *a, **k: fake), \
             _patch.object(self.server, "elog", lambda *a, **k: None), \
             _patch.object(self.server, "eprog", lambda *a, **k: None), \
             _patch.object(self.server, "ephase", lambda *a, **k: None):
            self.server._fill_missing_images(arch, self.proj, "the pages")
        asked = {j["key"] for j in getattr(fake, "jobs", [])}
        self.assertNotIn("room-deluxe", asked)


class BuildAcceptsThemTests(unittest.TestCase):
    def test_the_pipeline_takes_an_uploads_argument(self):
        import inspect
        server = importlib.import_module("server")
        sig = inspect.signature(server.run_agent_pipeline)
        self.assertIn("uploads", sig.parameters)
        self.assertIsNone(sig.parameters["uploads"].default)

    def test_the_socket_reads_only_usable_pairs(self):
        server = importlib.import_module("server")
        got = server._upload_map({"hero": "/tmp/a.png", "": "/tmp/b.png",
                                  "empty": "", "x" * 80: "/tmp/c.png"})
        self.assertEqual(got, {"hero": "/tmp/a.png"})
        self.assertEqual(server._upload_map("not a map"), {})
        self.assertEqual(server._upload_map(None), {})


if __name__ == "__main__":
    unittest.main()
