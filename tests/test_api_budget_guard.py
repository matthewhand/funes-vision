"""The disk budget must be a number, and the redaction must reach every level.

Two #54 defects, both on the unauthenticated read path:

**A hostile ``max_dir_gb`` took /api/status and /api/health down.** #28 gave
the pipeline `analyze_images.setting_num` for exactly this and routed its own
call sites through it, but the two api_server call sites were left doing
`settings.get("max_dir_gb", 5.0)` and `max_dir_gb * 1024**3` raw -- the same
"the two call sites disagreed" pattern #22 fixed on the pipeline side. Over
the wire the failure is worse than a 500: the TypeError is raised while the
response is being built, so the handler thread dies with nothing written and
the client sees the connection close with no reply at all. Both endpoints are
polled hard -- the SPA, the uptime monitor, and `tools/watchdog.sh::cmd_check`,
which then read `cams=[]` -> `MAX_BUDGET=0` and lost its 90%-budget retention
kick silently.

A non-finite budget is the quieter half: `1e309` and `NaN` survived the
multiply and reached the body as bare `Infinity`/`NaN`, which is not JSON.
JSON.parse() throws on it, so /api/status answered 200 and the browser showed
nothing at all.

**redacted_settings() was a shallow denylist.** It tested the *value*
lowercased, not the key, so `privateKey` sailed through, and the marker
`api_key` never matches the hyphen in `x-api-key`. It did not recurse, so
`integrations.slack.bot_token` and `servers[0].token` were serialized verbatim
into a body anyone can fetch. 17 of 36 hostile key shapes survived with the
literal secret attached.
"""
import http.client
import io
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import URLError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_server  # noqa: E402
import integrations  # noqa: E402

# Every way a hand-edited settings.json (or a hostile one) can spell "not a
# number". None/"5.0"/"abc"/{} all raised TypeError on the * 1024**3.
HOSTILE = [None, "5.0", "abc", {}, [], True, False, 1e309, float("inf"),
           float("nan"), "NaN", "Infinity", -1, -1e309]

SECRET = "SUPERSECRET"


class FakeHandler:
    """Drive api_server.Handler.do_GET with captured output."""

    def __init__(self):
        self.sent = []
        self.handler = api_server.Handler.__new__(api_server.Handler)

    def run(self, path):
        h = self.handler
        h.path = path
        h.headers = {"Content-Length": "0"}
        h.rfile = io.BytesIO()
        h.wfile = io.BytesIO()
        h.sent = []
        h._send = lambda code, body: h.sent.append((code, body))
        h.do_GET()
        return h.sent[-1] if h.sent else (None, None)


class SandboxCase(unittest.TestCase):
    """Temp settings.json and camera dirs; never /mnt/models."""

    GLOBALS = ("WATCH_DIRS", "BASE_DIR", "SETTINGS_FILE", "INTEGRATIONS_FILE",
               "PINS_FILE")
    ENV = ("WEBCAM_API_TOKEN", "WEBCAM_CORS_ORIGIN", "WEBCAM_PROBE_TTL",
           "WEBCAM_API_SOCKET_TIMEOUT", "WEBCAM_API_MAX_CONNECTIONS")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="webcam_budget_")
        self.front = os.path.join(self.tmp, "front")
        os.makedirs(self.front)
        self.settings_path = os.path.join(self.tmp, "settings.json")
        self._orig = {g: getattr(api_server, g) for g in self.GLOBALS}
        self._orig_state = integrations.STATE_FILE
        self._orig_env = {k: os.environ.pop(k, None) for k in self.ENV}
        api_server.WATCH_DIRS = [self.front]
        api_server.BASE_DIR = self.tmp
        api_server.SETTINGS_FILE = self.settings_path
        api_server.INTEGRATIONS_FILE = os.path.join(self.tmp, "integrations.json")
        api_server.PINS_FILE = os.path.join(self.tmp, "pins.json")
        integrations.STATE_FILE = os.path.join(self.tmp, "integrations_state.json")
        os.environ["WEBCAM_CORS_ORIGIN"] = "http://localhost:8180"
        api_server.reset_probe_cache()
        self.addCleanup(api_server.reset_probe_cache)
        # No forks, no connects: pgrep and a 3s Ollama socket have nothing to
        # find in a temp tree and only make these tests slow.
        for target, attr, repl in (
                (api_server.subprocess, "run",
                 mock.Mock(return_value=mock.Mock(returncode=1))),
                (api_server.urllib.request, "urlopen",
                 mock.Mock(side_effect=URLError("no ollama in tests")))):
            patcher = mock.patch.object(target, attr, repl)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.h = FakeHandler()

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
            json.dump(cfg, f, allow_nan=True)
        return self.settings_path


