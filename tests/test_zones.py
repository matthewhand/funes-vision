"""Parked-car ignore triangle: mute the bay, keep street / leaving cars."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import zones

# Front-camera bay covering the parked orange SUV (left edge), not the street.
BAY = [[0.0, 0.22], [0.20, 0.45], [0.0, 0.72]]
REGION = {"camera": "front", "labels": ["car"], "polygon": BAY, "enabled": True}


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


class TestIgnoreRegionsValid(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(zones.ignore_regions_valid([REGION]))
        self.assertTrue(zones.ignore_regions_valid([]))

    def test_settings_default_triangle_matches_this_box(self):
        import json
        settings = json.load(open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "settings.json")))
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
