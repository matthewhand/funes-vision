"""Turn a burst's frame sequence into a small shareable animation.

``build_gif`` produces a looping GIF (auto-plays inline in Slack). When that
gets too big -- GIF compresses poorly -- the caller falls back to
``build_mp4`` (ffmpeg/H.264), which is far smaller and Slack renders inline
with a scrub control. Both downscale and cap the frame count so a 200-frame
burst still yields a lightweight clip.
"""
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime

MAX_FRAMES = 24
WIDTH = 480
FRAME_MS = 400  # 2.5 fps fallback, matching the in-app flipbook (openEventPlayer)
MIN_FRAME_MS = 80
MAX_FRAME_MS = 4000
FPS_MIN = 0.5
FPS_MAX = 30.0

_FNAME_TS = re.compile(r"_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{3})?_")


def parse_frame_timestamp(name):
    """Epoch seconds from a Hikvision filename clock, or ``None`` if absent.

    Only *differences* matter for cadence, so the local-time interpretation
    cancels out as long as every frame shares it.
    """
    m = _FNAME_TS.search(str(name)) if name else None
    if not m:
        return None
    y, mo, d, h, mi, s, ms = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                        int(ms or 0) * 1000).timestamp()
    except (TypeError, ValueError):
        return None


def _coerce_ts(value):
    """``value`` as a finite epoch-seconds float, or ``None`` when unusable.

    Caller-supplied timestamps arrive straight from the JSON request body, so
    ``NaN``/``inf``/``None``/non-numeric all have to degrade to ``None`` -- the
    signal for "fall back to the filename clock" -- instead of raising inside
    the gap arithmetic.
    """
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return ts if math.isfinite(ts) else None


def _clamp_ms(ms, default=FRAME_MS):
    """Hold time in ``MIN_FRAME_MS..MAX_FRAME_MS``; unusable -> ``default``."""
    try:
        value = int(ms)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(MIN_FRAME_MS, min(MAX_FRAME_MS, value))


def _clamp_int(value, low, high, default):
    """``value`` as an int in ``low..high``; unusable -> ``default``."""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


def _clamp_width(width, default=WIDTH):
    """Positive output width; unusable or non-positive -> ``default``."""
    try:
        value = int(width)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if value > 0 else default


def _resolve_frames(frame_paths, timestamps=None, cap=MAX_FRAMES):
    """Existing frames as ``(path, timestamp)`` pairs, evenly downsampled to at
    most ``cap``. Supplied timestamps are carried through by position; a missing
    entry stays ``None`` so callers can fall back to the filename clock."""
    ts = list(timestamps) if timestamps else []
    existing = [(p, ts[i] if i < len(ts) else None)
                for i, p in enumerate(frame_paths) if p and os.path.exists(p)]
    if len(existing) <= cap:
        return existing
    step = len(existing) / cap
    return [existing[int(i * step)] for i in range(cap)]


def _sample(frame_paths, cap=MAX_FRAMES):
    """Existing frames only, evenly downsampled to at most ``cap``."""
    return [p for p, _ in _resolve_frames(frame_paths, None, cap)]


def _frame_timestamps(pairs):
    """Effective epoch seconds per pair: the supplied value (coerced to a finite
    float) when usable, else the filename clock. ``None`` marks a frame whose
    clock is unusable -- only that frame loses its timing."""
    out = []
    for path, supplied in pairs:
        ts = _coerce_ts(supplied)
        out.append(parse_frame_timestamp(path) if ts is None else ts)
    return out


