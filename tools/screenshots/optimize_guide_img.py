#!/usr/bin/env python3
"""Re-encode the published user-guide screenshots as capped-width 256-colour PNGs.

Two separate costs are addressed here, and only the second one is new.

**Palette.** Playwright captures at 1x are 24-bit RGB PNGs that weigh ~0.5-1 MB
each. The gallery UI is flat-colour with a few photographic stills, so an
adaptive 256-colour palette is visually indistinguishable at guide size
(see issue #79).

**Resolution.** The guide paints every screenshot into the same box, and that
box is much smaller than the capture. Measured in Chromium against
`docs/USER-GUIDE.html`: `.wrap` is `max-width: 880px` with 20px side padding and
`figure` keeps the UA default 40px inline margin, so the widest an `<img>` is
ever laid out is **802 CSS px** -- 880 - 20 - 20 - 40 - 40 -- at any viewport of
880px or more, and 272 CSS px in the 390px phone column. The desktop captures
are 1440px wide (shots.js uses a 1440x900 viewport at deviceScaleFactor 1), so a
reader at 1x was already throwing away 44% of every row of pixels. The phone
captures are 780px (390 CSS px viewport at deviceScaleFactor 2) and are already
under the cap, so they pass through at native size.

`MAX_WIDTH` is therefore derived from the displayed width, not guessed: 1024px is
1.28x the 802 CSS px box, which leaves headroom for a 125%-zoomed browser and
for HiDPI while dropping the 1440->1024 pixels no reader could resolve. 1024 is
also ~1:1 for the markdown twin `docs/USER-GUIDE.md` on GitHub, whose content
column is wider than the HTML guide's, so both readers of these files stay
sharp. Change that one constant if the guide's layout ever moves.

Measured against the previous 1440px committed bytes, re-rendering both versions
into the 802 CSS px box the browser actually paints: worst-file MAE 0.68/255,
p95 3.0, PSNR 43.6 dB, against a ~1/255 and ~40 dB floor for visible change.
Screenshotting the whole page in Chromium before and after and diffing it
confirms it end to end: at 1440x900 the page is 95.9% bit-identical, MAE
0.169/255, PSNR 43.6 dB over every pixel of a 21171px-tall page, and the page
height is unchanged, so nothing reflowed. Inside the image boxes 93.4% of pixels
are bit-identical and only 0.16% move by more than 32/255.

On a 2x display the guide paints 1604 device px, and the capped 1024px capture is
a 1.57x browser upscale where 1440 was a 1.11x one. That is the one place this
is a real trade: the same comparison models MAE 2.51 (PSNR 30.9 dB) rather than
0.68. The browser's own 2x render of the page measured no worse than its 1x
render (whole-page MAE 0.174, PSNR 43.4 dB), so the resample stays smooth, but
the sharpness headroom for HiDPI is spent. Raise MAX_WIDTH to 1440 to buy it
back; the byte ceiling below is what stops that from happening by accident.

The cap is a mandate, not a target: a re-encode that came out *larger* than the
file it replaced is still written, so "no guide PNG is wider than MAX_WIDTH" stays
a single unconditional invariant the test can assert. Two very flat captures
(`help.png` +23 KB, `timeline.png` +1 KB) are heavier that way -- they are almost
uniform dark page, so a Lanczos resample turns large flat regions into gradients
and costs entropy rather than saving it. Trading 25 KB for an invariant that
cannot silently rot is the point.

The bytes are a committed artefact, so `--check` re-validates them read-only
(under the width cap and the byte ceilings below) and exits non-zero on a
regression. Nothing in the build runs it; `tests/test_guide_img_budget.py` does.

Run after `PUBLISH_GUIDE_IMG=1 bash tools/screenshots/run_shots.sh`:

    python3 tools/screenshots/optimize_guide_img.py

Pass a directory to override the default (`docs/guide/img`).
"""
import argparse
import io
import os
import sys
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docs",
    "guide",
    "img",
)

# Widest box an <img> in docs/USER-GUIDE.html is painted into, measured in
# Chromium. See the module docstring for the box-model arithmetic.
DISPLAY_CSS_WIDTH = 802
# 1.28x DISPLAY_CSS_WIDTH. Never upscales, so a capture already at or under the
# cap keeps its native geometry: the 780px phone captures are left alone.
MAX_WIDTH = 1024
# Enough for the flat theme chrome plus the photographic stills inside the grid.
# Reducing it further is what costs visible quality -- at 128 colours the
# worst-file PSNR at the display box drops to 38.3 dB, under the transparency
# floor.
COLORS = 256
# Ceilings, as a ratchet on the committed bytes. The published demo bundle ships
# this whole directory, so a capture that skips this script costs every reader of
# the guide the difference. MAX_PNG_BYTES is the largest committed screenshot
# (mobile-grid.png, a 780px phone capture) plus ~15% of headroom for a different
# fixture gallery; MAX_TOTAL_BYTES is the whole directory plus ~7%. Both fail
# the test if the encoder or the capture geometry drifts, and a revert to the
# full-resolution captures (3.06 MB) busts the total by 18%. `optimize()` exits
# non-zero on a directory that busts the total, so the run itself cannot quietly
# produce something the test would then reject.
MAX_PNG_BYTES = 420_000
MAX_TOTAL_BYTES = 2_600_000


