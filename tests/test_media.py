"""Cadence/quality tests for integrations/media.py and the /api/clip
width/fps/timestamp pass-through (#3).

Pillow-backed cases skip when Pillow is missing: media.py treats it as an
optional dependency, and the endpoint surfaces a 500 rather than importing it.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_api_endpoints import EndpointTestCase

from integrations import media

try:
    from PIL import Image
    HAS_PIL = True
except Exception:  # pragma: no cover - Pillow is an optional runtime dep
    HAS_PIL = False


def _save(path, color, size=(160, 120)):
    Image.new("RGB", size, color).save(path, "JPEG")


def _timed_name(ss, mmm):
    """Hikvision-style name: ...20260618 0645 <ss> <mmm>_MOTDEC.jpg."""
    return f"10.0.0.21_01_202606180645{ss:02d}{mmm:03d}_MOTDEC.jpg"


def _durations(gif_path):
    with Image.open(gif_path) as im:
        out = []
        for i in range(im.n_frames):
            im.seek(i)
            out.append(im.info.get("duration"))
    return out


class TestParseFrameTimestamp(unittest.TestCase):
    def test_parses_and_rejects(self):
        name = _timed_name(25, 84)
        first = media.parse_frame_timestamp(name)
        self.assertIsNotNone(first)
        self.assertEqual(media.parse_frame_timestamp("frame0.jpg"), None)
        self.assertEqual(media.parse_frame_timestamp(None), None)
        self.assertEqual(media.parse_frame_timestamp(""), None)
        # Impossible month -> None, not an exception.
        self.assertEqual(
            media.parse_frame_timestamp("x_01_20261318064525084_MOTDEC.jpg"), None)

    def test_ordering_follows_the_clock(self):
        earlier = media.parse_frame_timestamp(_timed_name(25, 0))
        later = media.parse_frame_timestamp(_timed_name(25, 400))
        self.assertLess(earlier, later)


class TestCadence(unittest.TestCase):
    def test_median_fps_and_fallbacks(self):
        a, b = _timed_name(25, 0), _timed_name(25, 400)
        self.assertAlmostEqual(
            media._cadence_fps([(a, None), (b, None)]), 2.5, places=3)
        self.assertIsNone(media._cadence_fps([(a, None)]))
        self.assertIsNone(media._cadence_fps([("plain.jpg", None)]))
        # Explicit timestamps override the (absent) filename clock.
        self.assertAlmostEqual(
            media._cadence_fps([("a.jpg", 1000.0), ("b.jpg", 1000.5)]), 2.0)

    def test_fps_is_clamped(self):
        self.assertEqual(media._clamp_fps(0), media.FPS_MIN)
        self.assertEqual(media._clamp_fps(None), 1000.0 / media.FRAME_MS)
        self.assertEqual(media._clamp_fps(100), media.FPS_MAX)
        self.assertEqual(media._clamp_fps(0.01), media.FPS_MIN)


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestBuildGif(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="webcam_media_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _frame(self, name, color, size=(160, 120)):
        path = os.path.join(self.tmp, name)
        _save(path, color, size)
        return path

    def test_empty_input_returns_none(self):
        out = os.path.join(self.tmp, "empty.gif")
        self.assertIsNone(media.build_gif([], out))
        self.assertIsNone(media.build_gif([os.path.join(self.tmp, "nope.jpg")], out))
        self.assertIsNone(media.build_mp4([], os.path.join(self.tmp, "empty.mp4")))

    def test_animated_with_expected_frame_count(self):
        paths = [
            self._frame(_timed_name(25, 0), (10, 20, 30)),
            self._frame(_timed_name(25, 800), (90, 20, 30)),
            self._frame(_timed_name(26, 600), (170, 20, 30)),
        ]
        out = os.path.join(self.tmp, "clip.gif")
        self.assertEqual(media.build_gif(paths, out), out)
        with Image.open(out) as im:
            self.assertTrue(getattr(im, "is_animated", False))
            self.assertEqual(im.n_frames, 3)

    def test_durations_follow_timestamps(self):
        paths = [
            self._frame(_timed_name(25, 0), (10, 20, 30)),
            self._frame(_timed_name(25, 400), (90, 20, 30)),
            self._frame(_timed_name(26, 400), (170, 20, 30)),
        ]
        out = os.path.join(self.tmp, "timed.gif")
        media.build_gif(paths, out)
        durs = _durations(out)
        self.assertEqual(durs[0], 400)
        self.assertEqual(durs[1], 1000)
        self.assertNotEqual(durs[0], durs[1])

    def test_durations_are_clamped(self):
        paths = [
            self._frame(_timed_name(25, 0), (10, 20, 30)),
            self._frame(_timed_name(25, 10), (90, 20, 30)),
            self._frame(_timed_name(35, 10), (170, 20, 30)),
        ]
        out = os.path.join(self.tmp, "clamp.gif")
        media.build_gif(paths, out)
        durs = _durations(out)
        self.assertEqual(durs[0], media.MIN_FRAME_MS)   # 10 ms gap -> floor
        self.assertEqual(durs[1], media.MAX_FRAME_MS)   # 10 s gap -> ceiling

    def test_uniform_fallback_when_untimed(self):
        paths = [self._frame(f"plain{i}.jpg", (i * 70, 40, 90)) for i in range(3)]
        out = os.path.join(self.tmp, "plain.gif")
        media.build_gif(paths, out)
        self.assertEqual(_durations(out), [media.FRAME_MS] * 3)

    def test_width_downscales_and_never_upscales(self):
        big = self._frame(_timed_name(25, 0), (10, 20, 30), size=(300, 200))
        out = os.path.join(self.tmp, "down.gif")
        media.build_gif([big], out, width=100)
        with Image.open(out) as im:
            self.assertEqual(im.size[0], 100)
        small = self._frame(_timed_name(25, 1), (10, 20, 30), size=(40, 30))
        out2 = os.path.join(self.tmp, "up.gif")
        media.build_gif([small], out2, width=480)
        with Image.open(out2) as im:
            self.assertEqual(im.size[0], 40)


class TestClipEndpointOptions(EndpointTestCase):
    def _capture(self, fmt):
        """Install a fake builder capturing kwargs; returns (seen, restore)."""
        seen = {}

        def fake(frames, out_path, **kw):
            seen.update(kw)
            with open(out_path, "wb") as fh:
                fh.write(b"clip-body")
            return True

        attr = "build_mp4" if fmt == "mp4" else "build_gif"
        orig = getattr(media, attr)
        setattr(media, attr, fake)
        return seen, (attr, orig)

    def _restore(self, saved):
        attr, orig = saved
        setattr(media, attr, orig)

    def test_width_is_clamped_and_passed_through(self):
        name = _timed_name(25, 0)
        self.add_frame(name)
        for requested, expected in ((10, 160), (5000, 960), (480, 480)):
            seen, saved = self._capture("gif")
            try:
                code, _ = self.h.run(
                    "POST", "/api/clip",
                    json.dumps({"files": [name], "width": requested}).encode())
            finally:
                self._restore(saved)
            self.assertEqual(code, 200)
            self.assertEqual(seen.get("width"), expected)

    def test_timestamps_come_from_filenames(self):
        names = [_timed_name(25, 0), _timed_name(25, 400)]
        for n in names:
            self.add_frame(n)
        seen, saved = self._capture("gif")
        try:
            code, _ = self.h.run(
                "POST", "/api/clip", json.dumps({"files": names}).encode())
        finally:
            self._restore(saved)
        self.assertEqual(code, 200)
        stamps = seen.get("timestamps")
        self.assertEqual(len(stamps), 2)
        self.assertAlmostEqual(stamps[1] - stamps[0], 0.4, places=3)

    def test_fps_is_clamped_and_passed_through(self):
        name = _timed_name(25, 0)
        self.add_frame(name)
        seen, saved = self._capture("mp4")
        try:
            code, _ = self.h.run(
                "POST", "/api/clip",
                json.dumps({"files": [name], "format": "mp4", "fps": 100}).encode())
        finally:
            self._restore(saved)
        self.assertEqual(code, 200)
        self.assertEqual(seen.get("fps"), 30.0)

    def test_invalid_options_fall_back_to_defaults(self):
        name = _timed_name(25, 0)
        self.add_frame(name)
        seen, saved = self._capture("gif")
        try:
            code, _ = self.h.run(
                "POST", "/api/clip",
                json.dumps({"files": [name], "width": "nope", "fps": "nope"}).encode())
        finally:
            self._restore(saved)
        self.assertEqual(code, 200)
        self.assertNotIn("width", seen)
        self.assertNotIn("fps", seen)

    def test_clip_still_rejects_traversal(self):
        code, _ = self.h.run(
            "POST", "/api/clip",
            json.dumps({"files": ["../etc/passwd", "nope.jpg"]}).encode())
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
