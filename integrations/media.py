"""Turn a burst's frame sequence into a small shareable animation.

``build_gif`` produces a looping GIF (auto-plays inline in Slack). When that
gets too big -- GIF compresses poorly -- the caller falls back to
``build_mp4`` (ffmpeg/H.264), which is far smaller and Slack renders inline
with a scrub control. Both downscale and cap the frame count so a 200-frame
burst still yields a lightweight clip.
"""
import os
import subprocess
import tempfile

MAX_FRAMES = 24
WIDTH = 480
FRAME_MS = 400  # 2.5 fps, matching the in-app flipbook (openEventPlayer)


def _sample(frame_paths, cap=MAX_FRAMES):
    """Existing frames only, evenly downsampled to at most ``cap``."""
    paths = [p for p in frame_paths if p and os.path.exists(p)]
    if len(paths) <= cap:
        return paths
    step = len(paths) / cap
    return [paths[int(i * step)] for i in range(cap)]


def build_gif(frame_paths, out_path, width=WIDTH):
    """Assemble frames into a looping GIF. Returns ``out_path`` or ``None``."""
    from PIL import Image
    frames = _sample(frame_paths)
    if not frames:
        return None
    imgs = []
    for p in frames:
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            continue
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))))
        imgs.append(im)
    if not imgs:
        return None
    imgs[0].save(out_path, save_all=True, append_images=imgs[1:],
                 duration=FRAME_MS, loop=0, optimize=True)
    return out_path


def build_mp4(frame_paths, out_path, width=WIDTH):
    """Assemble frames into an H.264 MP4 via ffmpeg. Returns path or ``None``.

    ffmpeg's image2 demuxer needs a contiguous numbered sequence, so the
    sampled frames are symlinked into a temp dir first.
    """
    frames = _sample(frame_paths)
    if not frames:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        for i, p in enumerate(frames):
            try:
                os.symlink(os.path.abspath(p), os.path.join(tmp, f"f{i:04d}.jpg"))
            except OSError:
                return None
        cmd = [
            "ffmpeg", "-y", "-framerate", "2.5",
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
