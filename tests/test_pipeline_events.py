"""Unit tests for the append-only pipeline event log.

Run from the repo root:  python3 -m unittest tests.test_pipeline_events
No third-party deps — stdlib unittest only. Does not import analyze_images.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline_events as pe


class TestPipelineEvents(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="pe-")
        self._orig = {
            "EVENTS_FILE": pe.EVENTS_FILE,
            "MAX_BYTES": pe.MAX_BYTES,
            "MAX_LINES": pe.MAX_LINES,
            "KEEP_LINES": pe.KEEP_LINES,
            "_writes": pe._writes,
        }
        pe.EVENTS_FILE = os.path.join(self.td, "events.jsonl")
        pe._writes = 0

    def tearDown(self):
        pe.EVENTS_FILE = self._orig["EVENTS_FILE"]
        pe.MAX_BYTES = self._orig["MAX_BYTES"]
        pe.MAX_LINES = self._orig["MAX_LINES"]
        pe.KEEP_LINES = self._orig["KEEP_LINES"]
        pe._writes = self._orig["_writes"]
        shutil.rmtree(self.td, ignore_errors=True)

    def test_emit_two_then_iter_since_and_empty_again(self):
        pe.emit("image.new", file="a.jpg")
        pe.emit("detection.preliminary", file="a.jpg", labels=["car"])

        offset, events = pe.iter_since(0)
        self.assertEqual(len(events), 2)
        self.assertGreater(offset, 0)
        self.assertEqual(os.path.getsize(pe.EVENTS_FILE), offset)

        self.assertEqual(events[0]["event"], "image.new")
        self.assertEqual(events[0]["file"], "a.jpg")
        self.assertIsInstance(events[0]["ts"], float)
        self.assertEqual(events[1]["event"], "detection.preliminary")
        self.assertEqual(events[1]["labels"], ["car"])

        offset2, events2 = pe.iter_since(offset)
        self.assertEqual(events2, [])
        self.assertEqual(offset2, offset)

    def test_emit_after_rotate_still_readable(self):
        pe.MAX_BYTES = 500
        pe.KEEP_LINES = 3
        pe.MAX_LINES = 8
        # Each line is well over 100 bytes so a 4th append exceeds MAX_BYTES.
        pad = "x" * 80
        for i in range(12):
            pe.emit("image.new", file=f"f{i}.jpg", labels=[pad])

        with open(pe.EVENTS_FILE, "rb") as f:
            nlines = sum(1 for _ in f)
        self.assertLessEqual(nlines, pe.KEEP_LINES)
        self.assertLess(os.path.getsize(pe.EVENTS_FILE), 12 * 80)

        pe.emit("new-burst", id="b1", summary="person at door")
        _off, events = pe.iter_since(0)
        self.assertTrue(events)
        self.assertEqual(events[-1]["event"], "new-burst")
        self.assertEqual(events[-1]["id"], "b1")
        self.assertEqual(events[-1]["summary"], "person at door")

        pe.emit("new-detection", file="after.jpg", labels=["person"])
        _off2, more = pe.iter_since(_off)
        names = [e.get("event") for e in more]
        self.assertIn("new-detection", names)
        self.assertEqual(more[-1]["file"], "after.jpg")

    def test_emit_never_raises_if_not_writable(self):
        ro = tempfile.mkdtemp(prefix="pe-ro-")
        try:
            os.chmod(ro, 0o555)
            pe.EVENTS_FILE = os.path.join(ro, "events.jsonl")
            if os.access(ro, os.W_OK):
                # root (or otherwise still writable) — force a failing path
                pe.EVENTS_FILE = os.path.join(
                    self.td, "no-such-dir", "events.jsonl")
            try:
                pe.emit("image.new", file="x.jpg")
            except Exception as exc:
                self.fail(f"emit raised {exc!r}")
        finally:
            try:
                os.chmod(ro, 0o755)
            except OSError:
                pass
            shutil.rmtree(ro, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
