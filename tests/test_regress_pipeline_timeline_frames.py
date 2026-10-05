"""Regression: the current frame was sent to the LLM twice on every deep pass.

collect_timeline_images() returns [prior..., current] (its docstring promises
the list "always ends with current_img itself"), and run_deep_pass forwards
that whole list as ``extra_images``. analyze_image_with_schema then built
``[encode(p) for p in extra_images] + [encode(image_path)]`` — the current
JPEG was encoded, paid for, and transmitted a second time, and
TIMELINE_PROMPT claimed "2 frames taken in sequence, seconds apart" for what
was really a single still.

This is the surviving sibling of d600b4c: n_images was corrected to count
distinct frames while the *request* still carried one extra.

Run from the repo root:  python3 -m unittest tests.test_regress_pipeline_timeline_frames
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_images

SCHEMA = {"type": "object", "properties": {"porch_access": {"type": "boolean"}}}


class _FakeResponse:
    def __init__(self, text):
        self._text = text
        self.status_code = 200
        self.headers = {}

    def json(self):
        return {"message": {"content": self._text}}

    def raise_for_status(self):
        pass


class _FrameRecorder:
    """Stands in for the model transport. The fake encoder tags each frame so
    the recorder can read back the *message* the model would really receive —
    that is the thing under test, not the order encode_image() was called in."""

    def __init__(self, case):
        self.case = case
        self.message = None

    def encode_image(self, path):
        return "b64:" + os.path.basename(path)

    def chat(self, payload, timeout):
        self.message = payload["messages"][0]
        return _FakeResponse(json.dumps({"porch_access": True}))

    @property
    def frames(self):
        self.case.assertIsNotNone(self.message, "no inference call was made")
        return [i[len("b64:"):] for i in self.message["images"]]

    @property
    def prompt(self):
        self.case.assertIsNotNone(self.message, "no inference call was made")
        return self.message["content"]



class TestCurrentFrameNotDuplicated(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = self.td.name
        now = time.time()
        # 4 frames, 5 min apart, oldest first.
        for i in range(4):
            p = os.path.join(self.dir, f"f{i}.jpg")
            with open(p, "wb") as f:
                f.write(b"x" * 8)
            t = now - 300 * (4 - i)
            os.utime(p, (t, t))

        self.rec = _FrameRecorder(self)
        self._saved = {}
        for name, val in (("encode_image", self.rec.encode_image),
                          ("_ollama_chat", self.rec.chat),
                          ("runnable_chain", lambda *a, **k: ["m"]),
                          ("get_free_mem_gb", lambda: 99.0),
                          ("local_mem_threshold", lambda: 0.0)):
            self._saved[name] = getattr(analyze_images, name)
            setattr(analyze_images, name, val)

    def tearDown(self):
        for name, val in self._saved.items():
            setattr(analyze_images, name, val)
        self.td.cleanup()

    def _call(self, current, **kw):
        timeline = analyze_images.collect_timeline_images(
            self.dir, current, max_age_minutes=10.0, max_images=3)
        analyze_images.analyze_image_with_schema(
            os.path.join(self.dir, current), SCHEMA, 16,
            extra_images=timeline, **kw)
        return timeline

    def test_no_prior_frames_sends_current_exactly_once(self):
        # The regression: a single still in a 10-minute window was sent twice
        # and prompted with "These 2 security camera frames...". Use an empty
        # dir so there is genuinely no prior frame to pair it with.
        solo_dir = os.path.join(self.dir, "solo")
        os.makedirs(solo_dir)
        solo = os.path.join(solo_dir, "solo.jpg")
        with open(solo, "wb") as f:
            f.write(b"x" * 8)
        os.utime(solo, (time.time(), time.time()))

        timeline = analyze_images.collect_timeline_images(
            solo_dir, "solo.jpg", max_age_minutes=10.0, max_images=3)
        self.assertEqual(timeline, [solo])
        analyze_images.analyze_image_with_schema(
            solo, SCHEMA, 16, extra_images=timeline)
        self.assertEqual(self.rec.frames, ["solo.jpg"])
        self.assertEqual(self.rec.prompt,
                         analyze_images.prompt_for_schema(SCHEMA))

    def test_one_prior_sends_two_distinct_frames_once_each(self):
        timeline = self._call("f3.jpg")
        self.assertEqual(timeline, [os.path.join(self.dir, "f2.jpg"),
                                    os.path.join(self.dir, "f3.jpg")])
        self.assertEqual(sorted(self.rec.frames), ["f2.jpg", "f3.jpg"])
        self.assertNotIn("2 security camera frames",
                         analyze_images.TIMELINE_PROMPT.format(n=3))

    def test_two_priors_send_three_distinct_frames_once_each(self):
        # f3 with a 20-minute window picks up f1 and f2.
        timeline = analyze_images.collect_timeline_images(
            self.dir, "f3.jpg", max_age_minutes=20.0, max_images=3)
        analyze_images.analyze_image_with_schema(
            os.path.join(self.dir, "f3.jpg"), SCHEMA, 16, extra_images=timeline)
        self.assertEqual(len(timeline), 3)
        self.assertEqual(sorted(self.rec.frames), ["f1.jpg", "f2.jpg", "f3.jpg"])

    def test_frames_are_chronological_with_current_last(self):
        self._call("f3.jpg")
        # TIMELINE_PROMPT says "Look at the LAST frame"; the current frame is
        # the answer target, so it must not be shadowed by a duplicate.
        self.assertEqual(self.rec.frames[-1], "f3.jpg")
        self.assertEqual(len(set(self.rec.frames)), len(self.rec.frames))

    def test_n_images_log_matches_frames_actually_sent(self):
        timeline = self._call("f3.jpg")
        # run_deep_pass logs n_images=len(timeline_images); the transport must
        # agree or the inference audit trail is a lie (the d600b4c defect).
        self.assertEqual(len(timeline), len(self.rec.frames))

    def test_extra_images_omitted_still_sends_one_frame(self):
        analyze_images.analyze_image_with_schema(
            os.path.join(self.dir, "f3.jpg"), SCHEMA, 16)
        self.assertEqual(self.rec.frames, ["f3.jpg"])

    def test_relative_and_absolute_spellings_are_the_same_frame(self):
        # The current frame must be recognised through a different path
        # spelling, or the dedupe silently stops working.
        cur = os.path.join(self.dir, "f3.jpg")
        timeline = analyze_images.collect_timeline_images(
            self.dir, "f3.jpg", max_age_minutes=10.0, max_images=3)
        analyze_images.analyze_image_with_schema(
            cur, SCHEMA, 16,
            extra_images=[os.path.join(os.path.join(self.dir, "."), "f3.jpg")]
            + timeline)
        self.assertEqual(self.rec.frames, ["f2.jpg", "f3.jpg"])


if __name__ == "__main__":
    unittest.main()
