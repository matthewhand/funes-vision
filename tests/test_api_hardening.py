"""Regression tests for the API auth / secrets / resource-safety defects.

Covers #19 (status leaked the write token), #24 (cross-origin writes in the
default no-token deployment), #25 (blank WEBCAM_API_TOKEN failed open),
#26 (lockless pin read-modify-write), #32 (world-readable secret files and a
signed upload URL in the delivery state) and #33 (no socket timeout,
unvalidated Content-Length, per-request probe forks).

Mostly socket-free: the handler is driven directly, matching
test_api_cameras.py / test_api_endpoints.py. The only real threads are the
concurrent pins in TestPinConcurrency, which is the point of that test.
"""
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import URLError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_server
import integrations


class FakeHandler:
    """Drive api_server.Handler.do_GET / do_POST with captured output."""

    def __init__(self):
        self.sent = []
        self.handler = api_server.Handler.__new__(api_server.Handler)

    def _send(self, code, body):
        self.sent.append((code, body))

    def run(self, method, path, body=b"", headers=None):
        h = self.handler
        h._send = self._send
        h.path = path
        hdrs = {"Content-Length": str(len(body))}
        if headers:
            hdrs.update(headers)
        h.headers = hdrs
        h.rfile = io.BytesIO(body)
        h.wfile = io.BytesIO()
        if method == "GET":
            h.do_GET()
        else:
            h.do_POST()
        return self.sent[-1] if self.sent else (None, None)


class SandboxCase(unittest.TestCase):
    """Per-test camera roots and config in a temp dir; never /mnt/models."""

    GLOBALS = ("WATCH_DIRS", "BASE_DIR", "SETTINGS_FILE", "INTEGRATIONS_FILE",
               "PINS_FILE")
    ENV = ("WEBCAM_API_TOKEN", "WEBCAM_CORS_ORIGIN", "WEBCAM_SSE_MAX_CLIENTS",
           "WEBCAM_API_SOCKET_TIMEOUT", "WEBCAM_API_MAX_BODY",
           "WEBCAM_PROBE_TTL")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="webcam_api_hard_")
        self.front = os.path.join(self.tmp, "front")
        os.makedirs(self.front)
        self.settings_path = os.path.join(self.tmp, "settings.json")
        self.integrations_path = os.path.join(self.tmp, "integrations.json")
        self.state_path = os.path.join(self.tmp, "integrations_state.json")
        self._orig = {g: getattr(api_server, g) for g in self.GLOBALS}
        self._orig_state = integrations.STATE_FILE
        self._orig_env = {k: os.environ.pop(k, None) for k in self.ENV}
        api_server.WATCH_DIRS = [self.front]
        api_server.BASE_DIR = self.tmp
        api_server.SETTINGS_FILE = self.settings_path
        api_server.INTEGRATIONS_FILE = self.integrations_path
        api_server.PINS_FILE = os.path.join(self.tmp, "pins.json")
        integrations.STATE_FILE = self.state_path
        os.environ["WEBCAM_CORS_ORIGIN"] = "http://localhost:8180"
        # The liveness probes are cached process-wide, so every test starts
        # cold. Resolved leniently on purpose: this harness has to load against
        # a build that predates the cache, or a regression test would only ever
        # report "no such attribute" instead of the behaviour it guards.
        self._reset_probes = getattr(api_server, "reset_probe_cache", None)
        if self._reset_probes:
            self._reset_probes()
        # No forks and no connects: the pipeline is never running here, and
        # urlopen is a 3s-timeout socket to a box that is not there.
        self.pgrep = mock.Mock(return_value=mock.Mock(returncode=1))
        self.urlopen = mock.Mock(side_effect=URLError("no ollama in tests"))
        for target, attr, repl in ((api_server.subprocess, "run", self.pgrep),
                                   (api_server.urllib.request, "urlopen",
                                    self.urlopen)):
            patcher = mock.patch.object(target, attr, repl)
            patcher.start()
            self.addCleanup(patcher.stop)
        if self._reset_probes:
            self.addCleanup(self._reset_probes)
        self.h = FakeHandler()

    def probe_calls(self):
        """Total subprocess forks + Ollama connects the last status did."""
        return self.pgrep.call_count + self.urlopen.call_count

    def tearDown(self):
        for g, v in self._orig.items():
            setattr(api_server, g, v)
        integrations.STATE_FILE = self._orig_state
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_settings(self, **cfg):
        with open(self.settings_path, "w") as f:
            json.dump(cfg, f)
        return self.settings_path

    def add_frame(self, name, camera=None):
        path = os.path.join(camera or self.front, name)
        with open(path, "w") as f:
            f.write("x")
        return path

    def mode_of(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)


