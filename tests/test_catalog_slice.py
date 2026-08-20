"""create-index.sh slices the shared analysis catalog to one camera's files."""
import json
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCatalogSlice(unittest.TestCase):
    def test_jq_keeps_only_this_camera_keys(self):
        src = {
            "10.0.0.21_01_20260618100000000_MOTDEC.jpg": {"person": True},
            "10.0.0.22_01_20260618100000000_MOTDEC.jpg": {"dog": True},
        }
        images = ["10.0.0.21_01_20260618100000000_MOTDEC.jpg"]
        prog = """
          ($imgs[0] // []) as $list
          | ($list | map({(.): true}) | add // {}) as $want
          | if ($want | type) != "object" then .
            else with_entries(select(
              ($want[.key] == true)
              or ((.value.images // []) | map($want[.] == true) | any)
            ))
            end
        """
        with tempfile.TemporaryDirectory() as td:
            src_p = os.path.join(td, "analysis.json")
            imgs_p = os.path.join(td, "images.json")
            with open(src_p, "w") as f:
                json.dump(src, f)
            with open(imgs_p, "w") as f:
                json.dump(images, f)
            out = subprocess.check_output(
                ["jq", "--slurpfile", "imgs", imgs_p, prog, src_p],
                text=True,
            )
        sliced = json.loads(out)
        self.assertIn("10.0.0.21_01_20260618100000000_MOTDEC.jpg", sliced)
        self.assertNotIn("10.0.0.22_01_20260618100000000_MOTDEC.jpg", sliced)

    def test_jq_keeps_burst_if_any_frame_is_local(self):
        src = {
            "10.0.0.22_01_end.jpg": {
                "summary": "dog",
                "images": ["10.0.0.22_01_start.jpg", "10.0.0.22_01_end.jpg"],
            }
        }
        images = ["10.0.0.22_01_start.jpg"]
        prog = """
          ($imgs[0] // []) as $list
          | ($list | map({(.): true}) | add // {}) as $want
          | if ($want | type) != "object" then .
            else with_entries(select(
              ($want[.key] == true)
              or ((.value.images // []) | map($want[.] == true) | any)
            ))
            end
        """
        with tempfile.TemporaryDirectory() as td:
            src_p = os.path.join(td, "bursts.json")
            imgs_p = os.path.join(td, "images.json")
            with open(src_p, "w") as f:
                json.dump(src, f)
            with open(imgs_p, "w") as f:
                json.dump(images, f)
            out = subprocess.check_output(
                ["jq", "--slurpfile", "imgs", imgs_p, prog, src_p],
                text=True,
            )
        sliced = json.loads(out)
        self.assertIn("10.0.0.22_01_end.jpg", sliced)


if __name__ == "__main__":
    unittest.main()