def _gif_durations(pairs, fallback_ms=FRAME_MS, frame_ms=None):
    """Per-frame GIF hold times (ms) from the capture gaps, each clamped to
    ``MIN_FRAME_MS..MAX_FRAME_MS``.

    ``frame_ms`` pins every frame to one hold and skips the gap arithmetic
    (the docs flipbook rate). Otherwise a missing, non-finite or non-positive
    gap falls back to ``fallback_ms`` for *that* frame only, so one untimed
    frame no longer voids the cadence of the rest of the sequence -- the same
    per-frame rule as the in-app ``frameDurations`` (index.html).
    """
    n = len(pairs)
    if n == 0:
        return []
    fallback = _clamp_ms(fallback_ms)
    if frame_ms is not None:
        return [_clamp_ms(frame_ms)] * n
    if n == 1:
        return [fallback]
    ts = _frame_timestamps(pairs)
    windows = [(ts[i], ts[i + 1]) for i in range(n - 1)]
    positive = sorted(b - a for a, b in windows
                      if a is not None and b is not None and b > a)
    if not positive:
        return [fallback] * n
    holds = [fallback if a is None or b is None or b <= a
             else _clamp_ms(round((b - a) * 1000)) for a, b in windows]
    # The last frame has no successor, so it holds for the median real gap.
    holds.append(_clamp_ms(round(positive[len(positive) // 2] * 1000)))
    return holds


def _cadence_fps(pairs):
    """Median sampled fps from the capture gaps, or ``None`` when untimed.

    Frames with an unusable clock are skipped rather than voiding the whole
    sequence, matching :func:`_gif_durations`.
    """
    ts = [t for t in _frame_timestamps(pairs) if t is not None]
    if len(ts) < 2:
        return None
    gaps = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
    median = gaps[len(gaps) // 2]
    return 1.0 / median if median > 0 else None


def _clamp_fps(fps):
    """Coerce ``fps`` to ``FPS_MIN..FPS_MAX``; anything unusable -> flipbook."""
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        return 1000.0 / FRAME_MS
    if fps != fps or fps in (float("inf"), float("-inf")):
        return 1000.0 / FRAME_MS
    return max(FPS_MIN, min(FPS_MAX, fps))


def build_gif(frame_paths, out_path, width=WIDTH, timestamps=None, colors=256,
              frame_ms=None):
    """Assemble frames into a looping GIF. Returns ``out_path`` or ``None``.

    ``timestamps`` are epoch seconds aligned with ``frame_paths``; when absent
    (or unusable) the filename clock is tried, then ``FRAME_MS``. ``colors``
    (2..256) sizes the adaptive palette used for each frame. ``frame_ms`` pins
    every frame to that one hold instead of following the capture cadence.
    Unusable ``width``/``colors``/``timestamps`` fall back to the defaults
    rather than raising, so a hostile payload can't 500 the export.
    """
    from PIL import Image
    pairs = _resolve_frames(frame_paths, timestamps)
    if not pairs:
        return None
    resample = getattr(Image, "Resampling", Image).LANCZOS
    adaptive = getattr(Image, "ADAPTIVE", 1)
    colors = _clamp_int(colors, 2, 256, 256)
    width = _clamp_width(width)
    imgs, kept = [], []
    for p, t in pairs:
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            continue
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))),
                           resample)
        imgs.append(im.convert("P", palette=adaptive, colors=colors))
        kept.append((p, t))
    if not imgs:
        return None
    imgs[0].save(out_path, save_all=True, append_images=imgs[1:],
                 duration=_gif_durations(kept, frame_ms=frame_ms), loop=0,
                 optimize=True, disposal=2)
    return out_path


def build_mp4(frame_paths, out_path, width=WIDTH, timestamps=None, fps=None):
    """Assemble frames into an H.264 MP4 via ffmpeg. Returns path or ``None``.

    Playback rate is the explicit ``fps`` when given, else the median sampled
    cadence, else ``FRAME_MS`` -- clamped to ``FPS_MIN..FPS_MAX``. ffmpeg's
    image2 demuxer needs a contiguous numbered sequence, so the sampled frames
    are symlinked into a temp dir first.
    """
    pairs = _resolve_frames(frame_paths, timestamps)
    if not pairs:
        return None
    if fps is None:
        fps = _cadence_fps(pairs)
    fps = _clamp_fps(fps)
    frames = [p for p, _ in pairs]
    with tempfile.TemporaryDirectory() as tmp:
        for i, p in enumerate(frames):
            try:
                os.symlink(os.path.abspath(p), os.path.join(tmp, f"f{i:04d}.jpg"))
            except OSError:
                return None
        cmd = [
            "ffmpeg", "-y", "-framerate", f"{fps:.6g}",
            "-i", os.path.join(tmp, "f%04d.jpg"),
            # yuv420p needs even dimensions; -2 keeps aspect ratio even
            "-vf", f"scale={width}:-2", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            out_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            return None
    return out_path if os.path.exists(out_path) else None
