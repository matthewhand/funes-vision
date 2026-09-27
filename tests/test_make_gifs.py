"""The showcase GIF tool must stay reproducible (#34).

``tools/screenshots/make_gifs.py`` builds the GIFs committed under
``docs/guide/img/`` and ``tools/demo/``. Two properties are asserted here:
the frames are ordered oldest-first (so the capture gaps are positive) and the
holds are the uniform flipbook rate (never the ``MIN_FRAME_MS`` strobe).
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "screenshots"))

import make_gifs  # noqa: E402  (path set above)

try:
    from PIL import Image
    HAS_PIL = True
except Exception:  # pragma: no cover - Pillow is an optional runtime dep
    HAS_PIL = False


def _durations(path):
    out = []
    with Image.open(path) as im:
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            out.append(im.info.get("duration"))
    return out


class TestFrameOrder(unittest.TestCase):
    """Every capture gap must be positive, or the hold collapses to 80 ms."""

    def test_catalog_order_is_still_newest_first(self):
        # The gallery serves images.json newest-first; the tool does the sorting.
        stamps = [make_gifs.parse_frame_timestamp(n)
                  for n in make_gifs.catalog_order()]
        self.assertGreater(len(stamps), 1)
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_timeline_frames_are_oldest_first(self):
        stamps = [make_gifs.parse_frame_timestamp(p)
                  for p in make_gifs.timeline_frames()]
        self.assertEqual(stamps, sorted(stamps))
        self.assertNotIn(None, stamps)

    def test_visit_frames_are_oldest_first(self):
        frames = make_gifs.visit_frames()
        stamps = [make_gifs.parse_frame_timestamp(p) for p in frames]
        self.assertEqual(stamps, sorted(stamps))
        self.assertNotIn(None, stamps)
        self.assertTrue(set(frames) <= set(make_gifs.timeline_frames()))


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webcam_gifs_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_out_redirects_the_demo_gif(self):
        made = make_gifs.generate(self.tmp, demo=True)
        self.assertEqual(
            sorted(p.name for p in made),
            ["demo.gif", "timeline-flipbook.gif", "visit-player.gif"])
        self.assertEqual(make_gifs.demo_path(self.tmp), self.tmp / "demo.gif")
        for path in made:
            self.assertTrue(path.is_file(), path)
            self.assertEqual(set(_durations(path)), {make_gifs.FLIPBOOK_MS})

    def test_no_demo_skips_the_demo_gif(self):
        made = make_gifs.generate(self.tmp, demo=False)
        self.assertEqual(sorted(p.name for p in made),
                         ["timeline-flipbook.gif", "visit-player.gif"])
        self.assertFalse((self.tmp / "demo.gif").exists())

    def test_default_demo_path_is_the_committed_one(self):
        self.assertEqual(make_gifs.demo_path(), make_gifs.DEMO_GIF)
        self.assertEqual(make_gifs.demo_path(make_gifs.GUIDE_IMG),
                         make_gifs.DEMO_GIF)


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestCommittedGifs(unittest.TestCase):
    """The binaries in the repo must be animated, uniform and small."""

    def test_committed_gifs_match_the_tool(self):
        for path in make_gifs.expected_paths():
            with self.subTest(path=os.fspath(path)):
                self.assertTrue(path.is_file(), path)
                delays = _durations(path)
                self.assertGreater(len(delays), 1)
                self.assertEqual(set(delays), {make_gifs.FLIPBOOK_MS})
                self.assertLessEqual(path.stat().st_size,
                                     make_gifs.MAX_GIF_BYTES)

    def test_check_passes_and_is_read_only(self):
        before = [(p, p.stat().st_size, p.stat().st_mtime_ns)
                  for p in make_gifs.expected_paths()]
        make_gifs.check(make_gifs.expected_paths())
        make_gifs.main(["--check"])
        self.assertEqual(
            before,
            [(p, p.stat().st_size, p.stat().st_mtime_ns)
             for p in make_gifs.expected_paths()])

    def test_check_rejects_a_strobing_gif(self):
        from integrations.media import MIN_FRAME_MS
        out = Path(tempfile.mkdtemp(prefix="webcam_gifs_check_"))
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        target = out / "visit-player.gif"
        make_gifs.generate(out, demo=True)
        # Re-save the same frames with the 80 ms strobe hold of #34.
        _restrobe(target, MIN_FRAME_MS)
        with self.assertRaises(SystemExit):
            make_gifs.check([target])


def _restrobe(path, hold_ms):
    """Re-save ``path``'s frames with a uniform ``hold_ms`` delay (#34 repro)."""
    frames = []
    with Image.open(path) as im:
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            frames.append(im.convert("P", palette=Image.ADAPTIVE, colors=256))
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=[hold_ms] * len(frames), loop=0, optimize=True,
                   disposal=2)


if __name__ == "__main__":
    unittest.main()
