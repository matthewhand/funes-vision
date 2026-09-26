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
# tests below can only assert against it when it exists (e.g. on a dev host).
SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")


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

    @unittest.skipUnless(os.path.exists(SETTINGS_PATH), "live settings.json not present")
    def test_settings_porch_quad_matches_this_box(self):
        import json
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "settings.json")) as f:
            settings = json.load(f)
        regions = settings.get("ignore_regions") or []
        self.assertEqual(zones.gated_scan_ids("front", regions), {"porch"})
        self.assertTrue(zones.scan_gate_flags(
            "front", {"person": (0.88, 0.45)}, regions)["porch_access"])
        self.assertFalse(zones.scan_gate_flags(
            "front", {"person": (0.52, 0.28)}, regions)["porch_access"])


class TestIgnoreRegionsValid(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(zones.ignore_regions_valid([REGION]))
        self.assertTrue(zones.ignore_regions_valid([]))

    @unittest.skipUnless(os.path.exists(SETTINGS_PATH), "live settings.json not present")
    def test_settings_default_triangle_matches_this_box(self):
        import json
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "settings.json")) as f:
            settings = json.load(f)
        regions = settings.get("ignore_regions") or []
        self.assertTrue(zones.detection_ignored("car", 0.055, 0.43, "front", regions))
        self.assertFalse(zones.detection_ignored("car", 0.158, 0.216, "front", regions))

    def test_rejects_junk(self):
        self.assertFalse(zones.ignore_regions_valid(None))
        self.assertFalse(zones.ignore_regions_valid({"polygon": BAY}))
        self.assertFalse(zones.ignore_regions_valid([{"camera": "front", "polygon": [[0, 0]]}]))
        self.assertFalse(zones.ignore_regions_valid([{
            "camera": "front", "polygon": [[0, 0], [1, 0], [2, 1]],
        }]))


if __name__ == "__main__":
    unittest.main()
