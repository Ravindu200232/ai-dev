import unittest
from unittest.mock import patch

import server


class StreamBatchTests(unittest.TestCase):
    def tearDown(self):
        with server._STREAM_SEND_LOCK:
            server._cancel_stream_timer_locked()
            server._take_stream_payload_locked()

    def test_small_tokens_are_batched_without_losing_text(self):
        seen = []
        with patch.object(server, "emit", side_effect=seen.append), patch.object(server, "_STREAM_SEND_DELAY", 60):
            server.estream_start("app/page.jsx")
            for _ in range(100):
                server.estream("app/page.jsx", "x")
            server.estream_end("app/page.jsx", "x" * 100)

        chunks = [row for row in seen if row.get("type") == "stream"]
        self.assertLess(len(chunks), 10)
        self.assertEqual("".join(row["token"] for row in chunks), "x" * 100)
        self.assertEqual(seen[0], {"type": "stream_start", "file": "app/page.jsx"})
        self.assertEqual(seen[-1]["type"], "stream_end")

    def test_switching_files_flushes_the_previous_batch(self):
        seen = []
        with patch.object(server, "emit", side_effect=seen.append), patch.object(server, "_STREAM_SEND_DELAY", 60):
            server.estream_start("a.js")
            server.estream("a.js", "alpha")
            server.estream("b.js", "beta")
            server.estream_end("b.js", "beta")

        chunks = [row for row in seen if row.get("type") == "stream"]
        self.assertEqual([(row["file"], row["token"]) for row in chunks], [("a.js", "alpha"), ("b.js", "beta")])


if __name__ == "__main__":
    unittest.main()
