"""Baseline unit tests for the webcam project's pure helpers.

Run from the repo root:  python3 -m unittest discover -s tests
No third-party deps — stdlib unittest only. These cover the small, pure
logic units; DOM/UI behaviour is proven separately (extracted pure fns +
the deployed app).
"""
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_server
import analyze_images
from integrations import slack, media, ntfy
import integrations as intg


class TestSettingValid(unittest.TestCase):
    def test_choice_ok(self):
        self.assertTrue(api_server.setting_valid("fast_pass_engine", "yolo"))
        self.assertTrue(api_server.setting_valid("deep_passes_enabled", False))
        self.assertTrue(api_server.setting_valid("burst_summaries_enabled", False))
        self.assertTrue(api_server.setting_valid("burst_summaries_enabled", True))
        self.assertFalse(api_server.setting_valid("burst_summaries_enabled", "yes"))

    def test_choice_bad(self):
        self.assertFalse(api_server.setting_valid("fast_pass_engine", "nope"))

    def test_range(self):
        self.assertTrue(api_server.setting_valid("idle_sweep_seconds", 60))
        self.assertFalse(api_server.setting_valid("idle_sweep_seconds", 5))    # below min
        self.assertFalse(api_server.setting_valid("idle_sweep_seconds", 9999))  # above max
        self.assertFalse(api_server.setting_valid("idle_sweep_seconds", True))  # bool rejected
        self.assertFalse(api_server.setting_valid("idle_sweep_seconds", 60.0))  # float rejected
        self.assertFalse(api_server.setting_valid("idle_sweep_seconds", 15.5))


class TestBackoff(unittest.TestCase):
    def test_exponential_capped(self):
        self.assertEqual(analyze_images.backoff_delay(0, base=2.0, cap=60.0), 2.0)
        self.assertEqual(analyze_images.backoff_delay(1, base=2.0, cap=60.0), 4.0)
        self.assertEqual(analyze_images.backoff_delay(2, base=2.0, cap=60.0), 8.0)
        self.assertEqual(analyze_images.backoff_delay(3, base=2.0, cap=60.0), 16.0)
        self.assertEqual(analyze_images.backoff_delay(5, base=2.0, cap=60.0), 60.0)   # 64 -> cap
        self.assertEqual(analyze_images.backoff_delay(20, base=2.0, cap=60.0), 60.0)  # cap holds

    def test_never_negative_and_respects_base(self):
        self.assertEqual(analyze_images.backoff_delay(0, base=1.0, cap=30.0), 1.0)
        self.assertGreaterEqual(analyze_images.backoff_delay(0), 0.0)


class TestModelFallback(unittest.TestCase):
    def test_model_chain(self):
        mc = analyze_images.model_chain
        self.assertEqual(mc("a", "b"), ["a", "b"])
        self.assertEqual(mc("a", "a"), ["a"])          # dedup
        self.assertEqual(mc(" a ", "b"), ["a", "b"])   # trimmed
        self.assertEqual(mc("a", ""), ["a"])           # empty fallback dropped
        self.assertEqual(mc(None, "b"), ["b"])
        self.assertEqual(mc("", None), [])

    def test_falls_back_on_rate_limit(self):
        ai = analyze_images
        ai.MODEL_PRIMARY, ai.MODEL_FALLBACK = "primary:cloud", "local:e4b"
        ai.MIN_MEM_FOR_LOCAL_GB = 0.0   # keep the local fallback in the runnable chain here
        ai.encode_image = lambda p: "x"
        calls = []

        class OK:
            def json(self): return {"message": {"content": '{"person": true}'}}

        def fake_chat(payload, timeout):
            calls.append(payload["model"])
            if payload["model"] == "primary:cloud":
                raise ai.RateLimited("429")
            return OK()

        orig, ai._ollama_chat = ai._ollama_chat, fake_chat
        ai.RATE_LIMITED = False
        try:
            res = ai.analyze_image_local("x.jpg")
        finally:
            ai._ollama_chat = orig
        self.assertEqual(res, {"person": True})
        self.assertEqual(calls, ["primary:cloud", "local:e4b"])  # primary then fallback
        self.assertFalse(ai.RATE_LIMITED)  # fallback succeeded -> sweep not bailed
        self.assertEqual(ai.LAST_MODEL_USED, "local:e4b")  # records the model that served

    def test_all_rate_limited_bails_sweep(self):
        ai = analyze_images
        ai.MODEL_PRIMARY, ai.MODEL_FALLBACK = "p", "f"
        ai.MIN_MEM_FOR_LOCAL_GB = 0.0   # both are "local"; keep them runnable here
        ai.encode_image = lambda p: "x"

        def fake_chat(payload, timeout):
            raise ai.RateLimited("429")

        orig, ai._ollama_chat = ai._ollama_chat, fake_chat
        ai.RATE_LIMITED = False
        try:
            res = ai.analyze_image_local("x.jpg")
        finally:
            ai._ollama_chat = orig
        self.assertIsNone(res)
        self.assertTrue(ai.RATE_LIMITED)  # whole chain throttled -> bail this sweep


class TestRunnableChain(unittest.TestCase):
    """The host may lack the RAM to run a local model, but a cloud (':cloud')
    model rides Ollama's endpoint and needs none. runnable_chain() filters
    model_chain() to what THIS box can actually serve, so a low-RAM box still
    runs its cloud primary instead of silently doing zero deep passes."""

    def test_model_is_cloud(self):
        ic = analyze_images.model_is_cloud
        self.assertTrue(ic("minimax-m3:cloud"))
        self.assertTrue(ic("  gpt:cloud  "))      # trimmed
        self.assertFalse(ic("gemma4:e2b"))
        self.assertFalse(ic("cloudy:latest"))     # must be the ':cloud' suffix
        self.assertFalse(ic(""))
        self.assertFalse(ic(None))

    def test_low_ram_keeps_cloud_drops_local(self):
        rc = analyze_images.runnable_chain
        # 5.6GB free, local model needs 16 -> cloud stays, local filtered out
        self.assertEqual(rc("minimax-m3:cloud", "gemma4:e2b", 5.6, 16.0), ["minimax-m3:cloud"])

    def test_enough_ram_keeps_both(self):
        rc = analyze_images.runnable_chain
        self.assertEqual(rc("minimax-m3:cloud", "gemma4:e2b", 32.0, 16.0),
                         ["minimax-m3:cloud", "gemma4:e2b"])

    def test_local_only_low_ram_is_empty(self):
        rc = analyze_images.runnable_chain
        # no cloud model and not enough RAM -> nothing this box can serve
        self.assertEqual(rc("gemma4:e2b", "", 5.6, 16.0), [])

    def test_local_only_enough_ram(self):
        rc = analyze_images.runnable_chain
        self.assertEqual(rc("gemma4:e2b", "", 32.0, 16.0), ["gemma4:e2b"])

    def test_preserves_chain_dedup_and_order(self):
        rc = analyze_images.runnable_chain
        self.assertEqual(rc("x:cloud", "x:cloud", 0.0, 16.0), ["x:cloud"])  # dedup via model_chain

    def test_low_ram_cloud_primary_still_serves(self):
        """The integration bug this fixes: cloud primary + low RAM used to do
        nothing because the deep-pass gate keyed on a 16GB local threshold."""
        ai = analyze_images
        ai.MODEL_PRIMARY, ai.MODEL_FALLBACK = "minimax-m3:cloud", "gemma4:e2b"
        ai.MIN_MEM_FOR_LOCAL_GB = 16.0
        orig_mem, ai.get_free_mem_gb = ai.get_free_mem_gb, (lambda: 5.6)
        ai.encode_image = lambda p: "x"
        calls = []

        class OK:
            def json(self): return {"message": {"content": '{"person": true}'}}

        def fake_chat(payload, timeout):
            calls.append(payload["model"])
            return OK()

        orig_chat, ai._ollama_chat = ai._ollama_chat, fake_chat
        ai.RATE_LIMITED = False
        try:
            res = ai.analyze_image_local("x.jpg")
        finally:
            ai._ollama_chat = orig_chat
            ai.get_free_mem_gb = orig_mem
        self.assertEqual(res, {"person": True})
        self.assertEqual(calls, ["minimax-m3:cloud"])  # cloud served; local never attempted
        self.assertEqual(ai.LAST_MODEL_USED, "minimax-m3:cloud")


