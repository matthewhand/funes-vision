"""Baseline unit tests for the webcam project's pure helpers.

Run from the repo root:  python3 -m unittest discover -s tests
No third-party deps — stdlib unittest only. These cover the small, pure
logic units; DOM/UI behaviour is proven separately (extracted pure fns +
the deployed app).
"""
import json
import os
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
            ai._atomic_write_json(p, {"b": 2})        # overwrite
            self.assertEqual(json.load(open(p)), {"b": 2})
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
    def test_endpoint_default_server(self):
        self.assertEqual(ntfy._endpoint({"topic": "home"}), "https://ntfy.sh/home")

    def test_endpoint_custom_server_strips_slashes(self):
        self.assertEqual(
            ntfy._endpoint({"server_url": "https://n.example.com/", "topic": "/cams/"}),
            "https://n.example.com/cams")

    def test_endpoint_requires_topic(self):
        self.assertEqual(ntfy._endpoint({}), "")
        self.assertEqual(ntfy._endpoint({"topic": "  "}), "")

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
        "b.jpg": {"person": True},                                # verified (LLM verdict)
        "c.jpg": {"person": False, "fast_pass": "negative"},      # preliminary record, no true label
        "d.jpg": {"dog": True, "_yolo": ["dog"]},                 # verified, _yolo ignored
        "e.jpg": {"person": True, "car": True, "fast_pass": "partial"},  # preliminary, 2 labels
        "f.jpg": {"person": False, "dog": False},                 # verified-but-empty
    }

    def test_verified_only_true_verdicts(self):
        h = api_server.Handler
        self.assertEqual(h._verified_detections(self.ANALYSIS),
                         {"b.jpg": ["person"], "d.jpg": ["dog"]})

    def test_preliminary_detector_only_with_labels(self):
        h = api_server.Handler
        self.assertEqual(h._preliminary_detections(self.ANALYSIS),
                         {"a.jpg": ["car"], "e.jpg": ["car", "person"]})

    def test_verified_and_preliminary_are_disjoint(self):
        h = api_server.Handler
        v = set(h._verified_detections(self.ANALYSIS))
        p = set(h._preliminary_detections(self.ANALYSIS))
        self.assertEqual(v & p, set())


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
               "animal_type": "dog", "_yolo": ["person", "car"]}
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


if __name__ == "__main__":
    unittest.main()