class TestStatusDoesNotLeakToken(SandboxCase):
    """#19: GET /api/status is unauthenticated, so it must not carry api_token."""

    def test_unauth_status_has_no_api_token(self):
        self.write_settings(api_token="SUPERSECRET", idle_sweep_seconds=45,
                            model_local="gemma4:e2b", watch_dirs=[self.front])
        code, body = self.h.run("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertNotIn("api_token", body["settings"])
        self.assertNotIn("SUPERSECRET", json.dumps(body))

    def test_other_secret_key_shapes_are_dropped_too(self):
        self.write_settings(api_token="a", mqtt_password="hunter2",
                            slack_app_secret="s3cr3t", webhook_token="t",
                            private_key="k", model_local="gemma4:e2b")
        _, body = self.h.run("GET", "/api/status")
        for leaked in ("hunter2", "s3cr3t", "api_token", "mqtt_password",
                       "slack_app_secret", "webhook_token", "private_key"):
            self.assertNotIn(leaked, json.dumps(body))

    def test_redaction_is_a_subset_not_an_empty_dict(self):
        # The SPA reads its config out of status; dropping everything would
        # break the settings panel just as surely as leaking it.
        self.write_settings(api_token="SUPERSECRET", idle_sweep_seconds=45,
                            model_local="gemma4:e2b", allow_cloud=False,
                            watch_dirs=[self.front])
        _, body = self.h.run("GET", "/api/status")
        self.assertEqual(body["settings"]["idle_sweep_seconds"], 45)
        self.assertEqual(body["settings"]["model_local"], "gemma4:e2b")
        self.assertEqual(body["trigger"]["idle_sweep_seconds"], 45)

    def test_health_still_works(self):
        # health_summary() reads settings.deep_passes_enabled out of the same
        # (now redacted) payload -- non-secret keys must survive.
        self.write_settings(api_token="SUPERSECRET", deep_passes_enabled=False)
        code, body = self.h.run("GET", "/api/health")
        self.assertIn(code, (200, 503))
        self.assertIn("llm_reachable", body["checks"])


class TestCrossOriginMutations(SandboxCase):
    """#24: a CORS-safelisted cross-origin write needed no preflight and no
    token, so any page the user visited could rewrite settings."""

    def test_text_plain_cross_origin_post_refused(self):
        code, body = self.h.run(
            "POST", "/api/settings", json.dumps({"idle_sweep_seconds": 1234}).encode(),
            headers={"Origin": "https://evil.example",
                     "Content-Type": "text/plain"})
        self.assertEqual(code, 403)
        self.assertFalse(os.path.exists(self.settings_path))

    def test_form_encoded_cross_origin_post_refused(self):
        code, _ = self.h.run(
            "POST", "/api/integrations",
            json.dumps({"slack": {"bot_token": "xoxb-attacker"}}).encode(),
            headers={"Origin": "https://evil.example",
                     "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(code, 403)
        self.assertFalse(os.path.exists(self.integrations_path))

    def test_cross_origin_refused_even_with_a_valid_token(self):
        # Auth and origin are independent gates: a leaked token still must not
        # be usable from a page the user is visiting.
        self.write_settings(api_token="SUPERSECRET")
        code, _ = self.h.run(
            "POST", "/api/settings", json.dumps({"idle_sweep_seconds": 45}).encode(),
            headers={"Origin": "https://evil.example",
                     "Content-Type": "application/json",
                     "Authorization": "Bearer SUPERSECRET"})
        self.assertEqual(code, 403)
        with open(self.settings_path) as f:
            self.assertNotIn("idle_sweep_seconds", json.load(f))

    def test_cross_origin_refused_with_auth_disabled(self):
        # The shipped default: no api_token, loopback bind. Origin is checked
        # regardless of auth state, which is the whole point.
        code, _ = self.h.run(
            "POST", "/api/delete?camera=front",
            json.dumps({"filename": "front.jpg"}).encode(),
            headers={"Origin": "https://evil.example",
                     "Content-Type": "text/plain"})
        self.assertEqual(code, 403)

    def test_allowlisted_origin_may_still_post_json(self):
        code, _ = self.h.run(
            "POST", "/api/does-not-exist", b"{}",
            headers={"Origin": "http://localhost:8180",
                     "Content-Type": "application/json"})
        self.assertEqual(code, 404)  # passed the guard, no such endpoint

    def test_allowlisted_origin_may_not_post_text_plain(self):
        code, _ = self.h.run(
            "POST", "/api/does-not-exist", b"{}",
            headers={"Origin": "http://localhost:8180",
                     "Content-Type": "text/plain"})
        self.assertEqual(code, 415)

    def test_non_browser_client_unaffected(self):
        # No Origin (curl, tools/watchdog.sh): unchanged behaviour.
        self.write_settings(api_token="SUPERSECRET")
        code, _ = self.h.run(
            "POST", "/api/settings", json.dumps({"idle_sweep_seconds": 45}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer SUPERSECRET"})
        self.assertEqual(code, 200)

    def test_charset_parameter_accepted(self):
        code, _ = self.h.run(
            "POST", "/api/does-not-exist", b"{}",
            headers={"Origin": "http://localhost:8180",
                     "Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(code, 404)


class TestBlankTokenFailsClosed(SandboxCase):
    """#25: a blank-but-present WEBCAM_API_TOKEN was truthy, so it returned ''
    and suppressed the settings.json token -- auth off, silently."""

    def test_whitespace_env_falls_through_to_settings(self):
        self.write_settings(api_token="SUPERSECRET")
        os.environ["WEBCAM_API_TOKEN"] = "   "
        self.assertEqual(api_server.api_token(), "SUPERSECRET")

    def test_whitespace_env_still_authenticates(self):
        self.write_settings(api_token="SUPERSECRET")
        os.environ["WEBCAM_API_TOKEN"] = "   "
        code, _ = self.h.run("POST", "/api/does-not-exist", b"{}")
        self.assertEqual(code, 401)  # no Authorization header presented
        code, _ = self.h.run("POST", "/api/does-not-exist", b"{}",
                             headers={"Authorization": "Bearer SUPERSECRET"})
        self.assertEqual(code, 404)

    def test_blank_env_without_settings_token_is_auth_off(self):
        os.environ["WEBCAM_API_TOKEN"] = "\t\n "
        self.assertEqual(api_server.api_token(), "")

    def test_env_token_is_still_stripped(self):
        os.environ["WEBCAM_API_TOKEN"] = "  padded  "
        self.assertEqual(api_server.api_token(), "padded")

    def test_absent_token_still_means_auth_off(self):
        self.write_settings(watch_dirs=[self.front])
        self.assertEqual(api_server.api_token(), "")

    def test_whitespace_settings_token_is_a_misconfiguration(self):
        # Not "auth off": somebody meant to configure auth. Fail closed.
        self.write_settings(api_token="   ", watch_dirs=[self.front])
        with self.assertRaises(api_server.BlankTokenError):
            api_server.api_token()

    def test_blank_settings_token_refuses_writes(self):
        self.write_settings(api_token="   ", watch_dirs=[self.front])
        code, _ = self.h.run("POST", "/api/does-not-exist", b"{}")
        self.assertEqual(self.h.sent[0][0], 500)
        self.assertIn("blank", self.h.sent[0][1]["error"])
        self.assertEqual(code, 401)

    def test_empty_string_settings_token_is_still_auth_off(self):
        # The documented default: an empty value means "no token configured".
        self.write_settings(api_token="", watch_dirs=[self.front])
        self.assertEqual(api_server.api_token(), "")


class TestPinConcurrency(SandboxCase):
    """#26: 12 concurrent pins persisted 1 -- load_pins/mutate/save_pins with no
    lock, and a plain open(path, 'w') that could tear."""

    COUNT = 12

    def _pin_all(self):
        names = [f"pin_{i:02d}.jpg" for i in range(self.COUNT)]
        for name in names:
            self.add_frame(name)
        barrier = threading.Barrier(self.COUNT)

        def pin(name):
            h = FakeHandler()
            barrier.wait(10)  # maximize the overlap the lock has to survive
            h.run("POST", "/api/pin?camera=front",
                  json.dumps({"filename": name, "pinned": True}).encode())

        threads = [threading.Thread(target=pin, args=(n,)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return names

    def test_all_concurrent_pins_persist(self):
        names = self._pin_all()
        # Assert through load_pins, i.e. what /api/pins serves and the gallery
        # renders: a torn pins.json reads as an empty set, so this is also the
        # "these frames look unpinned and become deletable" case.
        self.assertEqual(api_server.load_pins("front"), set(names))
        with open(os.path.join(self.front, "pins.json")) as f:
            self.assertEqual(set(json.load(f)), set(names))

    def test_pins_file_stays_readable_for_nginx(self):
        # Atomic write must not tighten the mode: nginx serves these as
        # www-data, and a 0600 pins.json 403s the pin badges.
        self._pin_all()
        self.assertEqual(self.mode_of(os.path.join(self.front, "pins.json")),
                         0o644)

    def test_no_temp_files_left_behind(self):
        self._pin_all()
        leftovers = [f for f in os.listdir(self.front) if ".tmp." in f]
        self.assertEqual(leftovers, [])

    def test_concurrent_unpin_and_pin_agree(self):
        names = self._pin_all()
        results = []

        def unpin(name):
            h = FakeHandler()
            h.run("POST", "/api/pin?camera=front",
                  json.dumps({"filename": name, "pinned": False}).encode())
            results.append(name)

        threads = [threading.Thread(target=unpin, args=(n,))
                   for n in names[:self.COUNT // 2]]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        with open(os.path.join(self.front, "pins.json")) as f:
            saved = set(json.load(f))
        self.assertEqual(saved, set(names[self.COUNT // 2:]))


class TestSecretFilePermissions(SandboxCase):
    """#32: secrets were written 0644 (settings.json never even chmod'd) and the
    delivery state file was written 0664 while holding a Slack signed URL."""

    def test_post_settings_writes_0600(self):
        self.write_settings(api_token="SUPERSECRET", watch_dirs=[self.front])
        code, _ = self.h.run("POST", "/api/settings",
                             json.dumps({"idle_sweep_seconds": 45}).encode(),
                             headers={"Authorization": "Bearer SUPERSECRET"})
        self.assertEqual(code, 200)
        self.assertEqual(self.mode_of(self.settings_path), 0o600)

    def test_post_integrations_writes_0600(self):
        code, _ = self.h.run(
            "POST", "/api/integrations",
            json.dumps({"slack": {"bot_token": "xoxb-SECRET"}}).encode())
        self.assertEqual(code, 200)
        self.assertEqual(self.mode_of(self.integrations_path), 0o600)

    def test_delivery_state_written_0600(self):
        integrations._record_delivery("slack", "burst", True, "sent ok")
        self.assertEqual(self.mode_of(self.state_path), 0o600)

    def test_signed_upload_url_is_redacted_from_state(self):
        integrations._record_delivery(
            "slack", "burst", True,
            "posted https://files.slack.com/upload/v1/ABC123"
            "?token=xoxb-SECRET&sig=deadbeefcafe")
        with open(self.state_path) as f:
            raw = f.read()
        self.assertNotIn("xoxb-SECRET", raw)
        self.assertNotIn("deadbeefcafe", raw)
        for leaked in ("token=", "sig=", "?"):
            self.assertNotIn(leaked, raw)
        # The useful half survives, so the UI can still say where it went.
        self.assertIn("files.slack.com/upload/v1/ABC123", raw)

    def test_state_detail_never_served_over_http(self):
        integrations._record_delivery(
            "slack", "burst", False,
            "failed https://slack.test/api?token=xoxb-SECRET&sig=deadbeef")
        code, body = self.h.run("GET", "/api/integrations")
        self.assertEqual(code, 200)
        blob = json.dumps(body)
        self.assertNotIn("xoxb-SECRET", blob)
        self.assertNotIn("deadbeef", blob)
        self.assertNotIn("token=", blob)
        self.assertIn("last_delivery", body["slack"])

    def test_bare_token_in_a_non_url_detail_is_redacted(self):
        # A provider can echo a token outside any URL ("token=... rejected"),
        # so the query-secret rule is not URL-scoped.
        integrations._record_delivery(
            "slack", "alert", False, "auth.error token=abc123secret sig=zz9")
        with open(self.state_path) as f:
            raw = f.read()
        self.assertNotIn("abc123secret", raw)
        self.assertNotIn("zz9", raw)
        self.assertIn("auth.error", raw)

    def test_preexisting_state_file_with_a_secret_is_redacted_on_read(self):
        # A state file written before the redaction, or by hand: read time
        # redacts too, so the leak closes without a manual cleanup.
        with open(self.state_path, "w") as f:
            json.dump({"slack": {"last_delivery": {
                "ts": 1.0, "kind": "burst", "ok": True,
                "detail": "posted https://files.slack.com/x?token=xoxb-OLD&sig=cafe",
            }}}, f)
        _, body = self.h.run("GET", "/api/integrations")
        blob = json.dumps(body)
        self.assertNotIn("xoxb-OLD", blob)
        self.assertNotIn("cafe", blob)

    def test_harden_secret_files_repairs_a_leftover_0644(self):
        for path in (self.settings_path, self.integrations_path):
            with open(path, "w") as f:
                json.dump({"api_token": "x", "slack": {}}, f)
            os.chmod(path, 0o644)
        api_server.harden_secret_files()
        self.assertEqual(self.mode_of(self.settings_path), 0o600)
        self.assertEqual(self.mode_of(self.integrations_path), 0o600)

    def test_harden_secret_files_leaves_a_tight_file_alone(self):
        with open(self.settings_path, "w") as f:
            json.dump({"api_token": "x"}, f)
        os.chmod(self.settings_path, 0o600)
        before = os.stat(self.settings_path).st_mtime_ns
        api_server.harden_secret_files()
        self.assertEqual(self.mode_of(self.settings_path), 0o600)
        self.assertEqual(os.stat(self.settings_path).st_mtime_ns, before)

    def test_concurrent_deliveries_do_not_drop_each_other(self):
        def record(i):
            integrations._record_delivery(f"prov{i % 2}", "burst", True, f"ok{i}")

        threads = [threading.Thread(target=record, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertEqual(sorted(state), ["prov0", "prov1"])
        self.assertEqual(self.mode_of(self.state_path), 0o600)
        leftovers = [f for f in os.listdir(self.tmp) if ".tmp." in f]
        self.assertEqual(leftovers, [])


class TestRequestResourceLimits(SandboxCase):
    """#33: no socket timeout, unvalidated Content-Length, and a fork plus a
    connect per unauthenticated /api/status."""

    def test_huge_content_length_is_rejected_before_reading(self):
        body = json.dumps({"idle_sweep_seconds": 45}).encode()
        h = FakeHandler()
        code, _ = h.run("POST", "/api/settings", body,
                        headers={"Content-Length": "2000000000"})
        self.assertEqual(code, 413)
        # Nothing was buffered: the point is not to allocate it first.
        self.assertEqual(h.handler.rfile.tell(), 0)
        self.assertFalse(os.path.exists(self.settings_path))

    def test_negative_content_length_is_rejected(self):
        code, _ = self.h.run("POST", "/api/settings", b"{}",
                             headers={"Content-Length": "-1"})
        self.assertEqual(code, 413)

    def test_unparseable_content_length_is_a_bad_request(self):
        code, _ = self.h.run("POST", "/api/settings", b"{}",
                             headers={"Content-Length": "banana"})
        self.assertEqual(code, 400)

    def test_one_mib_is_accepted_and_one_more_is_not(self):
        limit = 1024 * 1024
        code, _ = self.h.run("POST", "/api/does-not-exist", b"{}",
                             headers={"Content-Length": str(limit)})
        self.assertEqual(code, 404)  # passed the guard, no such endpoint
        code, _ = self.h.run("POST", "/api/does-not-exist", b"{}",
                             headers={"Content-Length": str(limit + 1)})
        self.assertEqual(code, 413)

    def test_body_limit_is_env_tunable(self):
        os.environ["WEBCAM_API_MAX_BODY"] = "16"
        code, _ = self.h.run("POST", "/api/does-not-exist", b"{}",
                             headers={"Content-Length": "17"})
        self.assertEqual(code, 413)

    def test_handler_has_a_socket_timeout(self):
        h = api_server.Handler.__new__(api_server.Handler)
        self.assertEqual(h.timeout, 30)
        os.environ["WEBCAM_API_SOCKET_TIMEOUT"] = "5"
        self.assertEqual(h.timeout, 5)

    def test_status_probes_are_cached(self):
        self.write_settings(api_token="SUPERSECRET", watch_dirs=[self.front])
        for _ in range(20):
            api_server.pipeline_status()
        # One pgrep, one Ollama connect for twenty polls, not forty.
        self.assertEqual(self.probe_calls(), 2)

    def test_probe_cache_expires(self):
        self.write_settings(api_token="SUPERSECRET", watch_dirs=[self.front])
        os.environ["WEBCAM_PROBE_TTL"] = "0"  # 0 disables caching
        for _ in range(3):
            api_server.pipeline_status()
        self.assertEqual(self.probe_calls(), 6)

    def test_reset_probe_cache_forces_a_reprobe(self):
        self.write_settings(api_token="SUPERSECRET", watch_dirs=[self.front])
        api_server.pipeline_status()
        self.assertEqual(self.probe_calls(), 2)
        api_server.reset_probe_cache()
        api_server.pipeline_status()
        self.assertEqual(self.probe_calls(), 4)

    def test_cached_probe_answers_are_reused(self):
        self.assertTrue(api_server._cached_probe("k", lambda: "first"))
        self.assertEqual(api_server._cached_probe("k", lambda: "second"), "first")


if __name__ == "__main__":
    unittest.main()