class TestConcurrencyWorkers(unittest.TestCase):
    def test_clamps(self):
        cw = analyze_images.concurrency_workers
        self.assertEqual(cw(3, 100), 3)      # honored
        self.assertEqual(cw(3, 2), 2)        # never more than targets
        self.assertEqual(cw(1, 100), 1)      # serial
        self.assertEqual(cw(0, 100), 1)      # floor at 1 worker
        self.assertEqual(cw(5, 0), 0)        # nothing to do -> 0
        self.assertEqual(cw(None, 10), 1)    # bad config -> 1
        self.assertEqual(cw("x", 10), 1)     # bad config -> 1


class TestAtomicIO(unittest.TestCase):
    """The audit-trail files are read live by the API and written by possibly
    concurrent deep passes; writes must be atomic and never lose log entries."""

    def test_atomic_write_replaces_with_valid_json(self):
        ai = analyze_images
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json")
            ai._atomic_write_json(p, {"a": 1}, indent=1)
            self.assertEqual(json.load(open(p)), {"a": 1})
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            ai._atomic_write_json(p, {"b": 2})        # overwrite
            self.assertEqual(json.load(open(p)), {"b": 2})
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            # no leftover temp files beside it
            self.assertEqual([f for f in os.listdir(d) if ".tmp." in f], [])

    def test_concurrent_status_writes_always_valid(self):
        ai = analyze_images
        with tempfile.TemporaryDirectory() as d:
            orig, ai.INFERENCE_STATUS = ai.INFERENCE_STATUS, os.path.join(d, "status.json")
            try:
                def hammer(n):
                    for i in range(50):
                        ai.set_inference_status({"image": f"img{n}-{i}"})
                ts = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
                [t.start() for t in ts]; [t.join() for t in ts]
                # file always parses (never a half-written read)
                json.load(open(ai.INFERENCE_STATUS))
            finally:
                ai.INFERENCE_STATUS = orig

    def test_concurrent_log_inference_no_lost_updates(self):
        ai = analyze_images
        with tempfile.TemporaryDirectory() as d:
            orig, ai.INFERENCE_LOG = ai.INFERENCE_LOG, os.path.join(d, "log.json")
            try:
                def hammer(n):
                    for i in range(10):
                        ai.log_inference(f"i{n}-{i}", "m", 0.0, 1.0, [], True, "t")
                ts = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
                [t.start() for t in ts]; [t.join() for t in ts]
                log = json.load(open(ai.INFERENCE_LOG))
                # 80 appends, capped at the last 200 -> all 80 retained, none lost
                self.assertEqual(len(log), 80)
            finally:
                ai.INFERENCE_LOG = orig


class TestChatStream(unittest.TestCase):
    def test_parse_chat_chunk(self):
        self.assertEqual(analyze_images.parse_chat_chunk('{"message":{"content":"Hi"},"done":false}'), ("Hi", False))
        self.assertEqual(analyze_images.parse_chat_chunk('{"message":{"content":" there"},"done":true}'), (" there", True))
        self.assertEqual(analyze_images.parse_chat_chunk('{"done":true}'), ("", True))
        self.assertEqual(analyze_images.parse_chat_chunk('not json'), ("", False))
        self.assertEqual(analyze_images.parse_chat_chunk('{}'), ("", False))

    def test_stream_accumulates_and_calls_back(self):
        ai = analyze_images
        lines = [b'{"message":{"content":"Hello"},"done":false}',
                 b'{"message":{"content":" world"},"done":false}',
                 b'',  # blank line ignored
                 b'{"message":{"content":"!"},"done":true}']

        class FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def iter_lines(self): return iter(lines)

        orig = ai.requests.post
        ai.requests.post = lambda *a, **k: FakeResp()
        deltas = []
        try:
            full = ai._ollama_chat_stream({"model": "x", "messages": []},
                                          lambda d, acc: deltas.append(d), timeout=5)
        finally:
            ai.requests.post = orig
        self.assertEqual(full, "Hello world!")
        self.assertEqual(deltas, ["Hello", " world", "!"])


