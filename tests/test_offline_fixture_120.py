"""Issue #120: the offline (inference/Ollama down) fixture must be a real diff.

The SPA is only ever exercised against `fixtures/api`, where /api/health says
ok, llm.reachable is true and every logged inference succeeded. That is the
one state nobody ships: with Ollama stopped the pipeline still serves frames,
so the gallery has to stay usable. `fixtures/api-offline` is that state, and
this test pins the *shape* of the difference so the offline fixture cannot
drift into a second, divergent copy of the API surface:

  * the same files, so SCREENSHOT_API_DIR can be swapped without a 404;
  * the same JSON keys, recursively, so a key only one fixture answers would
    turn a render bug into an undefined instead of a visible degraded state;
  * only values differ, and the ones that differ are exactly the offline ones;
  * every inference_log row is a failure, because "the backend is down" must
    also mean the audit trail shows it.

The proxy half asserts SCREENSHOT_HEALTH_STATUS can put /api/health into the
503/degraded branch api_server.py uses (health_summary(), "status" != "ok"),
while still reporting `"fixture": true` — a11y_audit.js refuses to run against
an origin that does not.
"""
import importlib.util
import json
import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = os.path.join(HERE, "tools", "screenshots", "proxy.py")
FIXTURES = os.path.join(HERE, "tools", "screenshots", "fixtures")
ONLINE = os.path.join(FIXTURES, "api")
OFFLINE = os.path.join(FIXTURES, "api-offline")

# Every file both fixture dirs must serve: these are the files
# tools/screenshots/proxy.py's _stub_api reads out of SCREENSHOT_API_DIR.
FIXTURE_FILES = (
    "inference_log.json",
    "integrations.json",
    "llm-schema.json",
    "settings.json",
    "status.json",
)

