"""The showcase GIF tool must stay reproducible (#34).

``tools/screenshots/make_gifs.py`` builds the GIFs committed under
``docs/guide/img/`` and ``tools/demo/``. Three properties are asserted here:
the frames are ordered oldest-first (so the capture gaps are positive), the
holds are the uniform flipbook rate (never the ``MIN_FRAME_MS`` strobe), and
the committed bytes still match the sha256 manifest that backs ``--check``
(#43) under the Pillow pin in requirements.txt.
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


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestCommittedBytes(unittest.TestCase):
    """``--check`` byte-compares the committed GIFs (#43).

    The manifest is only trustworthy while Pillow is pinned to one version, so
    this also asserts the pin is still exact -- otherwise the digests below are
    comparing against an encoder that may already have moved.
    """

    def test_pillow_is_pinned_exactly(self):
        req = (ROOT / "requirements.txt").read_text().splitlines()
        pins = [ln for ln in req
                if ln.strip().startswith("Pillow") and not ln.lstrip().startswith("#")]
        self.assertEqual(len(pins), 1, pins)
        spec = pins[0].split("#", 1)[0].strip()
        self.assertRegex(spec, r"^Pillow==\d+\.\d+\.\d+$")

    def test_every_committed_asset_has_a_manifest_entry(self):
        # Nothing committed may lack a digest, and no digest may go stale.
        keys = {make_gifs._committed_key(p)
                for p in make_gifs.expected_paths()}
        self.assertEqual(keys, set(make_gifs.COMMITTED_SHA256))

    def test_committed_gifs_match_the_manifest(self):
        for path, want in make_gifs.expected_hashes(
                make_gifs.expected_paths()).items():
            with self.subTest(path=path.name):
                self.assertEqual(make_gifs.sha256(path), want)

    def test_check_fails_on_a_wrong_digest(self):
        # A wrong-but-well-formed digest must fail even though every structural
        # invariant (animated, uniform hold, size) still holds -- that is the
        # whole point of adding the byte comparison.
        target = make_gifs.DEMO_GIF
        with self.assertRaises(SystemExit):
            make_gifs.check([target], expected={target: "0" * 64})

    def test_check_rejects_a_tampered_gif(self):
        out = Path(tempfile.mkdtemp(prefix="webcam_gifs_tamper_"))
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        target = out / "demo.gif"
        original = make_gifs.DEMO_GIF.read_bytes()
        # Flip one trailer byte: same length, same frames, different bytes.
        target.write_bytes(original[:-1] + bytes([original[-1] ^ 0x01]))
        with self.assertRaises(SystemExit):
            make_gifs.check([target],
                            expected={target: make_gifs.sha256(make_gifs.DEMO_GIF)})

    def test_check_reads_only_the_bytes(self):
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns)
                  for p in make_gifs.expected_paths()}
        make_gifs.check(make_gifs.expected_paths())
        self.assertEqual(
            before,
            {p: (p.stat().st_size, p.stat().st_mtime_ns)
             for p in make_gifs.expected_paths()})


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