def _dither_for(im):
    """Floyd-Steinberg, unless the palette would be a no-op.

    Dithering trades bytes for gradient smoothness, and it only does anything
    when the quantizer is actually dropping colours. Deriving the palette from
    the image that is about to be written -- rather than from a thumbnail of the
    full-size capture -- is what keeps the colour error transparent: a palette
    fitted to the capped image is fitted to the pixels that are actually shipped.
    Fitting it to the capture instead is ~9% smaller on disk and costs 14 dB of
    PSNR at the display box (29.0 vs 43.6 dB worst file), which is visible
    banding in the dark theme gradients. Bytes are not worth that.
    """
    if im.mode == "P" and len(set(im.tobytes())) <= COLORS:
        # Every source colour already has a palette slot: nothing to dither.
        return Image.Dither.NONE
    return Image.Dither.FLOYDSTEINBERG


def _fit(im, max_width=MAX_WIDTH):
    """Downscale to the cap, preserving aspect. Never upscales.

    Must be handed RGB, never a palette image: Lanczos on a `P` image
    interpolates palette *indices*, so the result is both the wrong colours and
    deceptively cheap to compress (adjacent indices are adjacent numbers).
    """
    if im.width <= max_width:
        return im
    return im.resize((max_width, max(1, round(im.height * max_width / im.width))),
                     Image.LANCZOS)


def _encode(im):
    buf = io.BytesIO()
    capped = _fit(im.convert("RGB"))
    capped.quantize(
        colors=COLORS,
        method=Image.Quantize.MEDIANCUT,
        dither=_dither_for(capped),
    ).save(buf, format="PNG", optimize=True, compress_level=9)
    return buf.getvalue()


def _is_encoded(im):
    """True if this file is already what this tool would have written.

    Median cut is not quite a fixed point on a 255-colour image -- it re-splits
    and shifts a handful of bytes -- so a plain second pass is not reproducible.
    An explicit fast path is: the committed artefacts are already in their final
    form, and re-running the tool must be a no-op rather than a slow drift.
    """
    return im.mode == "P" and im.width <= MAX_WIDTH


def pngs(directory):
    return sorted(f for f in os.listdir(directory) if f.lower().endswith(".png"))


def optimize(path):
    """Rewrite one screenshot in place. Returns (before, after, changed)."""
    before = os.path.getsize(path)
    with Image.open(path) as im:
        im.load()
        if _is_encoded(im):
            print("%-24s %8d -> %8d  (already encoded, left alone)"
                  % (os.path.basename(path), before, before))
            return before, before, False
        data = _encode(im)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    after = os.path.getsize(path)
    print("%-24s %8d -> %8d" % (os.path.basename(path), before, after))
    return before, after, True


def check(directory):
    """Read-only validation of the committed bytes. Returns a list of failures."""
    names = pngs(directory)
    if not names:
        raise SystemExit("no PNGs in " + directory)
    total = 0
    failures = []
    for name in names:
        path = os.path.join(directory, name)
        size = os.path.getsize(path)
        total += size
        with Image.open(path) as im:
            width, height, mode = im.width, im.height, im.mode
        if size > MAX_PNG_BYTES:
            failures.append(f"{name} is {size:,} bytes (ceiling {MAX_PNG_BYTES:,})")
        if width > MAX_WIDTH:
            failures.append(f"{name} is {width}px wide (cap {MAX_WIDTH}px)")
        if mode != "P":
            failures.append(f"{name} is mode {mode}, not an 8-bit palette")
        print("%-24s %5dx%-5d %-2s %8d" % (name, width, height, mode, size))
    print("total                    %8d (ceiling %d)" % (total, MAX_TOTAL_BYTES))
    if total > MAX_TOTAL_BYTES:
        failures.append(f"directory is {total:,} bytes (ceiling {MAX_TOTAL_BYTES:,})")
    for line in failures:
        print("FAIL " + line, file=sys.stderr)
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("directory", nargs="?", default=DEFAULT_DIR)
    ap.add_argument(
        "--check",
        action="store_true",
        help="validate the committed bytes read-only, do not rewrite",
    )
    args = ap.parse_args(argv)

    if args.check:
        return 1 if check(args.directory) else 0

    names = pngs(args.directory)
    if not names:
        sys.exit("no PNGs in " + args.directory)
    before = after = 0
    for name in names:
        b, a, _ = optimize(os.path.join(args.directory, name))
        before += b
        after += a
    print("total                    %8d -> %8d (%.0f%% saved)"
          % (before, after, 100 * (1 - after / before)))
    if after > MAX_TOTAL_BYTES:
        sys.exit(f"total {after:,} bytes exceeds ceiling {MAX_TOTAL_BYTES:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
