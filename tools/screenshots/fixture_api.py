"""Fixture-backed payloads for the /api routes the screenshot stub and the
static demo bundle serve.

Both tools used to hardcode their own answer, which is how the demo ended up
generated against a stale API surface (the SPA's /api/cameras and
/api/taxonomy were never served, so a visitor saw the SPA's offline fallback
instead of the real code path). One definition, imported by
tools/screenshots/proxy.py and tools/demo/build_demo.py, so a new endpoint
cannot be half-implemented in one tool and missing from the other.

Two payloads:

  * ``cameras()`` — derived from the synthetic fixture gallery itself. The
    stills are Hikvision-named, so the leading IP is the camera's identity
    (``10.0.0.21`` = CAM 01 FRONT, ``10.0.0.22`` = CAM 02 BACK in the OSD
    make_fixtures.py stamps). Deriving from the files on disk means the
    registry cannot claim a camera the fixtures do not contain, and a fixture
    with a third camera yields a third entry instead of vanishing.
  * ``taxonomy()`` — imported from the real ``taxonomy.py``, the same module
    api_server.py serves, so the stub cannot drift from the app.

Import-light on purpose: no cv2, no analyze_images, no /mnt/models.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

# Fixture camera identity: prefix -> registry metadata. `index` is the
# deterministic order the SPA's camera switcher and the demo's deck layout
# use; keep it aligned with the OSD CAM numbers make_fixtures.py draws.
CAMERA_FIXTURES = (
    {"ip": "10.0.0.21", "id": "Webcam21", "kind": "front", "label": "Front"},
    {"ip": "10.0.0.22", "id": "Webcam22", "kind": "back", "label": "Back"},
)
# Fallback for a fixture still whose prefix is not in the table above: still
# expose it (a camera the SPA cannot see is worse than an unlabelled one).
FALLBACK_CAMERA = {"kind": "front", "label": "Camera"}


def _load(path, fallback):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def camera_ip(filename):
    """Leading dotted IP of a Hikvision still name, or None."""
    head = str(filename).split("_", 1)[0]
    parts = head.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return head
    return None


def cameras(gallery_dir):
    """[{id, kind, label, source_dir, index}] for the fixture gallery.

    Only cameras with at least one frame in ``gallery_dir/images.json`` are
    returned, ordered by CAMERA_FIXTURES then anything unexpected, so the
    list matches what the gallery can actually show.
    """
    images = _load(os.path.join(gallery_dir, "images.json"), [])
    seen = []
    for name in images if isinstance(images, list) else []:
        ip = camera_ip(name)
        if ip and ip not in seen:
            seen.append(ip)
    meta = {c["ip"]: c for c in CAMERA_FIXTURES}
    return [
        {
            "id": meta[ip]["id"] if ip in meta else ip,
            "kind": meta[ip]["kind"] if ip in meta else FALLBACK_CAMERA["kind"],
            "label": meta[ip]["label"] if ip in meta else FALLBACK_CAMERA["label"],
            # Fixture-relative, never a /mnt/models path: the bundle ships the
            # stills beside index.html, and the SPA never reads source_dir.
            "source_dir": "fixtures/gallery",
            "index": i,
        }
        for i, ip in enumerate(seen)
    ]


def camera_of(filename):
    """Registry entry owning a still, by its leading IP. None if unknown."""
    ip = camera_ip(filename)
    if not ip:
        return None
    meta = {c["ip"]: c for c in CAMERA_FIXTURES}
    return meta.get(ip)


def _catalog(images, analysis, bursts, pins):
    names = set(images)
    return {
        "images": list(images),
        "analysis": {k: v for k, v in analysis.items() if k in names},
        "bursts": {k: v for k, v in bursts.items() if k in names},
        "pins": [p for p in pins if p in names],
        "thumbUrl": f"thumbs/{images[0]}" if images else None,
    }


def catalogs(gallery_dir, camera_id=None):
    """Fixture catalog payload in api_server's /api/catalogs shape.

    Omitting ``camera_id`` returns every camera keyed by id (the dashboard
    form); passing one returns that camera's catalog (the SPA's per-camera
    form). Both are derived from the same files the gallery serves, so the
    stub cannot advertise a camera whose frames the page cannot load.
    """
    images = _load(os.path.join(gallery_dir, "images.json"), [])
    analysis = _load(os.path.join(gallery_dir, "analysis.json"), {})
    bursts = _load(os.path.join(gallery_dir, "bursts.json"), {})
    pins = _load(os.path.join(gallery_dir, "pins.json"), [])
    images = [i for i in images if isinstance(i, str)] if isinstance(images, list) else []
    analysis = analysis if isinstance(analysis, dict) else {}
    bursts = bursts if isinstance(bursts, dict) else {}
    pins = [p for p in pins if isinstance(p, str)] if isinstance(pins, list) else []

    grouped = {c["id"]: [] for c in cameras(gallery_dir)}
    for name in images:
        owner = camera_of(name)
        if owner and owner["id"] in grouped:
            grouped[owner["id"]].append(name)
    if camera_id is not None:
        if camera_id not in grouped:
            return None
        return _catalog(grouped[camera_id], analysis, bursts, pins)
    return {cid: _catalog(names, analysis, bursts, pins) for cid, names in grouped.items()}


def taxonomy():
    """The canonical HA taxonomy, straight from the app's taxonomy.py.

    Returns None if the module cannot be imported, so a caller can fall back
    rather than crash a screenshot run from a partial checkout.
    """
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    try:
        import taxonomy as _taxonomy
    except Exception:
        return None
    try:
        return _taxonomy.payload()
    except Exception:
        return None
