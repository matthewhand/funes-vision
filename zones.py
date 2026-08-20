"""Spatial ignore regions for the fast detector.

A region is a normalized polygon (origin top-left, 0..1) on one camera.
YOLO still runs on the whole still; a detection is dropped only when its
bounding-box centre sits inside an enabled region for that label.

Used to mute a parked car that is in every front-camera frame, without
dropping a car on the street or one leaving the driveway.
"""


def point_in_polygon(x, y, poly):
    """Even-odd ray test. poly is [[x,y], ...] in the same units as x,y."""
    if not poly or len(poly) < 3:
        return False
    try:
        x = float(x)
        y = float(y)
    except (TypeError, ValueError):
        return False
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        try:
            x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
        except (TypeError, ValueError):
            return False
        if (y1 > y) != (y2 > y):
            denom = (y2 - y1)
            if denom == 0:
                continue
            xinters = (x2 - x1) * (y - y1) / denom + x1
            if x < xinters:
                inside = not inside
    return inside


def region_enabled(region):
    if not isinstance(region, dict):
        return False
    if region.get("enabled") is False:
        return False
    poly = region.get("polygon")
    return isinstance(poly, list) and len(poly) >= 3


def region_for_camera(region, camera_kind):
    cam = str((region or {}).get("camera") or "front").lower()
    kind = str(camera_kind or "front").lower()
    if cam in ("front", "back"):
        return cam == kind
    return cam in kind


def detection_ignored(label, cx, cy, camera_kind, regions):
    """True when this detector hit should be dropped (parked-car bay, etc.)."""
    if not regions or not label:
        return False
    lab = str(label).lower()
    for region in regions:
        if not region_enabled(region):
            continue
        if not region_for_camera(region, camera_kind):
            continue
        labels = region.get("labels") or ["car"]
        want = {str(x).lower() for x in labels if x}
        if lab not in want:
            continue
        if point_in_polygon(cx, cy, region.get("polygon") or []):
            return True
    return False


def ignore_regions_valid(value):
    """Shape check for settings.json / POST /api/settings."""
    if not isinstance(value, list):
        return False
    for region in value:
        if not isinstance(region, dict):
            return False
        cam = region.get("camera", "front")
        if not isinstance(cam, str) or not cam.strip():
            return False
        if "enabled" in region and not isinstance(region["enabled"], bool):
            return False
        labels = region.get("labels", ["car"])
        if not isinstance(labels, list) or not labels:
            return False
        if not all(isinstance(l, str) and l.strip() for l in labels):
            return False
        poly = region.get("polygon")
        if not isinstance(poly, list) or len(poly) < 3 or len(poly) > 8:
            return False
        for pt in poly:
            if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                return False
            try:
                x, y = float(pt[0]), float(pt[1])
            except (TypeError, ValueError):
                return False
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                return False
    return True
