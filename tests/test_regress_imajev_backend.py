"""Regression tests for the optional Imajev typed-decision backend."""
import os
import unittest
from unittest import mock

import analyze_images


SCHEMA = {
    "type": "object",
    "properties": {
        "approaching_house": {
            "type": "boolean",
            "description": "Is the person moving toward the house?",
        },
        "direction": {
            "type": "string",
            "enum": ["toward", "away", "lateral", "stationary", "unknown"],
            "description": "Direction of travel.",
        },
    },
}


class FakeResponse:
    ok = True

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class TestImajevBackend(unittest.TestCase):
    def test_schema_translation(self):
        q = analyze_images._imajev_questions_for_schema(SCHEMA)
        self.assertEqual(q["approaching_house"]["type"], "noul")
        self.assertEqual(q["direction"]["type"], "choice")
        self.assertEqual(
            list(q["direction"]["criteria"]),
            ["toward", "away", "lateral", "stationary", "unknown"],
        )

    @mock.patch.object(analyze_images, "camera_kind", return_value="front")
    @mock.patch.object(analyze_images, "encode_image")
    @mock.patch.object(analyze_images.requests, "post")
    def test_dual_image_caps_to_newest_prior_plus_current(
        self, post, encode, _kind
    ):
        encode.side_effect = lambda p: "ENC:" + os.path.basename(p)
        post.return_value = FakeResponse({
            "answers": {
                "approaching_house": {
                    "type": "noul",
                    "noul": 0.91,
                    "abstained": False,
                },
                "direction": {
                    "type": "choice",
                    "choice": "toward",
                    "abstained": False,
                },
            }
        })

        got = analyze_images.analyze_image_imajev(
            "/frames/current.jpg",
            SCHEMA,
            extra_images=[
                "/frames/old.jpg",
                "/frames/newest.jpg",
                "/frames/current.jpg",
            ],
        )

        self.assertEqual(
            post.call_args.kwargs["json"]["images"],
            [
                "data:image/jpeg;base64,ENC:newest.jpg",
                "data:image/jpeg;base64,ENC:current.jpg",
            ],
        )
        self.assertTrue(got["approaching_house"])
        self.assertEqual(got["direction"], "toward")

    @mock.patch.object(analyze_images, "camera_kind", return_value="front")
    @mock.patch.object(analyze_images, "encode_image", return_value="ENC")
    @mock.patch.object(analyze_images.requests, "post")
    def test_abstention_maps_to_none(self, post, _encode, _kind):
        post.return_value = FakeResponse({
            "answers": {
                "approaching_house": {
                    "type": "noul",
                    "noul": 0.91,
                    "abstained": True,
                },
                "direction": {
                    "type": "choice",
                    "choice": "unknown",
                    "abstained": True,
                },
            }
        })

        got = analyze_images.analyze_image_imajev(
            "/frames/current.jpg", SCHEMA
        )
        self.assertIsNone(got["approaching_house"])
        self.assertIsNone(got["direction"])


if __name__ == "__main__":
    unittest.main()

class TestImajevRuntimeSelection(unittest.TestCase):
    def test_apply_settings_selects_imajev_and_rejects_invalid_backend(self):
        old_backend = analyze_images.DECISION_BACKEND
        old_url = analyze_images.IMAJEV_URL
        old_settings = analyze_images._s
        try:
            analyze_images.DECISION_BACKEND = "ollama"
            analyze_images.IMAJEV_URL = "http://127.0.0.1:8791"
            analyze_images.apply_settings({
                "decision_backend": "imajev",
                "imajev_url": "http://127.0.0.1:9999/",
            })
            self.assertEqual(analyze_images.DECISION_BACKEND, "imajev")
            self.assertEqual(analyze_images.IMAJEV_URL, "http://127.0.0.1:9999")

            analyze_images.apply_settings({"decision_backend": "not-a-backend"})
            self.assertEqual(
                analyze_images.DECISION_BACKEND,
                "imajev",
                "invalid config must not replace the last valid backend",
            )
        finally:
            analyze_images.DECISION_BACKEND = old_backend
            analyze_images.IMAJEV_URL = old_url
            analyze_images._s = old_settings

    def test_run_deep_pass_falls_back_to_openrouter_when_imajev_returns_none(self):
        import scans

        spec = {
            "id": "test",
            "schema": {
                "type": "object",
                "properties": {
                    "porch_access": {
                        "type": "boolean",
                        "description": "Is a person on the porch?",
                    }
                },
            },
            "num_predict": 16,
        }

        old_cloud = analyze_images.ALLOW_CLOUD
        old_backend = analyze_images.DECISION_BACKEND
        old_rate = analyze_images.RATE_LIMITED
        try:
            analyze_images.ALLOW_CLOUD = True
            analyze_images.DECISION_BACKEND = "imajev"
            analyze_images.RATE_LIMITED = False

            with mock.patch.object(scans, "scans_for", return_value=[spec]), \
                 mock.patch.object(scans, "yolo_animal_seed", return_value={}), \
                 mock.patch.object(analyze_images, "scan_geometry_flags", return_value={}), \
                 mock.patch.object(analyze_images, "scan_skip_ids", return_value=set()), \
                 mock.patch.object(analyze_images, "camera_kind", return_value="front"), \
                 mock.patch.object(analyze_images, "analyze_image_with_schema", return_value=None) as local, \
                 mock.patch.object(
                     analyze_images,
                     "analyze_image_openrouter",
                     return_value={"porch_access": True},
                 ) as cloud, \
                 mock.patch.object(analyze_images, "set_inference_status"), \
                 mock.patch.object(analyze_images, "log_inference"):
                result, calls = analyze_images.run_deep_pass(
                    "frame.jpg",
                    "frame.jpg",
                    True,
                    "sk-test",
                    "priority",
                    fp_labels=["person"],
                )

            self.assertEqual(calls, 1)
            self.assertTrue(result["porch_access"])
            local.assert_called_once()
            cloud.assert_called_once()
        finally:
            analyze_images.ALLOW_CLOUD = old_cloud
            analyze_images.DECISION_BACKEND = old_backend
            analyze_images.RATE_LIMITED = old_rate