class TestHostileMaxDirGb(SandboxCase):
    """#54 part 2: the budget call site is not routed through the guard."""

    def test_accessor_never_raises_and_never_goes_non_finite(self):
        for bad in HOSTILE:
            with self.subTest(max_dir_gb=bad):
                gb = api_server.setting_budget_gb({"max_dir_gb": bad})
                self.assertIsInstance(gb, float)
                self.assertTrue(math.isfinite(gb),
                                f"{bad!r} produced a non-finite budget")
                self.assertGreaterEqual(gb, 0.0)

    def test_absent_key_keeps_the_documented_default(self):
        self.assertEqual(api_server.setting_budget_gb({}), 5.0)
        # A reload that does not mention the key must not clobber it.
        self.assertEqual(api_server.setting_budget_gb(None), 5.0)
        self.assertEqual(api_server.setting_budget_gb([]), 5.0)

    def test_a_good_value_is_still_honoured(self):
        for good in (1.5, 20, 0):
            with self.subTest(max_dir_gb=good):
                self.assertEqual(api_server.setting_budget_gb(
                    {"max_dir_gb": good}), float(good))

    def test_budget_bytes_is_safe_for_a_direct_caller(self):
        # _camera_stats is the other half of the crash; guarding only the
        # accessor would leave a raw call reintroducing it.
        for bad in HOSTILE:
            with self.subTest(max_dir_gb=bad):
                n = api_server._budget_bytes(bad)
                self.assertIsInstance(n, float)
                self.assertTrue(math.isfinite(n) and n >= 0)

    def test_status_answers_200_with_a_numeric_budget(self):
        for bad in HOSTILE:
            with self.subTest(max_dir_gb=bad):
                self.write_settings(watch_dirs=[self.front], max_dir_gb=bad)
                code, body = self.h.run("/api/status")
                self.assertEqual(code, 200, "a hostile value took /api/status down")
                cams = body["cameras"]
                self.assertEqual(len(cams), 1, "cameras=[] is what killed the "
                                                "watchdog's retention kick")
                pct = cams[0]["budget_pct"]
                self.assertTrue(pct is None or isinstance(pct, (int, float)),
                                f"budget_pct={pct!r} is not a number")

    def test_health_answers_with_a_body_for_every_hostile_value(self):
        for bad in HOSTILE:
            with self.subTest(max_dir_gb=bad):
                self.write_settings(watch_dirs=[self.front], max_dir_gb=bad)
                code, body = self.h.run("/api/health")
                # 503 = degraded, which is the honest answer with no pipeline
                # running. What must not happen is an exception.
                self.assertIn(code, (200, 503))
                self.assertEqual(len(body["cameras"]), 1)

    @staticmethod
    def _strict_json_loads(raw):
        """json.loads, but a bare NaN/Infinity/-Infinity is an error.

        Python accepts those three by default, which is exactly why they
        reach the wire: nothing upstream complained. JSON.parse() does not
        accept them, so this is the check that matches the browser."""
        def _reject(constant):
            raise AssertionError(f"non-JSON constant in the body: {constant}")

        return json.loads(raw, parse_constant=_reject)

    def test_no_bare_infinity_or_nan_reaches_the_body(self):
        """json.dumps writes inf/nan unquoted, which JSON.parse() rejects.

        Driven through a real listener: this is about the bytes `_send`
        actually puts on the wire, and the socket-free FakeHandler replaces
        `_send` with a capture double, so only a real request can show it."""
        srv = api_server.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), api_server.Handler)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        for bad in (1e309, float("inf"), float("nan"), "NaN", "Infinity"):
            with self.subTest(max_dir_gb=bad):
                self.write_settings(watch_dirs=[self.front], max_dir_gb=bad)
                conn = http.client.HTTPConnection(
                    "127.0.0.1", srv.server_address[1], timeout=10)
                self.addCleanup(conn.close)
                conn.request("GET", "/api/status", headers={"Host": "127.0.0.1"})
                resp = conn.getresponse()
                raw = resp.read().decode()
                self.assertEqual(resp.status, 200)
                # Raises AssertionError on a bare NaN/Infinity, exactly where
                # JSON.parse() would raise SyntaxError.
                self._strict_json_loads(raw)

    def test_json_safe_leaves_finite_numbers_and_bools_alone(self):
        for value in (0, 1, -3, 2.5, True, False, "x", None, [1, 2.5],
                      {"a": 1.5}, (), float("-inf")):
            with self.subTest(value=value):
                out = api_server.json_safe(value)
                self._strict_json_loads(json.dumps(out))
        self.assertEqual(api_server.json_safe(1.5), 1.5)
        self.assertIs(api_server.json_safe(True), True)
        self.assertIsNone(api_server.json_safe(float("inf")))

    def test_the_connection_is_not_dropped_over_a_real_socket(self):
        """The wire shape of the crash: the TypeError killed the handler
        thread mid-response, so the client got RemoteDisconnected rather than
        any status at all. Driven through a real listener so that is
        observable -- a socket-free FakeHandler cannot show it."""
        self.write_settings(watch_dirs=[self.front], max_dir_gb=None)
        srv = api_server.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), api_server.Handler)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        for path in ("/api/status", "/api/health"):
            with self.subTest(path=path):
                conn = http.client.HTTPConnection(
                    "127.0.0.1", srv.server_address[1], timeout=10)
                self.addCleanup(conn.close)
                conn.request("GET", path, headers={"Host": "127.0.0.1"})
                resp = conn.getresponse()
                self.assertIn(resp.status, (200, 503))
                json.loads(resp.read())  # parses, so the SPA can read it