class TestNtfy(unittest.TestCase):
    def test_endpoint_requires_explicit_server(self):
        self.assertEqual(ntfy._endpoint({"topic": "home"}), "")
        self.assertEqual(ntfy._endpoint({"topic": "home", "server_url": "  "}), "")
        self.assertFalse(hasattr(ntfy, "DEFAULT_SERVER"))
        self.assertNotIn("ntfy.sh", ntfy._endpoint({"topic": "secret"}))

    def test_endpoint_custom_server_strips_slashes(self):
        self.assertEqual(
            ntfy._endpoint({"server_url": "https://n.example.com/", "topic": "/cams/"}),
            "https://n.example.com/cams")

    def test_endpoint_requires_topic(self):
        self.assertEqual(ntfy._endpoint({}), "")
        self.assertEqual(ntfy._endpoint({"topic": "  "}), "")
        self.assertEqual(ntfy._endpoint({"server_url": "https://n.example.com"}), "")

    def test_post_without_server_does_not_publish(self):
        called = []
        orig = ntfy.requests.post
        ntfy.requests.post = lambda *a, **k: called.append((a, k)) or type(
            "R", (), {"status_code": 200})()
        try:
            ok, detail = ntfy._post({"topic": "home"}, "hi")
        finally:
            ntfy.requests.post = orig
        self.assertFalse(ok)
        self.assertIn("server_url", detail)
        self.assertEqual(called, [])

    def test_post_without_topic_does_not_publish(self):
        ok, detail = ntfy._post({"server_url": "https://n.example.com"}, "hi")
        self.assertFalse(ok)
        self.assertIn("topic", detail)

    def test_deep_link(self):
        self.assertEqual(
            ntfy._deep_link({"public_base_url": "https://w.example/"}, "event", "b1"),
            "https://w.example/?event=b1")

    def test_deep_link_blank_without_base_or_ident(self):
        self.assertEqual(ntfy._deep_link({}, "event", "b1"), "")
        self.assertEqual(ntfy._deep_link({"public_base_url": "https://w.example"}, "event", ""), "")

    def test_headers_compose(self):
        h = ntfy._headers({"token": "tk"}, title="T", click="C", tags=["a", "b"])
        self.assertEqual(h["Title"], "T")
        self.assertEqual(h["Click"], "C")
        self.assertEqual(h["Tags"], "a,b")
        self.assertEqual(h["Authorization"], "Bearer tk")

    def test_headers_minimal(self):
        self.assertEqual(ntfy._headers({}), {})


