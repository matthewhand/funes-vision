"""The committed user-guide screenshots must stay inside a documented byte budget.

Publishing ``docs/USER-GUIDE.html`` in the demo bundle also published its 15
screenshots, which took ``dist/demo/`` from 3.0 MB to 6.0 MB. The bytes are a
committed artefact, so nothing in the build re-derives them: a capture that
skips ``tools/screenshots/optimize_guide_img.py`` would ship at full capture
resolution forever and nothing would notice. This suite is the ratchet.

It asserts the ceilings *and* that the ceilings have teeth -- a budget constant
loosened to a rubber stamp, or the tool that fills it silently stopped capping,
fails here. The GIFs are deliberately not the tool's business: they are built by
``make_gifs.py`` and budgeted by ``tests/test_make_gifs.py``.
"""
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "screenshots"))

import optimize_guide_img  # noqa: E402  (path set above)

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow is a declared dependency
    HAS_PIL = False

GUIDE_IMG = ROOT / "docs" / "guide" / "img"
GUIDE_HTML = ROOT / "docs" / "USER-GUIDE.html"
GIFS = ("timeline-flipbook.gif", "visit-player.gif")


def _write_png(path, width, height, mode="P", colors=256):
    """A valid PNG of the requested shape, for the negative cases."""
    rgb = Image.new("RGB", (width, height), (11, 18, 32))
    for x in range(0, width, 7):
        for y in range(0, height, 11):
            rgb.putpixel((x, y), ((x * 13) % 256, (y * 7) % 256, ((x + y) * 3) % 256))
    if mode == "P":
        im = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    else:
        im = rgb
    im.save(path, format="PNG", optimize=True)


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestCommittedBudget(unittest.TestCase):
    """Every committed screenshot is inside the documented ceilings."""

    def test_each_png_is_within_the_byte_ceiling(self):
        names = optimize_guide_img.pngs(GUIDE_IMG)
        self.assertGreaterEqual(len(names), 14, "the guide lost its screenshots")
        for name in names:
            with self.subTest(png=name):
                path = GUIDE_IMG / name
                self.assertLessEqual(
                    path.stat().st_size, optimize_guide_img.MAX_PNG_BYTES,
                    f"{name} is {path.stat().st_size:,} bytes, over the "
                    f"{optimize_guide_img.MAX_PNG_BYTES:,} ceiling -- re-run "
                    f"tools/screenshots/optimize_guide_img.py",
                )

    def test_directory_total_is_within_the_ceiling(self):
        total = sum((GUIDE_IMG / n).stat().st_size for n in optimize_guide_img.pngs(GUIDE_IMG))
        self.assertLessEqual(total, optimize_guide_img.MAX_TOTAL_BYTES)

    def test_ceilings_are_a_ratchet_not_a_rubber_stamp(self):
        """A ceiling below the real bytes fails, and so does a meaningless one.

        Without the lower bound, raising MAX_TOTAL_BYTES past the actual total
        is silently accepted; without the upper bound, setting it to a gigabyte
        is too. Both directions are a decision, so both are asserted.
        """
        total = sum((GUIDE_IMG / n).stat().st_size for n in optimize_guide_img.pngs(GUIDE_IMG))
        self.assertGreater(optimize_guide_img.MAX_TOTAL_BYTES, total)
        self.assertLess(
            optimize_guide_img.MAX_TOTAL_BYTES, total * 1.5,
            "the total ceiling is more than 50% above the committed bytes, so it "
            "would not catch a regression to full-resolution captures",
        )

    def test_no_png_is_wider_than_the_cap(self):
        for name in optimize_guide_img.pngs(GUIDE_IMG):
            with self.subTest(png=name):
                with Image.open(GUIDE_IMG / name) as im:
                    self.assertLessEqual(im.width, optimize_guide_img.MAX_WIDTH)
                    self.assertEqual(im.mode, "P", "not an 8-bit palette PNG")

    def test_cap_is_derived_from_the_guides_displayed_width(self):
        """MAX_WIDTH must stay above the box the guide actually paints into.

        802 CSS px is the measured width of the widest <img> in
        docs/USER-GUIDE.html (.wrap 880 - 20 padding - 40 figure margin, per
        side). If the layout ever shrinks, the cap should be revisited -- this
        only catches the direction that would ship an over-large cap forever.
        """
        self.assertGreater(optimize_guide_img.MAX_WIDTH, optimize_guide_img.DISPLAY_CSS_WIDTH)
        self.assertLessEqual(
            optimize_guide_img.MAX_WIDTH, 2 * optimize_guide_img.DISPLAY_CSS_WIDTH,
            "the cap is above 2x the displayed width, so the resolution cut is "
            "not what the constant claims to be",
        )

    def test_guide_html_carries_no_hard_coded_image_dimensions(self):
        """The CSS is `width: 100%; height: auto`, so the new intrinsic sizes
        need no width/height attribute to match. If someone adds one later, the
        cap change would silently letterbox the guide."""
        html = GUIDE_HTML.read_text()
        for tag in re.findall(r"<img\b[^>]*>", html):
            self.assertNotRegex(tag, r"\bwidth\s*=", tag)
            self.assertNotRegex(tag, r"\bheight\s*=", tag)


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestCheckMode(unittest.TestCase):
    """``--check`` is the read-only gate, and it can actually fail."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webcam_guide_img_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _populate(self, count=1):
        for i in range(count):
            _write_png(self.tmp / ("shot%d.png" % i), 200, 120)

    def test_check_passes_on_the_committed_bytes_without_writing(self):
        before = sorted(
            (p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in GUIDE_IMG.iterdir()
        )
        self.assertEqual(optimize_guide_img.check(str(GUIDE_IMG)), [])
        self.assertEqual(0, optimize_guide_img.main(["--check", str(GUIDE_IMG)]))
        self.assertEqual(
            before,
            sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in GUIDE_IMG.iterdir()),
            "--check rewrote the committed files",
        )

    def test_check_rejects_a_capture_wider_than_the_cap(self):
        _write_png(self.tmp / "wide.png", optimize_guide_img.MAX_WIDTH * 2, 100)
        failures = optimize_guide_img.check(str(self.tmp))
        self.assertTrue(any("px wide" in f for f in failures), failures)
        self.assertEqual(1, optimize_guide_img.main(["--check", str(self.tmp)]))

    def test_check_rejects_a_full_colour_png(self):
        _write_png(self.tmp / "rgb.png", 200, 100, mode="RGB")
        failures = optimize_guide_img.check(str(self.tmp))
        self.assertTrue(any("palette" in f for f in failures), failures)

    def test_check_rejects_an_over_budget_png(self):
        _write_png(self.tmp / "shot.png", 200, 120)
        self.assertEqual([], optimize_guide_img.check(str(self.tmp)))
        real = optimize_guide_img.MAX_PNG_BYTES
        optimize_guide_img.MAX_PNG_BYTES = 8
        self.addCleanup(setattr, optimize_guide_img, "MAX_PNG_BYTES", real)
        failures = optimize_guide_img.check(str(self.tmp))
        self.assertTrue(any("bytes" in f for f in failures), failures)

    def test_check_rejects_an_over_budget_directory(self):
        _write_png(self.tmp / "shot.png", 200, 120)
        real = optimize_guide_img.MAX_TOTAL_BYTES
        optimize_guide_img.MAX_TOTAL_BYTES = 8
        self.addCleanup(setattr, optimize_guide_img, "MAX_TOTAL_BYTES", real)
        failures = optimize_guide_img.check(str(self.tmp))
        self.assertTrue(any("directory" in f for f in failures), failures)

    def test_check_rejects_an_empty_directory(self):
        with self.assertRaises(SystemExit):
            optimize_guide_img.check(str(self.tmp))


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestOptimizeIsReproducible(unittest.TestCase):
    """The tool, not a hand edit, is what produced the committed bytes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webcam_guide_opt_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_rerunning_on_the_committed_bytes_is_a_no_op(self):
        """Median cut is not a byte-exact fixed point, so the second pass has to
        be an explicit fast path rather than an accident of the quantizer."""
        for name in optimize_guide_img.pngs(GUIDE_IMG):
            shutil.copy2(GUIDE_IMG / name, self.tmp / name)
        before = {p.name: p.read_bytes() for p in self.tmp.iterdir()}
        for name in optimize_guide_img.pngs(str(self.tmp)):
            _, after, changed = optimize_guide_img.optimize(str(self.tmp / name))
            self.assertFalse(changed, name)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.tmp.iterdir()})

    def test_a_full_resolution_capture_comes_back_capped(self):
        """The regression this guards is a fresh 1x capture landing at 1440px."""
        target = self.tmp / "capture.png"
        with Image.open(GUIDE_IMG / "all-grid.png") as im:
            # Stand in for a 1440x900 deviceScaleFactor-1 Playwright capture.
            im.convert("RGB").resize((1440, 900), Image.LANCZOS).save(target, format="PNG")
        before = target.stat().st_size
        self.assertGreater(before, 0)
        _, after, changed = optimize_guide_img.optimize(str(target))
        self.assertTrue(changed)
        with Image.open(target) as im:
            self.assertEqual(optimize_guide_img.MAX_WIDTH, im.width)
            self.assertEqual(im.mode, "P")
        self.assertLess(after, before)
        self.assertLessEqual(after, optimize_guide_img.MAX_PNG_BYTES)

    def test_optimize_never_upscales_a_small_capture(self):
        target = self.tmp / "small.png"
        _write_png(target, 320, 200)
        optimize_guide_img.optimize(str(target))
        with Image.open(target) as im:
            self.assertEqual((320, 200), im.size)

    def test_optimize_leaves_the_gifs_alone(self):
        """make_gifs.py owns the GIFs and tests/test_make_gifs.py budgets them."""
        for name in GIFS:
            self.assertTrue((GUIDE_IMG / name).is_file(), name)
        before = {n: (GUIDE_IMG / n).read_bytes() for n in GIFS}
        for name in GIFS:
            shutil.copy2(GUIDE_IMG / name, self.tmp / name)
        # The tool only globs *.png, so a GIF in the same directory is inert.
        self.assertNotIn("visit-player.gif", optimize_guide_img.pngs(str(self.tmp)))
        self.assertEqual(
            before, {n: (GUIDE_IMG / n).read_bytes() for n in GIFS},
            "optimize_guide_img touched a committed GIF",
        )

    def test_png_round_trip_is_byte_identical(self):
        """Two independent runs from the same input must produce the same bytes."""
        src = GUIDE_IMG / "status.png"
        with Image.open(src) as im:
            full = im.convert("RGB").resize((1440, 900), Image.LANCZOS)
        digests = []
        for run in ("a", "b"):
            out = self.tmp / ("%s.png" % run)
            full.save(out, format="PNG")
            optimize_guide_img.optimize(str(out))
            digests.append(out.read_bytes())
        self.assertEqual(digests[0], digests[1])


if __name__ == "__main__":
    unittest.main()