class TestRedactionReachesEveryLevel(SandboxCase):
    """#54 part 3: recurse, and stop testing the value instead of the key."""

    # key, where it lives, and whether a *whole subtree* must go
    HOSTILE_KEYS = [
        ("privateKey", {"privateKey": SECRET}),
        ("x-api-key", {"x-api-key": SECRET}),
        ("ssh_key", {"ssh_key": SECRET}),
        ("openai_key", {"openai_key": SECRET}),
        ("webhook_url", {"webhook_url": SECRET}),
        ("AUTHORIZATION", {"AUTHORIZATION": SECRET}),
        ("API-TOKEN", {"API-TOKEN": SECRET}),
        ("Bot.Token", {"Bot.Token": SECRET}),
        ("integrations.slack.bot_token",
         {"integrations": {"slack": {"bot_token": SECRET, "channel_id": "C1"}}}),
        ("servers[].token", {"servers": [{"token": SECRET}, {"host": "db"}]}),
        ("deep.nested.credentials",
         {"a": {"b": {"c": {"credentials": {"k": SECRET}}}}}),
        ("list of maps", {"servers": [{"password": SECRET}]}),
    ]

    def test_each_hostile_shape_is_dropped_from_the_wire(self):
        for label, cfg in self.HOSTILE_KEYS:
            with self.subTest(key=label):
                self.write_settings(**cfg)
                code, body = self.h.run("/api/status")
                self.assertEqual(code, 200)
                raw = json.dumps(body)
                self.assertNotIn(SECRET, raw,
                                 f"{label} reached the unauthenticated body")

    def test_each_hostile_shape_is_dropped_by_the_accessor(self):
        for label, cfg in self.HOSTILE_KEYS:
            with self.subTest(key=label):
                self.assertNotIn(
                    SECRET, json.dumps(api_server.redacted_settings(cfg)),
                    f"{label} survived redacted_settings()")

    def test_benign_nested_values_survive(self):
        """Recursion must not become "replace everything nested with {}"."""
        self.write_settings(**{
            "integrations": {"slack": {"bot_token": SECRET, "channel_id": "C1"}},
            "servers": [{"token": SECRET, "host": "db", "port": 5432}],
        })
        _, body = self.h.run("/api/status")
        settings = body["settings"]
        self.assertEqual(settings["integrations"]["slack"]["channel_id"], "C1")
        self.assertEqual(settings["servers"], [{"host": "db", "port": 5432}])
        self.assertNotIn(SECRET, json.dumps(settings))

    def test_a_secret_subtree_is_dropped_whole(self):
        """A key that names a secret takes its whole value with it, so nothing
        beneath it can be serialized by accident; a *benign* key keeps its
        non-secret children and loses only the secret ones."""
        self.write_settings(
            mqtt={"broker": "10.0.0.1", "password": SECRET,
                  "nested": {"api-key": SECRET, "keep": 1}},
            slack={"bot_token": {"value": SECRET}, "channel_id": "C1"})
        _, body = self.h.run("/api/status")
        settings = body["settings"]
        self.assertNotIn(SECRET, json.dumps(settings))
        # mqtt is not itself a secret: its benign children survive.
        self.assertEqual(settings["mqtt"]["broker"], "10.0.0.1")
        self.assertEqual(settings["mqtt"]["nested"], {"keep": 1})
        self.assertNotIn("password", settings["mqtt"])
        self.assertNotIn("api-key", settings["mqtt"]["nested"])
        # bot_token IS: the subtree goes, not just the leaf.
        self.assertNotIn("bot_token", settings["slack"])
        self.assertEqual(settings["slack"]["channel_id"], "C1")

    def test_is_secret_key_normalises_case_and_separators(self):
        for spelling in ("api_key", "API_KEY", "api-key", "x-api-key",
                         "X-Api-Key", "apiKey", "ApiKey"):
            self.assertTrue(api_server.is_secret_key(spelling), spelling)
        for benign in ("max_dir_gb", "watch_dirs", "gate_ignore_labels",
                       "model_local", "fast_pass_engine", "timezone",
                       "allow_cloud", "max_age_days", "deep_passes_enabled"):
            self.assertFalse(api_server.is_secret_key(benign), benign)

    def test_the_documented_status_shape_is_unchanged(self):
        """API.md documents `settings` as the full settings.json minus
        secrets, and the SPA reads these five keys out of it. An allowlist
        inversion would be a smaller hole but would break the documented
        contract, so the fix is a stronger denylist, not a different one."""
        self.write_settings(watch_dirs=[self.front], max_dir_gb=7.5,
                            model_local="gemma4:e2b", idle_sweep_seconds=45,
                            gate_ignore_labels=["car"],
                            fast_pass_engine="yolo", deep_backfill=True,
                            burst_summaries_enabled=True,
                            deep_passes_enabled=False, api_token="tok")
        _, body = self.h.run("/api/status")
        settings = body["settings"]
        for key in ("max_dir_gb", "model_local", "idle_sweep_seconds",
                    "gate_ignore_labels", "fast_pass_engine", "deep_backfill",
                    "burst_summaries_enabled", "deep_passes_enabled"):
            self.assertIn(key, settings)
        self.assertNotIn("api_token", settings)

    def test_non_dict_settings_stay_empty(self):
        self.assertEqual(api_server.redacted_settings([]), {})
        self.assertEqual(api_server.redacted_settings(None), {})
        self.assertEqual(api_server.redacted_settings("x"), {})


if __name__ == "__main__":
    unittest.main()