class TestSaveIntegrations(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, "integrations.json")
        self._orig = api_server.INTEGRATIONS_FILE
        api_server.INTEGRATIONS_FILE = self.path

    def tearDown(self):
        api_server.INTEGRATIONS_FILE = self._orig
        self.td.cleanup()

    def test_load_missing_is_empty(self):
        self.assertEqual(api_server.load_integrations(), {})

    def test_load_corrupt_is_empty_and_leaves_file(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(api_server.load_integrations(), {})
        with open(self.path) as f:
            self.assertEqual(f.read(), "{not json")

    def test_missing_file_creates_slack_only(self):
        api_server.save_slack_settings({"enabled": True, "channel_id": "C1"})
        data = json.load(open(self.path))
        self.assertEqual(data["slack"]["enabled"], True)
        self.assertEqual(data["slack"]["channel_id"], "C1")
        self.assertNotIn("mqtt", data)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_merge_preserves_mqtt_and_ntfy(self):
        json.dump({
            "slack": {"enabled": False, "bot_token": "xoxb-keep"},
            "mqtt": {"enabled": True, "password": "s3cret", "host": "broker"},
            "ntfy": {"topic": "cams", "server_url": "https://n.example.com"},
        }, open(self.path, "w"))
        api_server.save_slack_settings({"enabled": True})
        data = json.load(open(self.path))
        self.assertTrue(data["slack"]["enabled"])
        self.assertEqual(data["slack"]["bot_token"], "xoxb-keep")
        self.assertEqual(data["mqtt"]["password"], "s3cret")
        self.assertEqual(data["ntfy"]["topic"], "cams")

    def test_empty_file_is_writable(self):
        open(self.path, "w").close()
        api_server.save_slack_settings({"enabled": False})
        self.assertFalse(json.load(open(self.path))["slack"]["enabled"])

    def test_corrupt_file_refuses_and_keeps_bytes(self):
        original = '{"mqtt":{"password":"s3cret"},"ntfy":{"topic":"x"}\n'
        with open(self.path, "w") as f:
            f.write(original)
        with self.assertRaises(api_server.IntegrationsUnreadable):
            api_server.save_slack_settings({"enabled": True})
        with open(self.path) as f:
            self.assertEqual(f.read(), original)

    def test_whitespace_file_refuses(self):
        with open(self.path, "w") as f:
            f.write("   \n")
        with self.assertRaises(api_server.IntegrationsUnreadable):
            api_server.save_slack_settings({"enabled": True})
        with open(self.path) as f:
            self.assertEqual(f.read(), "   \n")

    def test_non_object_json_refuses(self):
        with open(self.path, "w") as f:
            f.write("[1, 2, 3]")
        with self.assertRaises(api_server.IntegrationsUnreadable):
            api_server.save_slack_settings({"enabled": True})
        with open(self.path) as f:
            self.assertEqual(f.read(), "[1, 2, 3]")

    @unittest.skipIf(os.geteuid() == 0, "root bypasses file mode")
    def test_unreadable_file_refuses(self):
        with open(self.path, "w") as f:
            json.dump({"mqtt": {"password": "s3cret"}}, f)
        os.chmod(self.path, 0o000)
        try:
            with self.assertRaises(api_server.IntegrationsUnreadable):
                api_server.save_slack_settings({"enabled": True})
        finally:
            os.chmod(self.path, 0o600)
        self.assertIn("s3cret", open(self.path).read())

    def test_get_missing_file_is_empty_slack(self):
        code, body = api_server.integrations_get_response()
        self.assertEqual(code, 200)
        self.assertIn("slack", body)
        self.assertFalse(body["slack"]["enabled"])
        self.assertFalse(body["slack"]["has_bot_token"])
        self.assertNotIn("unreadable", body)

    def test_get_empty_file_is_empty_slack(self):
        open(self.path, "w").close()
        code, body = api_server.integrations_get_response()
        self.assertEqual(code, 200)
        self.assertIn("slack", body)
        self.assertFalse(body["slack"]["has_bot_token"])
        self.assertNotIn("unreadable", body)

    def test_get_corrupt_is_409_unreadable(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        code, body = api_server.integrations_get_response()
        self.assertEqual(code, 409)
        self.assertEqual(body, {
            "ok": False,
            "detail": "integrations.json is unreadable",
            "unreadable": True,
        })
        self.assertNotIn("slack", body)
        with open(self.path) as f:
            self.assertEqual(f.read(), "{not json")

    def test_get_readable_is_redacted(self):
        json.dump({
            "slack": {"enabled": True, "bot_token": "xoxb-secret", "channel_id": "C1"},
            "mqtt": {"password": "s3cret"},
        }, open(self.path, "w"))
        code, body = api_server.integrations_get_response()
        self.assertEqual(code, 200)
        self.assertTrue(body["slack"]["enabled"])
        self.assertTrue(body["slack"]["has_bot_token"])
        self.assertEqual(body["slack"]["channel_id"], "C1")
        self.assertNotIn("bot_token", body["slack"])
        self.assertNotIn("mqtt", body)


class TestFriendly(unittest.TestCase):
    def test_known_code_gets_hint(self):
        self.assertIn("invite the bot", slack._friendly("not_in_channel"))

    def test_unknown_code_passthrough(self):
        self.assertEqual(slack._friendly("weird_error"), "weird_error")


class TestMediaSample(unittest.TestCase):
    def test_downsamples_and_filters_missing(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(50):
                p = os.path.join(d, f"f{i}.jpg")
                open(p, "w").close()
                paths.append(p)
            paths.append(os.path.join(d, "missing.jpg"))  # nonexistent -> filtered out
            out = media._sample(paths, cap=10)
            self.assertEqual(len(out), 10)
            self.assertTrue(all(os.path.exists(p) for p in out))

    def test_under_cap_returns_existing_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.jpg")
            open(p, "w").close()
            self.assertEqual(media._sample([p, "nope.jpg"]), [p])


class TestRecordDelivery(unittest.TestCase):
    def test_records_ok_and_error(self):
        with tempfile.TemporaryDirectory() as d:
            intg.STATE_FILE = os.path.join(d, "state.json")
            intg._record_delivery("slack", "burst", True, "posted clip.gif")
            st = json.load(open(intg.STATE_FILE))
            self.assertTrue(st["slack"]["last_delivery"]["ok"])
            self.assertEqual(st["slack"]["last_delivery"]["kind"], "burst")
            self.assertIn("last_ok", st["slack"])

            intg._record_delivery("slack", "alert", False, "boom")
            st = json.load(open(intg.STATE_FILE))
            self.assertFalse(st["slack"]["last_delivery"]["ok"])
            self.assertIn("last_error", st["slack"])


class TestInferenceMetrics(unittest.TestCase):
    def test_rollup_window_and_split(self):
        with tempfile.TemporaryDirectory() as d:
            now = time.time()
            log = [
                {"started": now, "ok": True, "duration_s": 10, "model": "gemma4:12b"},
                {"started": now, "ok": False, "duration_s": 20, "model": "gemma4:12b"},
                {"started": now, "ok": True, "duration_s": 30, "model": "google/gemma-4-31b-it"},
                {"started": now - 99999, "ok": True, "duration_s": 5, "model": "x"},  # outside window
            ]
            json.dump(log, open(os.path.join(d, "inference_log.json"), "w"))
            orig = api_server.BASE_DIR
            api_server.BASE_DIR = d
            try:
                m = api_server._inference_metrics(window_s=3600)
            finally:
                api_server.BASE_DIR = orig
            self.assertEqual(m["count"], 3)        # the stale 4th is excluded
            self.assertEqual(m["ok"], 2)
            self.assertEqual(m["failures"], 1)
            self.assertEqual(m["cloud"], 1)        # the google/ id
            self.assertEqual(m["local"], 2)


class TestDetectionExtract(unittest.TestCase):
    ANALYSIS = {
        "a.jpg": {"car": True, "fast_pass": "partial"},          # preliminary (detector only)
        "b.jpg": {"person": True},                                # no _llm: not a verdict
        "c.jpg": {"person": False, "fast_pass": "negative"},      # awaiting backfill, no true label
        "d.jpg": {"dog": True, "_yolo": ["dog"], "_llm": {"animal_detected": True}},
        "e.jpg": {"person": True, "car": True, "fast_pass": "partial"},  # preliminary, 2 labels
        "f.jpg": {"person": False, "dog": False},                 # empty, no _llm
        "g.jpg": {"person": True, "_llm": {"porch_access": False}},  # verified (LLM merge)
        "h.jpg": {"car": True, "_llm_skip": "no_trigger"},        # car-only skip, not verified
        "i.jpg": {"person": True, "fast_pass": "partial", "_llm_skip": "budget"},
    }

    def test_verified_only_llm_merge_with_labels(self):
        h = api_server.Handler
        self.assertEqual(h._verified_detections(self.ANALYSIS),
                         {"d.jpg": ["dog"], "g.jpg": ["person"]})

    def test_person_without_llm_is_not_verified(self):
        rec = {"person": True}
        self.assertFalse(analyze_images.is_llm_verified(rec))
        self.assertNotIn("b.jpg", api_server.Handler._verified_detections(self.ANALYSIS))
        self.assertEqual(api_server.Handler._verified_detections({"x.jpg": rec}), {})

    def test_llm_dict_is_verified(self):
        rec = {"person": True, "_llm": {"porch_access": False}}
        self.assertTrue(analyze_images.is_llm_verified(rec))
        self.assertTrue(analyze_images.is_llm_verified({"_llm": {}}))
        self.assertEqual(api_server.Handler._verified_detections({"x.jpg": rec}),
                         {"x.jpg": ["person"]})

    def test_verified_labels_exclude_ha_flags(self):
        rec = {
            "person": True,
            "porch_access": True,
            "_llm": {"porch_access": True},
        }
        self.assertEqual(
            api_server.Handler._verified_detections({"x.jpg": rec}),
            {"x.jpg": ["person"]},
        )

    def test_preliminary_labels_exclude_ha_flags(self):
        rec = {
            "person": True,
            "porch_access": True,
            "fast_pass": "partial",
        }
        self.assertEqual(
            api_server.Handler._preliminary_detections({"x.jpg": rec}),
            {"x.jpg": ["person"]},
        )

    def test_no_trigger_is_not_verified(self):
        rec = {"car": True, "_llm_skip": "no_trigger"}
        self.assertFalse(analyze_images.is_llm_verified(rec))
        self.assertTrue(analyze_images.is_awaiting_backfill(rec))
        self.assertTrue(analyze_images.in_backfill_pool(rec))
        self.assertNotIn("h.jpg", api_server.Handler._verified_detections(self.ANALYSIS))
        self.assertEqual(api_server.Handler._verified_detections({"x.jpg": rec}), {})

    def test_no_trigger_car_is_awaiting_not_verified(self):
        """Car-only skip is backfill work, never an LLM verdict."""
        rec = {"car": True, "_llm_skip": "no_trigger"}
        self.assertTrue(analyze_images.is_awaiting_backfill(rec))
        self.assertTrue(analyze_images.in_backfill_pool(rec))
        self.assertFalse(analyze_images.is_llm_verified(rec))
        self.assertEqual(api_server.Handler._verified_detections({"x.jpg": rec}), {})

    def test_queue_counts_skip_only_and_bare_person(self):
        q = api_server.queue_from_analysis(self.ANALYSIS)
        self.assertEqual(q["llm_verified"], 2)          # d, g
        self.assertEqual(q["awaiting_backfill"], 2)     # c negative, h no_trigger
        self.assertEqual(q["unverified_partials"], 4)   # a, e, i + b bare person

    def test_backfill_pool_includes_no_trigger_not_urgent_partials(self):
        pool = analyze_images.in_backfill_pool
        awaiting = analyze_images.is_awaiting_backfill
        self.assertTrue(pool({"fast_pass": "negative"}))
        self.assertTrue(awaiting({"fast_pass": "negative"}))
        self.assertTrue(pool({"car": True, "_llm_skip": "no_trigger"}))
        self.assertTrue(awaiting({"car": True, "_llm_skip": "no_trigger"}))
        self.assertTrue(pool({"car": True, "fast_pass": "partial"}))
        self.assertFalse(awaiting({"car": True, "fast_pass": "partial"}))
        self.assertFalse(pool({"person": True, "fast_pass": "partial", "_llm_skip": "budget"}))
        self.assertFalse(awaiting({"person": True, "fast_pass": "partial", "_llm_skip": "budget"}))
        self.assertFalse(pool({"person": True, "_llm": {"porch_access": False}}))
        self.assertFalse(awaiting({"person": True, "_llm": {"porch_access": False}}))
        self.assertFalse(pool({"person": True}))
        self.assertFalse(awaiting({"person": True}))

    def test_preliminary_detector_only_with_labels(self):
        h = api_server.Handler
        self.assertEqual(h._preliminary_detections(self.ANALYSIS),
                         {"a.jpg": ["car"], "e.jpg": ["car", "person"],
                          "i.jpg": ["person"]})

    def test_verified_and_preliminary_are_disjoint(self):
        h = api_server.Handler
        v = set(h._verified_detections(self.ANALYSIS))
        p = set(h._preliminary_detections(self.ANALYSIS))
        self.assertEqual(v & p, set())
        self.assertNotIn("h.jpg", v)
        self.assertNotIn("h.jpg", p)


class TestNewEntries(unittest.TestCase):
    def test_returns_new_preserving_cur_order(self):
        self.assertEqual(api_server.Handler._new_entries(["a", "b"], ["b", "c", "d"]), ["c", "d"])

    def test_empty_prev_returns_all(self):
        self.assertEqual(api_server.Handler._new_entries([], ["x", "y"]), ["x", "y"])

    def test_no_new_returns_empty(self):
        self.assertEqual(api_server.Handler._new_entries(["a", "b"], ["a", "b"]), [])

    def test_accepts_dicts_and_sets_as_prev(self):
        self.assertEqual(api_server.Handler._new_entries({"a": 1}, ["a", "b"]), ["b"])
        self.assertEqual(api_server.Handler._new_entries({"a", "b"}, ["b", "c"]), ["c"])

    def test_interleaved_new_keep_order(self):
        self.assertEqual(api_server.Handler._new_entries(["a"], ["b", "a", "c"]), ["b", "c"])


class TestClip(unittest.TestCase):
    def test_meta(self):
        self.assertEqual(api_server.Handler._clip_meta("mp4"), ("mp4", "video/mp4"))
        self.assertEqual(api_server.Handler._clip_meta("gif"), ("gif", "image/gif"))
        self.assertEqual(api_server.Handler._clip_meta("GIF"), ("gif", "image/gif"))
        self.assertEqual(api_server.Handler._clip_meta(None), ("gif", "image/gif"))   # default
        self.assertEqual(api_server.Handler._clip_meta("webm"), ("gif", "image/gif")) # unknown -> gif

    def test_resolve_validates_and_joins(self):
        resolve = lambda n: "/cam" if n in ("a.jpg", "b.jpg") else None
        self.assertEqual(
            api_server.Handler._clip_resolve(["a.jpg", "bad.jpg", "b.jpg"], resolve),
            ["/cam/a.jpg", "/cam/b.jpg"])
        self.assertEqual(api_server.Handler._clip_resolve([], resolve), [])

    def test_resolve_caps_count(self):
        resolve = lambda n: "/c"
        out = api_server.Handler._clip_resolve(["f%d.jpg" % i for i in range(500)], resolve, cap=300)
        self.assertEqual(len(out), 300)

    def test_filename_sanitised(self):
        self.assertEqual(api_server.Handler._clip_filename("visit", "gif"), "visit.gif")
        self.assertEqual(api_server.Handler._clip_filename("a b/c", "mp4"), "a_b_c.mp4")


class TestResolveTimezone(unittest.TestCase):
    def test_env_overrides_settings(self):
        self.assertEqual(api_server.resolve_timezone("UTC", "Australia/Sydney"), "UTC")

    def test_settings_used_without_env(self):
        self.assertEqual(api_server.resolve_timezone(None, "Europe/London"), "Europe/London")

    def test_default_when_neither(self):
        self.assertEqual(api_server.resolve_timezone(None, None), "Australia/Sydney")

    def test_blank_values_ignored(self):
        self.assertEqual(api_server.resolve_timezone("", "  "), "Australia/Sydney")
        self.assertEqual(api_server.resolve_timezone("  ", "America/New_York"), "America/New_York")


class TestJsonRecovery(unittest.TestCase):
    """Corrupt/truncated analysis.json must not kill the sweep (retention)."""

    def test_recover_truncated_object_keeps_complete_entries(self):
        # Mimic the ENOSPC mid-key truncation we hit in production.
        truncated = (
            '{\n'
            '  "a.jpg": {\n    "fast_pass": "negative"\n  },\n'
            '  "b.jpg": {\n    "person": true,\n    "fast_pass": "partial"\n  },\n'
            '  "c.jpg": {\n    "fast_pass": "negat'
        )
        recovered = analyze_images._recover_truncated_json(truncated, {})
        self.assertIsInstance(recovered, dict)
        self.assertIn("a.jpg", recovered)
        self.assertIn("b.jpg", recovered)
        self.assertNotIn("c.jpg", recovered)

    def test_load_json_file_rewrites_clean_copy(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "analysis.json")
            # Two complete entries + incomplete tail.
            with open(path, "w") as f:
                f.write(
                    '{\n  "x.jpg": {\n    "fast_pass": "negative"\n  },\n'
                    '  "y.jpg": {\n    "fast_pass": "negative"\n  },\n'
                    '  "z.jpg": {\n    "fast_pass": "neg'
                )
            data = analyze_images.load_json_file(path, {})
            self.assertEqual(set(data), {"x.jpg", "y.jpg"})
            # Clean rewrite so the next load is a plain json.loads success.
            with open(path) as f:
                reloaded = json.load(f)
            self.assertEqual(set(reloaded), {"x.jpg", "y.jpg"})

    def test_load_json_file_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "nope.json")
            self.assertEqual(analyze_images.load_json_file(path, {}), {})
            self.assertEqual(analyze_images.load_json_file(path, []), [])


class TestApplyRetention(unittest.TestCase):
    def setUp(self):
        self._age = analyze_images.MAX_AGE_DAYS
        self._budget = analyze_images.MAX_DIR_GB
        self._log = analyze_images.RETENTION_LOG
        self.td = tempfile.TemporaryDirectory()
        self.dir = self.td.name
        analyze_images.RETENTION_LOG = os.path.join(self.dir, "retention_log.json")

    def tearDown(self):
        analyze_images.MAX_AGE_DAYS = self._age
        analyze_images.MAX_DIR_GB = self._budget
        analyze_images.RETENTION_LOG = self._log
        self.td.cleanup()

    def _touch(self, name, size, mtime):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(b"x" * size)
        os.utime(path, (mtime, mtime))
        return path

    def test_age_deletes_old_negatives_only(self):
        analyze_images.MAX_AGE_DAYS = 1
        analyze_images.MAX_DIR_GB = 100  # no budget pressure
        now = time.time()
        self._touch("old_neg.jpg", 100, now - 3 * 86400)
        self._touch("old_hit.jpg", 100, now - 3 * 86400)
        self._touch("new_neg.jpg", 100, now - 100)
        analysis = {
            "old_neg.jpg": {"fast_pass": "negative"},
            "old_hit.jpg": {"person": True},
            "new_neg.jpg": {"fast_pass": "negative"},
        }
        deleted = analyze_images.apply_retention(self.dir, analysis, set())
        self.assertEqual(deleted, {"old_neg.jpg"})
        self.assertFalse(os.path.exists(os.path.join(self.dir, "old_neg.jpg")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "old_hit.jpg")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "new_neg.jpg")))

    def test_budget_prefers_negatives_then_detected(self):
        analyze_images.MAX_AGE_DAYS = 3650
        analyze_images.MAX_DIR_GB = 500 / (1024 ** 3)  # 500 bytes
        now = time.time()
        self._touch("n1.jpg", 400, now - 300)
        self._touch("n2.jpg", 400, now - 200)
        self._touch("d1.jpg", 400, now - 100)
        analysis = {
            "n1.jpg": {"fast_pass": "negative"},
            "n2.jpg": {"fast_pass": "negative"},
            "d1.jpg": {"dog": True},
        }
        deleted = analyze_images.apply_retention(self.dir, analysis, set())
        # n1 (oldest neg) must go; may also need n2; d1 only if still over.
        self.assertIn("n1.jpg", deleted)
        self.assertNotIn("d1.jpg", deleted)  # still under after negatives
        total = sum(os.path.getsize(os.path.join(self.dir, f))
                    for f in os.listdir(self.dir) if f.endswith(".jpg"))
        self.assertLessEqual(total, 500)

    def test_pins_never_deleted(self):
        analyze_images.MAX_AGE_DAYS = 1
        analyze_images.MAX_DIR_GB = 1 / (1024 ** 3)  # ~1 byte budget
        now = time.time()
        self._touch("pinned.jpg", 1000, now - 10 * 86400)
        analysis = {"pinned.jpg": {"fast_pass": "negative"}}
        deleted = analyze_images.apply_retention(self.dir, analysis, {"pinned.jpg"})
        self.assertEqual(deleted, set())
        self.assertTrue(os.path.exists(os.path.join(self.dir, "pinned.jpg")))

    def test_unanalyzed_evicted_only_when_budget_stuck(self):
        analyze_images.MAX_AGE_DAYS = 3650
        analyze_images.MAX_DIR_GB = 500 / (1024 ** 3)
        now = time.time()
        self._touch("known.jpg", 200, now - 50)
        self._touch("orphan_old.jpg", 400, now - 200)
        self._touch("orphan_new.jpg", 400, now - 10)
        analysis = {"known.jpg": {"fast_pass": "negative"}}
        deleted = analyze_images.apply_retention(self.dir, analysis, set())
        # known (analyzed neg) goes first; then oldest unanalyzed until under budget.
        self.assertIn("known.jpg", deleted)
        self.assertIn("orphan_old.jpg", deleted)
        self.assertNotIn("orphan_new.jpg", deleted)


