"""Regression tests for the one-shot JEVision typed-decision backend."""
import json
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

import analyze_images

SCHEMA = {
    "type": "object",
    "properties": {
        "person_here": {
            "type": "boolean",
            "description": "Is at least one person visibly present?",
        },
        "direction": {
            "type": "string",
            "enum": ["toward", "away", "unknown"],
            "description": "Direction of travel.",
        },
    },
}


class TestJEVisionBackend(unittest.TestCase):
    def test_apply_settings_selects_jevision(self):
        old_backend = analyze_images.DECISION_BACKEND
        old_root = analyze_images.JEVISION_ROOT
        old_python = analyze_images.JEVISION_PYTHON
        old_hf = analyze_images.JEVISION_HF_HOME
        old_settings = analyze_images._s
        try:
            analyze_images.apply_settings({
                "decision_backend": "jevision",
                "jevision_root": "/models/jev/",
                "jevision_python": "/venv/bin/python",
                "jevision_hf_home": "/models/hf-cache/",
            })
            self.assertEqual(analyze_images.DECISION_BACKEND, "jevision")
            self.assertEqual(analyze_images.JEVISION_ROOT, "/models/jev")
            self.assertEqual(analyze_images.JEVISION_PYTHON, "/venv/bin/python")
            self.assertEqual(analyze_images.JEVISION_HF_HOME, "/models/hf-cache")
        finally:
            analyze_images.DECISION_BACKEND = old_backend
            analyze_images.JEVISION_ROOT = old_root
            analyze_images.JEVISION_PYTHON = old_python
            analyze_images.JEVISION_HF_HOME = old_hf
            analyze_images._s = old_settings

    @mock.patch.object(analyze_images, "camera_kind", return_value="front")
    @mock.patch.object(analyze_images, "unload_ollama_residents", return_value=True)
    @mock.patch.object(analyze_images, "heavyweight_inference_lock", return_value=nullcontext())
    @mock.patch.object(analyze_images.subprocess, "run")
    def test_one_shot_is_offline_single_frame_and_releases_by_process_exit(
        self, run, _lock, unload, _kind
    ):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout="loader note\n" + json.dumps({
                "model": "jevision-0.8b",
                "answers": {
                    "person_here": {"type": "noul", "noul": 0.99},
                    "direction": {"type": "choice", "choice": "toward"},
                },
            }) + "\n",
            stderr="",
        )
        got = analyze_images.analyze_image_jevision("/frames/current.jpg", SCHEMA)
        self.assertTrue(got["person_here"])
        self.assertEqual(got["direction"], "toward")
        unload.assert_called_once_with()
        kwargs = run.call_args.kwargs
        payload = json.loads(kwargs["input"])
        self.assertEqual(payload["image_path"], "/frames/current.jpg")
        self.assertNotIn("images", payload)
        self.assertEqual(payload["max_width"], 384)
        self.assertEqual(payload["max_height"], 288)
        self.assertEqual(kwargs["env"]["HF_HUB_CACHE"], analyze_images.JEVISION_HF_HOME)
        self.assertEqual(kwargs["env"]["HF_HUB_OFFLINE"], "1")
        self.assertEqual(kwargs["env"]["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(run.call_args.args[0][0], analyze_images.JEVISION_PYTHON)
        self.assertTrue(run.call_args.args[0][1].endswith("tools/jevision_once.py"))



    @mock.patch.object(analyze_images.time, "sleep")
    @mock.patch.object(analyze_images.requests, "post")
    @mock.patch.object(analyze_images.requests, "get")
    def test_unload_waits_until_ollama_reports_no_local_residents(
        self, get, post, sleep
    ):
        first = SimpleNamespace(
            json=lambda: {"models": [{"name": "gemma4:e2b"}]},
            raise_for_status=lambda: None)
        still = SimpleNamespace(
            json=lambda: {"models": [{"name": "gemma4:e2b"}]},
            raise_for_status=lambda: None)
        gone = SimpleNamespace(
            json=lambda: {"models": []},
            raise_for_status=lambda: None)
        get.side_effect = [first, still, gone]
        post.return_value = SimpleNamespace(raise_for_status=lambda: None)
        self.assertTrue(analyze_images.unload_ollama_residents())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(get.call_count, 3)
        sleep.assert_called_once_with(0.2)

    @mock.patch.object(analyze_images, "heavyweight_inference_lock", return_value=nullcontext())
    @mock.patch.object(analyze_images.requests, "post")
    def test_ollama_nonstream_uses_same_heavyweight_lock(self, post, lock):
        post.return_value = SimpleNamespace(
            status_code=200, headers={}, raise_for_status=lambda: None)
        analyze_images._ollama_chat({"model": "gemma4:e2b"}, timeout=5)
        lock.assert_called_once_with()

    @mock.patch.object(analyze_images, "heavyweight_inference_lock", return_value=nullcontext())
    @mock.patch.object(analyze_images.requests, "post")
    def test_ollama_stream_holds_same_heavyweight_lock(self, post, lock):
        post.return_value = SimpleNamespace(
            status_code=200, headers={}, raise_for_status=lambda: None,
            iter_lines=lambda: [b'{"message":{"content":"ok"},"done":true}'])
        got = analyze_images._ollama_chat_stream(
            {"model": "gemma4:e2b", "messages": []}, None, timeout=5)
        self.assertEqual(got, "ok")
        lock.assert_called_once_with()

    @mock.patch.object(analyze_images, "scan_geometry_flags", return_value={})
    @mock.patch.object(analyze_images, "scan_skip_ids", return_value=set())
    @mock.patch.object(analyze_images, "camera_kind", return_value="front")
    @mock.patch.object(analyze_images, "analyze_image_jevision")
    @mock.patch.object(analyze_images, "set_inference_status")
    @mock.patch.object(analyze_images, "log_inference")
    def test_run_deep_pass_packs_all_frame_questions_into_one_call(
        self, _log, _status, jev, _kind, _skip, _geometry
    ):
        jev.return_value = {
            "postal_delivery": False,
            "postal_how": "none",
            "porch_access": True,
        }
        old_backend = analyze_images.DECISION_BACKEND
        old_cloud = analyze_images.ALLOW_CLOUD
        try:
            analyze_images.DECISION_BACKEND = "jevision"
            analyze_images.ALLOW_CLOUD = False
            result, calls = analyze_images.run_deep_pass(
                "/frames/current.jpg", "current.jpg", True, None, "priority",
                fp_labels=["person"],
                timeline_images=["/frames/prior.jpg", "/frames/current.jpg"],
            )
        finally:
            analyze_images.DECISION_BACKEND = old_backend
            analyze_images.ALLOW_CLOUD = old_cloud

        self.assertEqual(calls, 1)
        jev.assert_called_once()
        union = jev.call_args.args[1]
        self.assertEqual(
            union["required"],
            ["postal_delivery", "postal_how", "porch_access"],
        )
        self.assertEqual(result["_scans"], ["postal", "porch"])
        self.assertNotIn("_timeline_images", result)
        self.assertTrue(result["porch_access"])


if __name__ == "__main__":
    unittest.main()
