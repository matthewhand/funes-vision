#!/usr/bin/env python3
"""Generate animated showcase GIFs from the synthetic fixture gallery.

These GIFs are built from the fictional stills in
``tools/screenshots/fixtures/gallery`` -- never real camera footage from
``/mnt/models/Webcam21`` or ``/mnt/models/Webcam22``.

Frames are assembled with :func:`integrations.media.build_gif`, the same helper
the gallery's *Download GIF* button uses, so the frame cap, width and
``optimize=True`` palette match the in-app export. Two deliberate differences:

* frames are ordered **oldest first** (``images.json`` is newest-first, which
  makes every capture gap negative and every hold collapse to ``MIN_FRAME_MS``),
  matching the in-app flipbook's ascending sort;
* the hold is pinned to the flipbook rate ``FRAME_MS`` instead of the fixture
  cadence -- the synthetic gallery spans a fictional day, which as real gaps
  would be a mostly-frozen 30-second clip.

Usage:
  python3 tools/screenshots/make_gifs.py            # write the committed GIFs
  python3 tools/screenshots/make_gifs.py --check    # validate the committed GIFs
  python3 tools/screenshots/make_gifs.py --out /tmp/gifs --no-demo

Outputs (all under ``--out``; the demo GIF's default home is ``tools/demo``):
  docs/guide/img/timeline-flipbook.gif   every still, oldest first
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
from integrations.media import FRAME_MS, build_gif, parse_frame_timestamp  # noqa: E402

# Narrower than the app's 480 px default so each committed GIF stays small.
TIMELINE_WIDTH = 360
VISIT_WIDTH = 400
DEMO_WIDTH = 320

# Every hold: the 2.5 fps flipbook rate the in-app player uses.
FLIPBOOK_MS = FRAME_MS
# Keep the committed binaries cheap to load from the docs pages.
MAX_GIF_BYTES = 1_500_000


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


def _oldest_first(paths):
    """Sort by the filename clock, ascending; untimed names last (stable).

    ``images.json`` is newest-first, so feeding it straight to ``build_gif``
    yields negative gaps and an 80 ms strobe. Ascending matches the in-app
    flipbook sort (index.html) and makes the gaps meaningful.
    """
    return sorted(paths,
                  key=lambda p: (parse_frame_timestamp(p) is None,
                                 parse_frame_timestamp(p) or 0.0))


def timeline_frames():
    """Every gallery still, oldest first."""
    return _oldest_first(GALLERY / n for n in catalog_order()
                         if (GALLERY / n).is_file())


def visit_frames():
    """Person and dog visit frames, oldest first (matches the in-app export)."""
    analysis = _load_json("analysis.json", {})
    names = [n for n in catalog_order() if _flagged(analysis, n, "person", "dog")]
    return _oldest_first(GALLERY / n for n in names
                         if (GALLERY / n).is_file())


def _make(frames, out_path, width):
    if not frames:
        raise SystemExit(f"no fixture frames for {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = build_gif(frames, str(out_path), width=width, frame_ms=FLIPBOOK_MS)
    if not result:
        raise SystemExit(f"build_gif produced nothing for {out_path}")
    return out_path


def _frame_count(path):
    from PIL import Image

    with Image.open(path) as im:
        return getattr(im, "n_frames", 1)


def _durations(path):
    from PIL import Image

    out = []
    with Image.open(path) as im:
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            out.append(im.info.get("duration"))
    return out


def demo_path(out_dir=GUIDE_IMG):
    """``--out`` redirects the demo GIF too; the default keeps it in tools/demo."""
    out_dir = Path(out_dir)
    return DEMO_GIF if out_dir.resolve() == GUIDE_IMG else out_dir / DEMO_GIF.name


def expected_paths(out_dir=GUIDE_IMG, demo=True):
    """Where the GIFs live for ``out_dir`` -- no writes, so ``--check`` can
    validate the committed binaries."""
    paths = [out_dir / "timeline-flipbook.gif", out_dir / "visit-player.gif"]
    if demo:
        paths.append(demo_path(out_dir))
    return paths


def generate(out_dir=GUIDE_IMG, demo=True):
    made = [
        _make(timeline_frames(), out_dir / "timeline-flipbook.gif", TIMELINE_WIDTH),
        _make(visit_frames(), out_dir / "visit-player.gif", VISIT_WIDTH),
    ]
    if demo:
        made.append(_make(visit_frames(), demo_path(out_dir), DEMO_WIDTH))
    return made


def report(paths, verify=False):
    """Print frames/delays/sizes; ``verify`` also enforces the invariants the
    committed GIFs must keep (animated, one hold, under budget)."""
    total = 0
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        size = path.stat().st_size
        total += size
        frames = _frame_count(path)
        delays = _durations(path)
        try:
            shown = path.relative_to(REPO)
        except ValueError:
            shown = path
        print(f"{shown}: {frames} frames, {size:,} bytes, delays={delays}")
        if frames < 2:
            raise SystemExit(f"{path} is not animated (frame count {frames})")
        if verify:
            _verify(path, size, delays)
    print(f"total: {total:,} bytes")
    return total


def _verify(path, size, delays):
    """Fail on drift from what the tool writes today: the holds must all be the
    flipbook rate (an 80 ms strobe or cadence-jitter shows up here) and the file
    must stay small enough to inline."""
    if set(delays) != {FLIPBOOK_MS}:
        raise SystemExit(
            f"{path} delays {sorted(set(delays))} are not the uniform "
            f"{FLIPBOOK_MS} ms flipbook rate -- regenerate it")
    if size > MAX_GIF_BYTES:
        raise SystemExit(
            f"{path} is {size:,} bytes (budget {MAX_GIF_BYTES:,})")


def check(paths):
    """Read-only validation of already-committed GIFs (used by ``--check``)."""
    return report(paths, verify=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=GUIDE_IMG,
                    help="output dir for the commit GIFs (default: docs/guide/img)")
    ap.add_argument("--no-demo", action="store_true",
                    help="skip tools/demo/demo.gif")
    ap.add_argument("--check", action="store_true",
                    help="validate the committed GIFs instead of rewriting them")
    args = ap.parse_args(argv)
    if args.check:
        check(expected_paths(args.out, demo=not args.no_demo))
        return
    report(generate(args.out, demo=not args.no_demo))


if __name__ == "__main__":
    main()