class TestE2bSchema(unittest.TestCase):
    def test_camera_kind(self):
        ck = analyze_images.camera_kind
        self.assertEqual(ck("/mnt/models/Webcam21/10.0.0.21_01_x.jpg"), "front")
        self.assertEqual(ck("/mnt/models/Webcam22/10.0.0.22_01_x.jpg"), "back")

    def test_tokens_and_schema(self):
        self.assertEqual(analyze_images.max_tokens_for_kind("front"), 220)
        self.assertEqual(analyze_images.max_tokens_for_kind("back"), 160)
        front = analyze_images.schema_for_kind("front")["required"]
        back = analyze_images.schema_for_kind("back")["required"]
        self.assertIn("postal_delivery", front)
        self.assertIn("porch_access", front)
        self.assertNotIn("weapon_detected", front)
        self.assertIn("weapon_detected", back)
        self.assertIn("approaching_house", back)
        self.assertNotIn("postal_delivery", back)

    def test_trigger_person_dog_car(self):
        trig = analyze_images.llm_should_trigger
        self.assertTrue(trig({"person": True}))
        self.assertTrue(trig({"dog": True}))
        self.assertTrue(trig({"cat": True}))
        self.assertTrue(trig({"bird": True}))
        self.assertTrue(trig({"person": True, "car": True}))
        self.assertFalse(trig({"car": True}))
        self.assertFalse(trig({}))
        self.assertFalse(trig(None))

    def test_merge_keeps_yolo(self):
        fp = {"person": True, "car": True, "fast_pass": "partial"}
        llm = {"postal_delivery": False, "postal_how": "none",
               "porch_access": True, "animal_detected": True,
               "animal_type": "dog", "_yolo": ["person", "car"],
               "person_at_car": True}
        rec = analyze_images.merge_llm_into_fastpass(
            fp, llm, model="gemma4:e2b", duration_s=1.2, schema=analyze_images.FRONT_SCHEMA)
        self.assertTrue(rec["person"])
        self.assertTrue(rec["car"])
        self.assertTrue(rec["porch_access"])
        self.assertEqual(rec["animal_type"], "dog")
        self.assertEqual(rec["_llm_model"], "gemma4:e2b")
        self.assertEqual(rec["_llm_ms"], 1200)
        self.assertNotIn("fast_pass", rec)
        self.assertEqual(rec["_yolo"], ["person", "car"])
        self.assertNotIn("person_at_car", rec)
        self.assertFalse(rec["dog_walked"])
        self.assertFalse(rec["car_access"])
        self.assertFalse(rec["enters_car"])
        self.assertFalse(rec["exits_car"])
        self.assertFalse(rec["opens_box"])
        self.assertEqual(rec["car_outfit"], "")
        self.assertEqual(rec["car_color"], "")
        self.assertEqual(rec["car_make"], "")
        self.assertEqual(rec["_llm"]["car_outfit"], "")
        self.assertFalse(rec["_llm"]["dog_walked"])
        self.assertEqual(rec["postal_how"], "none")

    def test_merge_skip_low_mem(self):
        fp = {"person": True, "dog": True}
        rec = analyze_images.merge_llm_into_fastpass(fp, None, skip_reason="ram_tight")
        self.assertTrue(rec["person"])
        self.assertTrue(rec["dog"])
        self.assertEqual(rec["fast_pass"], "partial")
        self.assertEqual(rec["_llm_skip"], "ram_tight")

    def test_analyze_local_payload_uses_schema_not_prompt(self):
        ai = analyze_images
        ai.MODEL_PRIMARY, ai.MODEL_FALLBACK = "gemma4:e2b", ""
        ai.MIN_MEM_FOR_LOCAL_GB = 0.0
        ai.encode_image = lambda p: "x"
        captured = {}

        class OK:
            def json(self):
                return {"message": {"content": '{"postal_delivery": false, "postal_how": "none", "dog_walked": false, "car_access": false, "enters_car": false, "exits_car": false, "car_outfit": "none", "car_color": "none", "car_make": "none", "opens_box": false, "porch_access": true, "animal_detected": false, "animal_type": "none"}'}}

        def fake_chat(payload, timeout):
            captured.update(payload)
            return OK()

        orig, ai._ollama_chat = ai._ollama_chat, fake_chat
        orig_thr, ai.local_mem_threshold = ai.local_mem_threshold, (lambda: 0.0)
        ai.RATE_LIMITED = False
        try:
            res = ai.analyze_image_local("/mnt/models/Webcam21/10.0.0.21_x.jpg")
        finally:
            ai._ollama_chat = orig
            ai.local_mem_threshold = orig_thr
        self.assertEqual(captured["format"], ai.FRONT_SCHEMA)
        self.assertEqual(captured["think"], False)
        self.assertEqual(captured["options"]["num_predict"], 220)
        self.assertEqual(captured["messages"][0]["content"], "Look at this image and answer the questions.")
        self.assertNotIn("postal_delivery", captured["messages"][0]["content"])
        self.assertNotIn("JSON", captured["messages"][0]["content"])
        self.assertEqual(res["porch_access"], True)
        self.assertEqual(res["postal_how"], "none")

    def test_schema_for_kind_accepts_path(self):
        sfk = analyze_images.schema_for_kind
        self.assertIs(sfk("/mnt/models/Webcam21/10.0.0.21_x.jpg"), analyze_images.FRONT_SCHEMA)
        self.assertIs(sfk("/mnt/models/Webcam22/10.0.0.22_x.jpg"), analyze_images.BACK_SCHEMA)

    def test_entry_caption_matches_ha_facts(self):
        cap = analyze_images.entry_caption
        self.assertEqual(cap(None), "")
        self.assertEqual(cap({}), "")
        self.assertEqual(cap({"description": "A person at the gate", "porch_access": True}),
                         "A person at the gate")
        self.assertEqual(cap({"description": "   ", "porch_access": True}),
                         "Someone at the porch")
        self.assertEqual(cap({"postal_delivery": True, "postal_how": "on foot"}),
                         "Postal delivery on foot")
        self.assertEqual(cap({"postal_delivery": True, "postal_how": "van"}),
                         "Postal delivery by van")
        self.assertEqual(cap({"postal_delivery": True, "postal_how": "none"}),
                         "Postal delivery")
        self.assertEqual(cap({"porch_access": True}), "Someone at the porch")
        self.assertEqual(cap({"dog_walked": True}), "Person walking a dog")
        self.assertEqual(cap({"animal_detected": True, "animal_type": "dog"}),
                         "Dog in the yard")
        self.assertEqual(cap({"animal_detected": True, "animal_type": "none"}),
                         "Animal in the yard")
        self.assertEqual(cap({"clothes_drying": True}), "Clothes on the line")
        self.assertEqual(cap({"car_access": True}), "Car in the driveway")
        self.assertEqual(cap({
            "postal_delivery": True, "postal_how": "on foot",
            "porch_access": True, "clothes_drying": True,
        }), "Postal delivery on foot. Someone at the porch")
        self.assertEqual(cap({
            "dog_walked": True, "animal_detected": True, "animal_type": "dog",
        }), "Person walking a dog")

    def test_analyze_openrouter_payload_uses_schema(self):
        ai = analyze_images
        ai.encode_image = lambda p: "x"
        captured = {}

        class OK:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": '{"porch_access": true}'}}]}

        def fake_post(url, headers=None, data=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json.loads(data)
            return OK()

        orig = ai.requests.post
        ai.requests.post = fake_post
        try:
            res = ai.analyze_image_openrouter(
                "/mnt/models/Webcam21/10.0.0.21_x.jpg", "sk-test")
        finally:
            ai.requests.post = orig
        payload = captured["payload"]
        rf = payload["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertEqual(rf["json_schema"]["schema"], ai.FRONT_SCHEMA)
        self.assertTrue(rf["json_schema"]["strict"])
        self.assertEqual(payload["max_tokens"], 220)
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["messages"][0]["content"][0]["text"],
                         "Look at this image and answer the questions.")
        self.assertNotIn("postal_delivery", payload["messages"][0]["content"][0]["text"])
        self.assertEqual(res, {"porch_access": True})

        captured.clear()
        ai.requests.post = fake_post
        try:
            ai.analyze_image_openrouter(
                "/mnt/models/Webcam22/10.0.0.22_x.jpg", "sk-test")
        finally:
            ai.requests.post = orig
        self.assertEqual(captured["payload"]["response_format"]["json_schema"]["schema"],
                         ai.BACK_SCHEMA)
        self.assertEqual(captured["payload"]["max_tokens"], 160)

    def test_notify_uses_detector_labels_and_caption(self):
        """objects notify must get YOLO labels, not HA true keys."""
        ai = analyze_images
        captured = {}

        def fake_local(path):
            return {
                "postal_delivery": False, "postal_how": "none",
                "porch_access": True, "animal_detected": False,
                "animal_type": "none",
            }

        def fake_notify(name, labels, caption, path):
            captured["name"] = name
            captured["labels"] = labels
            captured["caption"] = caption
            captured["path"] = path

        with tempfile.TemporaryDirectory() as d:
            orig_st, orig_log = ai.INFERENCE_STATUS, ai.INFERENCE_LOG
            orig_local, orig_cloud = ai.analyze_image_local, ai.analyze_image_openrouter
            orig_notify = intg.notify_image
            ai.INFERENCE_STATUS = os.path.join(d, "st.json")
            ai.INFERENCE_LOG = os.path.join(d, "log.json")
            ai.analyze_image_local = fake_local
            ai.analyze_image_openrouter = lambda *a, **k: None
            intg.notify_image = fake_notify
            ai.RATE_LIMITED = False
            try:
                res = ai.run_deep_pass(
                    "/mnt/models/Webcam21/10.0.0.21_x.jpg", "x.jpg",
                    True, None, "priority", fp_labels=["person", "dog"])
            finally:
                ai.INFERENCE_STATUS = orig_st
                ai.INFERENCE_LOG = orig_log
                ai.analyze_image_local = orig_local
                ai.analyze_image_openrouter = orig_cloud
                intg.notify_image = orig_notify
        self.assertEqual(res["porch_access"], True)
        self.assertEqual(captured["labels"], ["person", "dog"])
        self.assertEqual(captured["caption"], "Someone at the porch")
        self.assertNotIn("porch_access", captured["labels"])

    def test_notify_skips_backfill_and_prefers_description(self):
        ai = analyze_images
        calls = []

        def fake_local(path):
            return {"description": "A person at the gate", "porch_access": True}

        def fake_notify(name, labels, caption, path):
            calls.append((labels, caption))

        with tempfile.TemporaryDirectory() as d:
            orig_st, orig_log = ai.INFERENCE_STATUS, ai.INFERENCE_LOG
            orig_local = ai.analyze_image_local
            orig_notify = intg.notify_image
            ai.INFERENCE_STATUS = os.path.join(d, "st.json")
            ai.INFERENCE_LOG = os.path.join(d, "log.json")
            ai.analyze_image_local = fake_local
            intg.notify_image = fake_notify
            ai.RATE_LIMITED = False
            try:
                ai.run_deep_pass("x.jpg", "x.jpg", True, None, "backfill",
                                 fp_labels=["person"])
                self.assertEqual(calls, [])
                ai.run_deep_pass("x.jpg", "x.jpg", True, None, "priority",
                                 fp_labels=["person"])
            finally:
                ai.INFERENCE_STATUS = orig_st
                ai.INFERENCE_LOG = orig_log
                ai.analyze_image_local = orig_local
                intg.notify_image = orig_notify
        self.assertEqual(calls, [(["person"], "A person at the gate")])

    def test_notify_ha_false_still_sends_detector_labels(self):
        """A person/dog YOLO hit with every HA flag false must still notify."""
        ai = analyze_images
        captured = {}

        def fake_local(path):
            return {
                "postal_delivery": False, "porch_access": False,
                "dog_walked": False, "animal_detected": False,
            }

        def fake_notify(name, labels, caption, path):
            captured["labels"] = labels
            captured["caption"] = caption

        with tempfile.TemporaryDirectory() as d:
            orig_st, orig_log = ai.INFERENCE_STATUS, ai.INFERENCE_LOG
            orig_local, orig_notify = ai.analyze_image_local, intg.notify_image
            ai.INFERENCE_STATUS = os.path.join(d, "st.json")
            ai.INFERENCE_LOG = os.path.join(d, "log.json")
            ai.analyze_image_local = fake_local
            intg.notify_image = fake_notify
            ai.RATE_LIMITED = False
            try:
                ai.run_deep_pass("x.jpg", "x.jpg", True, None, "priority",
                                 fp_labels=["person"])
            finally:
                ai.INFERENCE_STATUS = orig_st
                ai.INFERENCE_LOG = orig_log
                ai.analyze_image_local = orig_local
                intg.notify_image = orig_notify
        self.assertEqual(captured["labels"], ["person"])
        self.assertEqual(captured["caption"], "")


class TestCameraOfflineHours(unittest.TestCase):
    """Slack cam_* alerts and /api/status stale share one default: 24h."""

    def test_missing_settings_key_uses_24_not_12(self):
        hours = analyze_images.camera_offline_hours({})
        self.assertEqual(hours, 24)
        self.assertNotEqual(hours, 12)

    def test_missing_key_in_file_uses_24(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "settings.json")
            with open(path, "w") as f:
                json.dump({"max_dir_gb": 5.0}, f)
            self.assertEqual(analyze_images.camera_offline_hours(path=path), 24)

    def test_unreadable_settings_uses_24(self):
        self.assertEqual(
            analyze_images.camera_offline_hours(path="/no/such/settings.json"), 24)

    def test_explicit_value_honored(self):
        self.assertEqual(
            analyze_images.camera_offline_hours({"camera_offline_hours": 8}), 8)

    def test_status_stale_uses_24h_when_key_missing(self):
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "x.jpg")
            open(img, "w").close()
            now = time.time()
            os.utime(img, (now - 13 * 3600, now - 13 * 3600))
            orig = api_server.WATCH_DIRS
            api_server.WATCH_DIRS = [d]
            try:
                mid = api_server._camera_stats(5.0, {})
                os.utime(img, (now - 25 * 3600, now - 25 * 3600))
                late = api_server._camera_stats(5.0, {})
            finally:
                api_server.WATCH_DIRS = orig
        self.assertFalse(mid[0]["stale"])   # 13h < 24h default (would be stale at 12)
        self.assertTrue(late[0]["stale"])   # 25h > 24h


