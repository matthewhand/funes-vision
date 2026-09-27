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

    def test_unusable_timestamps_degrade_to_the_filename_clock(self):
        a, b = _timed_name(25, 0), _timed_name(25, 200)
        for bad in (float("nan"), float("inf"), float("-inf"), "nope",
                    object(), [], {}):
            stamps = media._frame_timestamps([(a, bad), (b, bad)])
            self.assertNotIn(None, stamps, bad)
        # The supplied garbage is dropped, so the real gaps pace the clip.
        self.assertEqual(
            media._gif_durations([(a, float("nan")), (b, float("inf")),
                                  (_timed_name(25, 400), "x")]),
            [200, 200, 200])

    def test_cadence_fps_skips_unusable_frames(self):
        good = [_timed_name(25, i * 200) for i in range(3)]
        pairs = [(good[0], None), ("plain.jpg", None)] + [(p, None) for p in good[1:]]
        self.assertAlmostEqual(media._cadence_fps(pairs), 5.0, places=3)
        # Junk supplied for a timed name is dropped, not fatal.
        self.assertAlmostEqual(
            media._cadence_fps([(p, float("nan")) for p in good]), 5.0, places=3)

    def test_descending_timestamps_use_the_uniform_fallback(self):
        # #34: images.json is newest-first, so every gap is negative and the
        # uniform default is the only sane hold (as in the in-app frameDurations).
        names = [_timed_name(25, ms) for ms in (800, 400, 0)]
        self.assertEqual(media._gif_durations([(n, None) for n in names]),
                         [media.FRAME_MS] * 3)
        self.assertEqual(media._gif_durations([("a.jpg", 5.0), ("b.jpg", 4.0)]),
                         [media.FRAME_MS] * 2)
        self.assertEqual(media._gif_durations([("a.jpg", 5.0)]), [media.FRAME_MS])

    def test_out_of_order_gaps_fall_back_one_frame_at_a_time(self):
        # A single reversed pair takes the default hold; the real gaps around it
        # keep their cadence rather than the whole clip collapsing to 80 ms.
        self.assertEqual(
            media._gif_durations([("a.jpg", 0.0), ("b.jpg", 0.2),
                                  ("c.jpg", 0.9), ("d.jpg", 0.3)]),
            [200, 700, media.FRAME_MS, 700])

    def test_untimed_gap_voids_only_its_own_hold(self):
        good = [_timed_name(25, i * 200) for i in range(4)]
        pairs = [(good[0], None), ("plain.jpg", None)] + [(p, None) for p in good[1:]]
        # The two gaps touching the unparseable name take FRAME_MS; the three
        # real 200 ms gaps survive instead of the whole sequence falling back.
        self.assertEqual(media._gif_durations(pairs),
                         [media.FRAME_MS, media.FRAME_MS, 200, 200, 200])

    def test_clamps_fall_back_instead_of_raising(self):
        for bad in ("nope", None, float("nan"), float("inf"), object()):
            self.assertEqual(media._clamp_int(bad, 2, 256, 256), 256)
            self.assertEqual(media._clamp_width(bad), media.WIDTH)
            self.assertEqual(media._clamp_ms(bad), media.FRAME_MS)
        self.assertEqual(media._clamp_int(1, 2, 256, 256), 2)
        self.assertEqual(media._clamp_int(9999, 2, 256, 256), 256)
        self.assertEqual(media._clamp_width(0), media.WIDTH)
        self.assertEqual(media._clamp_width(-3), media.WIDTH)
        self.assertEqual(media._clamp_width(120), 120)
        self.assertEqual(media._clamp_ms(10), media.MIN_FRAME_MS)
        self.assertEqual(media._clamp_ms(99_999), media.MAX_FRAME_MS)
        self.assertEqual(media._clamp_ms(250), 250)


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class _GifCase(unittest.TestCase):
    """Scratch frames in a temp dir for the Pillow-backed GIF cases."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="webcam_media_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _frame(self, name, color, size=(160, 120)):
        path = os.path.join(self.tmp, name)
        _save(path, color, size)
        return path

    def _burst(self, count=3, step_ms=400, prefix=25):
        """``count`` frames whose filename clock is ``step_ms`` apart."""
        return [self._frame(_timed_name(prefix, i * step_ms), (i * 40, 20, 30))
                for i in range(count)]


class TestBuildGif(_GifCase):
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


class TestBuildGifRobustness(_GifCase):
    """#36: a hostile /api/clip payload must degrade, not 500."""

    def test_unusable_timestamps_still_build(self):
        paths = self._burst()
        cases = {
            "nan": [float("nan"), 2.0, 3.0],
            "inf": [1, 2, float("inf")],
            "neg_inf": [1, float("-inf"), 3],
            "text": ["a", "b", "c"],
            "mixed": [None, "nope", 3.0],
            "overflowing": [1e308, 1e308, 1e308],
        }
        for label, stamps in cases.items():
            out = os.path.join(self.tmp, f"{label}.gif")
            self.assertEqual(media.build_gif(paths, out, timestamps=stamps), out,
                             label)
            with Image.open(out) as im:
                self.assertEqual(im.n_frames, len(paths), label)

    def test_nan_timestamps_fall_back_to_the_filename_clock(self):
        # The bad value is dropped, so the real 400 ms gaps still pace the clip.
        out = os.path.join(self.tmp, "nan.gif")
        media.build_gif(self._burst(), out,
                        timestamps=[float("nan")] * 3)
        self.assertEqual(_durations(out), [400, 400, 400])

    def test_unusable_colors_and_width_fall_back(self):
        paths = self._burst()
        cases = {
            "colors_text": {"colors": "nope"},
            "colors_none": {"colors": None},
            "colors_nan": {"colors": float("nan")},
            "colors_inf": {"colors": float("inf")},
            "width_text": {"width": "nope"},
            "width_none": {"width": None},
            "width_zero": {"width": 0},
            "width_negative": {"width": -5},
            "width_inf": {"width": float("inf")},
        }
        for label, kwargs in cases.items():
            out = os.path.join(self.tmp, "fallback.gif")
            self.assertEqual(media.build_gif(paths, out, **kwargs), out, label)
            with Image.open(out) as im:
                self.assertEqual(im.n_frames, len(paths), label)

    def test_one_unparseable_name_keeps_the_burst_cadence(self):
        good = self._burst(count=4, step_ms=200)
        paths = [good[0], self._frame("plain.jpg", (1, 2, 3)),
                 good[1], good[2], good[3]]
        out = os.path.join(self.tmp, "burst.gif")
        media.build_gif(paths, out)
        durs = _durations(out)
        self.assertEqual(len(durs), len(paths))
        # Only the frame with no usable clock takes FRAME_MS; the rest keep the
        # 200 ms cadence instead of the whole export falling back (or strobing).
        self.assertEqual(durs[2:], [200, 200, 200])
        self.assertNotEqual(set(durs), {media.MIN_FRAME_MS})

    def test_frame_ms_pins_every_hold(self):
        paths = self._burst()
        out = os.path.join(self.tmp, "pinned.gif")
        media.build_gif(paths, out, frame_ms=250)
        self.assertEqual(_durations(out), [250, 250, 250])
        media.build_gif(paths, out)
        self.assertNotEqual(_durations(out), [250, 250, 250])


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
