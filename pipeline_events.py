"""Append-only JSONL bus so Live does not depend only on catalog mtime polls.

The analyzer calls emit(); the API tails with iter_since(byte_offset).
events.jsonl lives next to analyze_images.py (gitignored). This module does
not import analyze_images (cv2).
"""
import json
import os
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_FILE = os.path.join(BASE_DIR, "events.jsonl")

# Rotate when the log is large. Size is the cheap check; MAX_LINES is an
# in-process emit count so tiny lines cannot grow without bound either.
MAX_BYTES = 2_000_000
MAX_LINES = 5000
KEEP_LINES = 2000

_lock = threading.Lock()
_writes = 0


def emit(event, **payload):
    """Append {"ts": unix float, "event": event, ...payload}. Never raises."""
    global _writes
    try:
        rec = dict(payload)
        rec["ts"] = time.time()
        rec["event"] = event
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        with _lock:
            with open(EVENTS_FILE, "ab") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            _writes += 1
            try:
                size = os.path.getsize(EVENTS_FILE)
            except OSError:
                size = 0
            if size > MAX_BYTES or _writes >= MAX_LINES:
                _rotate_locked()
    except Exception as exc:
        print(f"[pipeline_events] emit failed: {exc}")


def iter_since(offset):
    """Return (new_byte_offset, [event dicts]) for complete lines after offset.

    If the file is missing or has shrunk past offset, start at 0. Junk /
    incomplete / non-object lines are skipped. Incomplete trailing bytes are
    left unread so the next tail can complete them.
    """
    try:
        offset = int(offset or 0)
    except (TypeError, ValueError):
        offset = 0
    if offset < 0:
        offset = 0

    try:
        with open(EVENTS_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if offset > size:
                offset = 0
            if offset == size:
                return offset, []
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return 0, []

    last_nl = chunk.rfind(b"\n")
    if last_nl < 0:
        return offset, []

    complete = chunk[: last_nl + 1]
    new_offset = offset + len(complete)
    events = []
    for raw in complete.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict):
            events.append(rec)
    return new_offset, events


def _rotate_locked():
    """Rewrite EVENTS_FILE to its last KEEP_LINES lines. Caller holds _lock."""
    global _writes
    keep = max(1, int(KEEP_LINES))
    with open(EVENTS_FILE, "rb") as f:
        data = f.read()
    lines = data.splitlines(keepends=True)
    tail = lines[-keep:]
    tmp = f"{EVENTS_FILE}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp, "wb") as f:
            f.writelines(tail)
            if tail and not tail[-1].endswith(b"\n"):
                f.write(b"\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, EVENTS_FILE)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _writes = len(tail)
