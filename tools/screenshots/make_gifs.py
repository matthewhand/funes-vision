#!/usr/bin/env python3
"""Generate animated showcase GIFs from the synthetic fixture gallery.

These GIFs are built from the fictional stills in
``tools/screenshots/fixtures/gallery`` -- never real camera footage from
``/mnt/models/Webcam21`` or ``/mnt/models/Webcam22``.

Frames are assembled with :func:`integrations.media.build_gif`, the same helper
the gallery's *Download GIF* button uses, so the committed animations match the
in-app export (frame cap, 2.5 fps duration, and ``optimize=True`` palette).

Usage:
  python3 tools/screenshots/make_gifs.py            # write the committed GIFs
  python3 tools/screenshots/make_gifs.py --check    # write + verify frame counts
  python3 tools/screenshots/make_gifs.py --out /tmp/gifs --no-demo

Outputs:
  docs/guide/img/timeline-flipbook.gif   every still, catalog order
  docs/guide/img/visit-player.gif        person + dog visit frames
  tools/demo/demo.gif                    lightweight demo-bundle animation
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GALLERY = HERE / "fixtures" / "gallery"
GUIDE_IMG = REPO / "docs" / "guide" / "img"
DEMO_GIF = REPO / "tools" / "demo" / "demo.gif"

sys.path.insert(0, str(REPO))
from integrations.media import build_gif  # noqa: E402  (sys.path set above)

# Narrower than the app's 480 px default so each committed GIF stays small.
TIMELINE_WIDTH = 360
VISIT_WIDTH = 400
DEMO_WIDTH = 320


def _load_json(name, fallback):
    path = GALLERY / name
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text())
    except ValueError:
        return fallback


def catalog_order():
    """Fixture filenames newest-first, as the gallery serves them."""
    return _load_json("images.json", [])


def _flagged(analysis, name, *keys):
    entry = analysis.get(name) or {}
    return any(entry.get(k) for k in keys)


def timeline_frames():
    """Every gallery still, in catalog order (newest first)."""
    return [GALLERY / n for n in catalog_order() if (GALLERY / n).is_file()]


def visit_frames():
    """Person and dog visit frames, in catalog order (matches app export)."""
    analysis = _load_json("analysis.json", {})
    names = [n for n in catalog_order() if _flagged(analysis, n, "person", "dog")]
    return [GALLERY / n for n in names if (GALLERY / n).is_file()]


def _make(frames, out_path, width):
    if not frames:
        raise SystemExit(f"no fixture frames for {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = build_gif(frames, str(out_path), width=width)
    if not result:
        raise SystemExit(f"build_gif produced nothing for {out_path}")
    return out_path


def _frame_count(path):
    from PIL import Image

    with Image.open(path) as im:
        return getattr(im, "n_frames", 1)


def generate(out_dir=GUIDE_IMG, demo=True):
    made = [
        _make(timeline_frames(), out_dir / "timeline-flipbook.gif", TIMELINE_WIDTH),
        _make(visit_frames(), out_dir / "visit-player.gif", VISIT_WIDTH),
    ]
    if demo:
        made.append(_make(visit_frames(), DEMO_GIF, DEMO_WIDTH))
    return made


def report(paths):
    total = 0
    for path in paths:
        size = path.stat().st_size
        total += size
        frames = _frame_count(path)
        try:
            shown = path.relative_to(REPO)
        except ValueError:
            shown = path
        print(f"{shown}: {frames} frames, {size:,} bytes")
        if frames < 2:
            raise SystemExit(f"{path} is not animated (frame count {frames})")
    print(f"total: {total:,} bytes")
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=GUIDE_IMG,
                    help="output dir for the commit GIFs (default: docs/guide/img)")
    ap.add_argument("--no-demo", action="store_true",
                    help="skip tools/demo/demo.gif")
    ap.add_argument("--check", action="store_true",
                    help="report frames/sizes (always done) and fail if not animated")
    args = ap.parse_args(argv)
    report(generate(args.out, demo=not args.no_demo))


if __name__ == "__main__":
    main()