# The only status.json values a stopped Ollama is allowed to change. A new key
# here means a new *behaviour* difference between the fixtures, which has to be
# deliberate — the recursive key-set assertion below still pins the shape.
OFFLINE_VALUES = {
    ("llm", "model"): None,
    ("llm", "reachable"): False,
    ("inference",): {},
    ("queue", "unanalyzed"): 4,
    ("queue", "unverified_partials"): 6,
    ("queue", "awaiting_backfill"): 6,
    ("queue", "llm_verified"): 0,
    ("queue", "deep_s_per_frame"): None,
    ("queue", "deep_eta_s"): None,
    # The same three failed runs the offline inference_log carries.
    ("metrics", "count"): 3,
    ("metrics", "ok"): 0,
    ("metrics", "failures"): 3,
    ("metrics", "success_rate"): 0.0,
    ("metrics", "local"): 3,
    ("metrics", "avg_s"): 0.0,
    ("metrics", "p95_s"): 0.0,
}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_proxy(env):
    """Fresh proxy module under `env` — it reads its config at import time."""
    saved = os.environ.copy()
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location("webcam_proxy_offline_120", PROXY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.environ.clear()
        os.environ.update(saved)


class TestOfflineFixture(unittest.TestCase):
    def test_both_fixture_dirs_serve_the_same_files(self):
        for name in FIXTURE_FILES:
            for d in (ONLINE, OFFLINE):
                self.assertTrue(
                    os.path.isfile(os.path.join(d, name)),
                    f"{d} is missing {name}, so SCREENSHOT_API_DIR cannot be swapped",
                )
        self.assertEqual(
            sorted(os.listdir(ONLINE)),
            sorted(os.listdir(OFFLINE)),
            "the offline fixture dir must hold exactly the online fixture's files",
        )

    def test_status_key_sets_match_recursively(self):
        online = load_json(os.path.join(ONLINE, "status.json"))
        offline = load_json(os.path.join(OFFLINE, "status.json"))
        diffs = []
        _walk_keys(online, offline, (), diffs)
        self.assertEqual(diffs, [], "status.json shape drifted:\n  " + "\n  ".join(diffs))

    def test_status_differs_only_where_ollama_is_down(self):
        online = load_json(os.path.join(ONLINE, "status.json"))
        offline = load_json(os.path.join(OFFLINE, "status.json"))
        diffs = []
        _walk_values(online, offline, (), diffs)
        self.assertEqual(diffs, [], "status.json values drifted:\n  " + "\n  ".join(diffs))

    def test_offline_status_models_a_stopped_ollama(self):
        offline = load_json(os.path.join(OFFLINE, "status.json"))
        online = load_json(os.path.join(ONLINE, "status.json"))
        self.assertIs(offline["llm"]["reachable"], False)
        self.assertIs(online["llm"]["reachable"], True)
        self.assertIsNone(offline["llm"]["model"])
        self.assertEqual(offline["inference"], {})
        self.assertEqual(offline["settings"]["model_primary"], online["settings"]["model_primary"])
        q = offline["queue"]
        self.assertEqual(q["deep_s_per_frame"], None)
        self.assertEqual(q["deep_eta_s"], None)
        # A queue that only ever grows while the backend is down.
        self.assertEqual(
            (q["unanalyzed"], q["unverified_partials"], q["awaiting_backfill"], q["llm_verified"]),
            (4, 6, 6, 0),
        )

    def test_offline_inference_log_is_all_failures(self):
        online = load_json(os.path.join(ONLINE, "inference_log.json"))
        offline = load_json(os.path.join(OFFLINE, "inference_log.json"))
        self.assertEqual(len(offline), 3)
        self.assertEqual(len(online), len(offline) - 1)
        for row in offline:
            self.assertIs(row["ok"], False, f"offline log row must be a failure: {row}")
            self.assertTrue(row.get("error"), f"a failure must say why: {row}")
            # Same keys as the online rows: no invented fields.
            self.assertEqual(
                sorted(row), sorted(sorted(online[0]) + ["error"]),
                f"unexpected log shape: {row}",
            )

    def test_other_fixture_payloads_are_byte_identical(self):
        # settings / integrations / llm-schema have nothing to do with whether
        # Ollama answers; a diff there is a copy-paste accident.
        for name in ("settings.json", "integrations.json", "llm-schema.json"):
            with open(os.path.join(ONLINE, name), "rb") as f:
                a = f.read()
            with open(os.path.join(OFFLINE, name), "rb") as f:
                b = f.read()
            self.assertEqual(a, b, f"{name} must be copied unchanged into the offline fixture")


class TestProxyHealthStatus(unittest.TestCase):
    """SCREENSHOT_HEALTH_STATUS drives the degraded /api/health branch."""

    def test_default_health_is_ok(self):
        p = load_proxy({})
        self.assertEqual(p.HEALTH_STATUS, "200")

    def test_503_is_honoured_and_garbage_falls_back_to_200(self):
        for value, want in (
            ("503", "503"),
            ("200", "200"),
            ("", "200"),
            ("503 ", "503"),
            ("degraded", "200"),
            ("500", "200"),
        ):
            with self.subTest(SCREENSHOT_HEALTH_STATUS=value):
                p = load_proxy({"SCREENSHOT_HEALTH_STATUS": value})
                self.assertEqual(p.HEALTH_STATUS, want)

    def test_degraded_body_mirrors_health_summary(self):
        p = load_proxy({"SCREENSHOT_HEALTH_STATUS": "503"})
        self.assertEqual(
            p.HEALTH_DEGRADED,
            {
                "status": "degraded",
                "fixture": True,
                "checks": {
                    "inotify": True,
                    "llm_reachable": False,
                    "recent_sweep": True,
                    "disk_space": True,
                },
                "cameras": [],
            },
        )
        # a11y_audit.js refuses an origin whose health does not say fixture.
        self.assertIs(p.HEALTH_DEGRADED["fixture"], True)
        self.assertEqual(p.HEALTH_DEGRADED["status"], "degraded")


def _walk_keys(a, b, path, diffs):
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                diffs.append(f"{'.'.join(path + (key,))}: only in offline")
            elif key not in b:
                diffs.append(f"{'.'.join(path + (key,))}: only in online")
            else:
                _walk_keys(a[key], b[key], path + (key,), diffs)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{'.'.join(path)}: list lengths {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _walk_keys(x, y, path + (str(i),), diffs)


def _walk_values(a, b, path, diffs):
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key in a and key in b:
                _walk_values(a[key], b[key], path + (key,), diffs)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{'.'.join(path)}: list lengths {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _walk_values(x, y, path + (str(i),), diffs)
        return
    if a != b and path not in OFFLINE_VALUES:
        diffs.append(f"{'.'.join(path)}: {a!r} != {b!r}")


if __name__ == "__main__":
    unittest.main()
