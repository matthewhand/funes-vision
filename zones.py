"""Spatial regions for the fast detector and for scan gates.

A region is a normalized polygon (origin top-left, 0..1) on one camera.

mode=ignore (default): YOLO still runs on the whole still; a detection is
dropped only when its bounding-box centre sits inside an enabled region
for that label. Used to mute a parked car that is in every front-camera
frame, without dropping a car on the street or one leaving the driveway.

mode=gate: do not drop the detection. The named scan (e.g. porch) is not
sent to e2b; the flag is set from geometry instead (person centre on the
grey tiles → porch_access, path/street → false).
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


def region_mode(region):
    m = str((region or {}).get("mode") or "ignore").lower().strip()
    return m if m in ("ignore", "gate") else "ignore"


def region_id(region):
    """Stable id: explicit `id`, else gate scan name, else parked_car."""
    rid = (region or {}).get("id")
    if isinstance(rid, str) and rid.strip():
        return rid.strip()
    if region_mode(region) == "gate":
        scan = str((region or {}).get("scan") or "").strip()
        if scan:
            return scan
    return "parked_car"


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
        if region_mode(region) != "ignore":
            continue
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


def gated_scan_ids(camera_kind, regions):
    """Scan ids whose e2b question is replaced by a geometry gate."""
    out = set()
    for region in regions or []:
        if region_mode(region) != "gate" or not region_enabled(region):
            continue
        if not region_for_camera(region, camera_kind):
            continue
        scan = str(region.get("scan") or "").strip()
        if scan:
            out.add(scan)
    return out


def _centre_xy(pt):
    if not isinstance(pt, (list, tuple)) or len(pt) < 2:
        return None
    try:
        return float(pt[0]), float(pt[1])
    except (TypeError, ValueError):
        return None


def scan_gate_flags(camera_kind, centres, regions):
    """Flags decided by gate polygons (currently porch_access).

    Only emitted when a person/face/body centre exists so a dog-only
    still does not grow a spurious porch_access=false.
    """
    flags = {}
    centres = centres if isinstance(centres, dict) else {}
    for region in regions or []:
        if region_mode(region) != "gate" or not region_enabled(region):
            continue
        if not region_for_camera(region, camera_kind):
            continue
        scan = str(region.get("scan") or "").strip()
        if scan != "porch":
            continue
        labels = region.get("labels") or ["person", "face", "body"]
        hit = False
        saw = False
        poly = region.get("polygon") or []
        for lab in labels:
            xy = _centre_xy(centres.get(str(lab).lower()))
            if xy is None:
                continue
            saw = True
            if point_in_polygon(xy[0], xy[1], poly):
                hit = True
                break
        if saw:
            flags["porch_access"] = hit
    return flags


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
        if "id" in region and not (isinstance(region["id"], str) and region["id"].strip()):
            return False
        mode = region.get("mode", "ignore")
        if mode not in ("ignore", "gate"):
            return False
        if mode == "gate":
            scan = region.get("scan")
            if not isinstance(scan, str) or not scan.strip():
                return False
        labels = region.get("labels", ["car"])
        if not isinstance(labels, list) or not labels:
            return False
        if not all(isinstance(lbl, str) and lbl.strip() for lbl in labels):
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