class TestNotifyImageMode(unittest.TestCase):
    """objects-mode gating on detector labels — no Slack/ntfy network."""

    def setUp(self):
        self.posts = []
        self.td = tempfile.TemporaryDirectory()
        self._orig_state = intg.STATE_FILE
        self._orig_load = intg.load_config
        self._orig_provider = intg._provider
        intg.STATE_FILE = os.path.join(self.td.name, "state.json")

        class FakeMod:
            @staticmethod
            def post_image(cfg, filename, labels, caption, image_path):
                self.posts.append({
                    "filename": filename,
                    "labels": list(labels or []),
                    "caption": caption,
                    "path": image_path,
                })
                return True, "ok"

        intg._provider = lambda name: FakeMod()

    def tearDown(self):
        intg.STATE_FILE = self._orig_state
        intg.load_config = self._orig_load
        intg._provider = self._orig_provider
        self.td.cleanup()

    def test_objects_mode_sends_detector_person_even_if_ha_false(self):
        intg.load_config = lambda: {
            "slack": {"enabled": True, "notify_mode": "objects"},
        }
        intg.notify_image("x.jpg", ["person"], "", "/tmp/x.jpg")
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0]["labels"], ["person"])
        self.assertEqual(self.posts[0]["caption"], "")
        self.assertNotIn("porch_access", self.posts[0]["labels"])

    def test_objects_mode_skips_ha_only_porch_without_detector_labels(self):
        intg.load_config = lambda: {
            "slack": {"enabled": True, "notify_mode": "objects"},
        }
        intg.notify_image("x.jpg", [], "Someone at the porch", "/tmp/x.jpg")
        self.assertEqual(self.posts, [])

    def test_context_mode_skips_per_image(self):
        intg.load_config = lambda: {
            "slack": {"enabled": True, "notify_mode": "context"},
        }
        intg.notify_image("x.jpg", ["person"], "", "/tmp/x.jpg")
        self.assertEqual(self.posts, [])


if __name__ == "__main__":
    unittest.main()
