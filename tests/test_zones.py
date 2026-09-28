"""Parked-car ignore triangle: mute the bay, keep street / leaving cars."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import zones

# Front-camera bay covering the parked orange SUV (left edge), not the street.
BAY = [[0.0, 0.22], [0.20, 0.45], [0.0, 0.72]]
REGION = {"camera": "front", "labels": ["car"], "polygon": BAY, "enabled": True}
# Grey tiled porch (right of frame). Path/street people sit outside.
PORCH = [[0.66, 0.28], [1.0, 0.22], [1.0, 1.0], [0.62, 1.0]]
PORCH_REGION = {
    "id": "porch", "camera": "front", "mode": "gate", "scan": "porch",
    "labels": ["person", "face", "body"], "polygon": PORCH, "enabled": True,
}
# The live site config is deliberately not committed; the two "matches_this_box"
# tests below can only assert against it when this box is the one it was
# calibrated on (e.g. on a dev host running the real cameras).
SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")


def live_settings():
    """This box's own settings.json, or None when it cannot be read.

    "Matches this box" asserts real `ignore_regions`, so it needs a *readable*
    file, not just an existing one. A corrupt settings.json is a fault for the
    pipeline to report loudly (#27) and a skip reason here -- the alternative is
    turning a local-config test into a red error for a box that is merely
    misconfigured.
    """
    import json
    try:
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
    except (OSError, ValueError):
        return None
    return settings if isinstance(settings, dict) else None


LIVE_SETTINGS = live_settings()


def calibrated_for_porch(settings):
    """True when `settings` is the box the committed porch/bay geometry came from.

    A *readable* settings.json is not enough. The BAY and PORCH polygons below
    were traced off real Front-camera stills on one particular installation, so
    they only describe a settings.json that has an enabled front-camera porch
    gate drawn on it. Any other settings.json -- a fresh copy of
    settings.example.json, a box aimed at a different scene, a deployment that
    has not drawn the porch region yet -- is a different box, not evidence that
    the committed constants are wrong, and it made these two tests fail (rather
    than skip) on any host that happened to have a settings.json.

    The gate is the operator's own declaration that the porch is calibrated: a
    front-camera region in gate mode scanned as "porch". It never looks at the
    polygon, the labels, or anything else the tests go on to assert, so on a
    calibrated box a drifted or rotated quad still fails exactly as it should.
    """
    if not isinstance(settings, dict):
        return False
    regions = settings.get("ignore_regions")
    if not isinstance(regions, list):
        return False
    for region in regions:
        if not isinstance(region, dict):
            continue
        if (zones.region_mode(region) == "gate"
                and zones.region_for_camera(region, "front")
                and str(region.get("scan") or "").strip() == "porch"
                and zones.region_enabled(region)):
            return True
    return False


# None = "not this box", so the two tests below skip. One shared reason string
# so a -v run says which condition applied instead of just "not this box".
SKIP_REASON = ("no readable live settings.json, or it has no enabled "
               "front-camera porch gate -- the committed porch/bay geometry is "
               "calibrated for one specific box")

CALIBRATED_BOX = calibrated_for_porch(LIVE_SETTINGS)


class TestPointInPolygon(unittest.TestCase):
    def test_parked_suv_centre_is_inside(self):
        self.assertTrue(zones.point_in_polygon(0.055, 0.436, BAY))
        self.assertTrue(zones.point_in_polygon(0.047, 0.405, BAY))

    def test_street_car_centre_is_outside(self):
        self.assertFalse(zones.point_in_polygon(0.158, 0.216, BAY))
        self.assertFalse(zones.point_in_polygon(0.187, 0.162, BAY))

    def test_empty_or_short_poly(self):
        self.assertFalse(zones.point_in_polygon(0.1, 0.1, []))
        self.assertFalse(zones.point_in_polygon(0.1, 0.1, [[0, 0], [1, 1]]))


class TestDetectionIgnored(unittest.TestCase):
    def test_parked_car_on_front_is_dropped(self):
        self.assertTrue(zones.detection_ignored("car", 0.055, 0.43, "front", [REGION]))

    def test_street_car_is_kept(self):
        self.assertFalse(zones.detection_ignored("car", 0.158, 0.216, "front", [REGION]))

    def test_person_in_the_bay_is_kept(self):
        self.assertFalse(zones.detection_ignored("person", 0.055, 0.43, "front", [REGION]))

    def test_back_camera_ignores_front_region(self):
        self.assertFalse(zones.detection_ignored("car", 0.055, 0.43, "back", [REGION]))

    def test_disabled_region_is_inert(self):
        r = dict(REGION, enabled=False)
        self.assertFalse(zones.detection_ignored("car", 0.055, 0.43, "front", [r]))

    def test_no_regions(self):
        self.assertFalse(zones.detection_ignored("car", 0.055, 0.43, "front", []))


class TestPorchGate(unittest.TestCase):
    def test_tile_person_is_porch(self):
        # Large right-side boxes from live Front stills (door/tiles).
        for cx, cy in ((0.879, 0.411), (0.840, 0.563), (0.758, 0.599), (0.905, 0.444)):
            self.assertTrue(zones.point_in_polygon(cx, cy, PORCH), (cx, cy))
        flags = zones.scan_gate_flags("front", {"person": (0.88, 0.45)}, [PORCH_REGION])
        self.assertEqual(flags, {"porch_access": True})

    def test_path_and_street_are_not_porch(self):
        for cx, cy in ((0.521, 0.282), (0.145, 0.186), (0.061, 0.364), (0.646, 0.298)):
            self.assertFalse(zones.point_in_polygon(cx, cy, PORCH), (cx, cy))
        flags = zones.scan_gate_flags("front", {"person": (0.52, 0.28)}, [PORCH_REGION])
        self.assertEqual(flags, {"porch_access": False})

    def test_gate_does_not_drop_the_person(self):
        self.assertFalse(zones.detection_ignored(
            "person", 0.88, 0.45, "front", [REGION, PORCH_REGION]))

    def test_gated_scan_ids(self):
        self.assertEqual(zones.gated_scan_ids("front", [REGION, PORCH_REGION]), {"porch"})
        self.assertEqual(zones.gated_scan_ids("back", [PORCH_REGION]), set())
        off = dict(PORCH_REGION, enabled=False)
        self.assertEqual(zones.gated_scan_ids("front", [off]), set())

    def test_dog_only_does_not_emit_porch(self):
        self.assertEqual(zones.scan_gate_flags("front", {"dog": (0.88, 0.45)}, [PORCH_REGION]), {})

    @unittest.skipUnless(CALIBRATED_BOX, SKIP_REASON)
    def test_settings_porch_quad_matches_this_box(self):
        regions = LIVE_SETTINGS.get("ignore_regions") or []
        self.assertEqual(zones.gated_scan_ids("front", regions), {"porch"})
        self.assertTrue(zones.scan_gate_flags(
            "front", {"person": (0.88, 0.45)}, regions)["porch_access"])
        self.assertFalse(zones.scan_gate_flags(
            "front", {"person": (0.52, 0.28)}, regions)["porch_access"])


class TestIgnoreRegionsValid(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(zones.ignore_regions_valid([REGION]))
        self.assertTrue(zones.ignore_regions_valid([]))

    @unittest.skipUnless(CALIBRATED_BOX, SKIP_REASON)
    def test_settings_default_triangle_matches_this_box(self):
        regions = LIVE_SETTINGS.get("ignore_regions") or []
        self.assertTrue(zones.detection_ignored("car", 0.055, 0.43, "front", regions))
        self.assertFalse(zones.detection_ignored("car", 0.158, 0.216, "front", regions))

    def test_rejects_junk(self):
        self.assertFalse(zones.ignore_regions_valid(None))
        self.assertFalse(zones.ignore_regions_valid({"polygon": BAY}))
        self.assertFalse(zones.ignore_regions_valid([{"camera": "front", "polygon": [[0, 0]]}]))
        self.assertFalse(zones.ignore_regions_valid([{
            "camera": "front", "polygon": [[0, 0], [1, 0], [2, 1]],
        }]))


class TestCalibrationGate(unittest.TestCase):
    """The skip gate itself, so "skip instead of fail" cannot become a cover.

    The point of calibrated_for_porch() is to skip on a *different* box and
    nothing else. These pin both halves: a settings.json with no porch gate is
    skipped, and one whose porch quad has drifted from the committed PORCH is
    still treated as this box -- so test_settings_porch_quad_matches_this_box
    goes on to fail on it, which is the whole point of keeping those tests.
    """

    # A porch quad translated so it no longer covers (0.879, 0.411). Same
    # camera, same mode, same scan id: still this box, wrong geometry.
    DRIFTED_PORCH = dict(PORCH_REGION, polygon=[[0.0, 0.0], [0.3, 0.0],
                                                [0.3, 0.3], [0.0, 0.3]])

    def test_calibrated_box_is_not_skipped(self):
        self.assertTrue(calibrated_for_porch(
            {"ignore_regions": [REGION, dict(PORCH_REGION)]}))

    def test_drifted_geometry_is_still_this_box(self):
        """The gate must not consult the geometry the tests assert on."""
        self.assertTrue(calibrated_for_porch(
            {"ignore_regions": [self.DRIFTED_PORCH]}))

    def test_not_this_box_is_skipped(self):
        for name, settings in (
            ("no settings.json", None),
            ("a settings.json with no ignore_regions", {"watch_dirs": []}),
            ("a settings.json that is not an object", ["nope"]),
            ("ignore_regions not a list", {"ignore_regions": {}}),
            ("only the car triangle", {"ignore_regions": [REGION]}),
            ("a porch gate on the other camera",
             {"ignore_regions": [dict(PORCH_REGION, camera="back")]}),
            ("a porch region that is not a gate",
             {"ignore_regions": [dict(PORCH_REGION, mode=None)]}),
            ("a porch gate the operator switched off",
             {"ignore_regions": [dict(PORCH_REGION, enabled=False)]}),
        ):
            with self.subTest(name):
                self.assertFalse(calibrated_for_porch(settings))


if __name__ == "__main__":
    unittest.main()
