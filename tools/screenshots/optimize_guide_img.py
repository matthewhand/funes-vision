#!/usr/bin/env python3
"""Re-encode the published user-guide screenshots as 256-colour PNGs.

Playwright captures at 1x are 24-bit RGB PNGs that weigh ~0.5-1 MB each.
The gallery UI is flat-colour with a few photographic stills, so an
adaptive 256-colour palette is visually indistinguishable at guide size
while cutting the directory roughly in half (see issue #79).

Run after `PUBLISH_GUIDE_IMG=1 bash tools/screenshots/run_shots.sh`:

    python3 tools/screenshots/optimize_guide_img.py

Pass a directory to override the default (`docs/guide/img`).
"""
import os
import sys
from PIL import Image

DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docs",
    "guide",
    "img",
)


def optimize(path):
    before = os.path.getsize(path)
    with Image.open(path) as im:
        im.load()
        out = im.convert("RGB").quantize(
            colors=256,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.FLOYDSTEINBERG,
        )
    tmp = path + ".tmp"
    out.save(tmp, format="PNG", optimize=True, compress_level=9)
    os.replace(tmp, path)
    after = os.path.getsize(path)
    print("%-24s %8d -> %8d" % (os.path.basename(path), before, after))
    return before, after


def main():
    directory = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    files = sorted(f for f in os.listdir(directory) if f.endswith(".png"))
    if not files:
        sys.exit("no PNGs in " + directory)
    before = after = 0
    for name in files:
        b, a = optimize(os.path.join(directory, name))
        before += b
        after += a
    print("total                    %8d -> %8d (%.0f%% saved)" % (before, after, 100 * (1 - after / before)))


if __name__ == "__main__":
    main()
