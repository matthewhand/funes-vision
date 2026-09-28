"""One pins file, read the same way by the writer and by retention (#21).

Before this change the API wrote a pin to `<camera_dir>/pins.json`
(api_server._pins_path) while `analyze_images.main()` read only
`<repo>/pins.json`, so every retention path — the hourly sweep, the watchdog's
--retention-only, create-index.sh — gated on an empty pin set and deleted the
frames the user had pinned. The repo-root copy was also pushed back over each
camera's file by the sync steps, undoing the pin entirely.

The contract these tests pin down:

* `<camera_dir>/pins.json` is canonical — it is the file the API writes and the
  one the web root serves, so it is what retention must read.
* `<repo>/pins.json` (pre-camera-scoping) is still honored, read-only, so an
  upgrade does not start deleting old pins.
* A pin request on one camera never writes another camera's pins into it.
* No sync step writes pins.json at all.

Each test runs the real `analyze_images.main(retention_only=True, ...)` on a
throwaway tree rather than poking `apply_retention` directly: the bug lived in
which file main() chose, and only main() can catch a regression there.

Two machine-global knobs are pinned in setUp so the result is the same on a
deployed box as in CI, where this suite used to go red (401s from a real
api_token) or pass for the wrong reason (a busy global pipeline lock turning
the sweep into a no-op) -- see tests/hermetic.py.
"""
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hermetic

import analyze_images
import api_server

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeHandler:
    """Drive api_server.Handler.do_POST / do_GET with captured output."""

    def __init__(self):
        self.sent = []
        self.handler = api_server.Handler.__new__(api_server.Handler)

    def _send(self, code, body):
        self.sent.append((code, body))

    def run(self, method, path, body=b""):
        h = self.handler
        h._send = self._send
        h.path = path
        # do_POST sizes its body read off Content-Length, so a missing header
        # would dispatch on an empty payload instead of the pin request.
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h.wfile = io.BytesIO()
        if method == "GET":
            h.do_GET()
        else:
            h.do_POST()
        return self.sent[-1] if self.sent else (None, None)


class PinsContractTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="webcam_pins_")
        self.root = self.td.name
        # The pipeline's BASE_DIR, separate from the camera dirs: the legacy
        # repo-root pins.json lives here and the analyzer's settings/analysis
        # catalogs are read from here.
        self.repo = os.path.join(self.root, "repo")
        self.cams = {}
        for name in ("camA", "camB"):
            d = os.path.join(self.root, name)
            os.makedirs(os.path.join(d, "thumbs"))
            self.cams[name] = d
        os.makedirs(self.repo)
        self.settings_path = os.path.join(self.repo, "settings.json")

        self._saved = {n: getattr(analyze_images, n) for n in (
            "BASE_DIR", "WATCH_DIRS", "MAX_AGE_DAYS", "MAX_DIR_GB",
            "PERSIST_BUDGET_PCT", "RETENTION_LOG", "GATE_IGNORE_LABELS")}
        self._saved_api = {n: getattr(api_server, n) for n in
                           ("WATCH_DIRS", "PINS_FILE", "BASE_DIR")}
        analyze_images.BASE_DIR = self.repo
        analyze_images.WATCH_DIRS = list(self.cams.values())
        analyze_images.RETENTION_LOG = os.path.join(self.repo, "retention_log.json")
        analyze_images.PERSIST_BUDGET_PCT = 20.0
        analyze_images.GATE_IGNORE_LABELS = ["car"]
        # main() takes the machine-global /tmp/webcam_analysis.lock
        # non-blocking and returns a benign exit 0 when it is busy, so a real
        # sweep on this box would turn every _sweep() below into a silent
        # no-op -- the frame would survive for the wrong reason (#30/#53).
        hermetic.pin_private_lock(self, analyze_images, self.root)
        api_server.WATCH_DIRS = list(self.cams.values())
        api_server.BASE_DIR = self.repo
        api_server.PINS_FILE = os.path.join(self.repo, "pins.json")
        # api_token() falls back to the settings.json next to the script, so a
        # deployed one carrying a real api_token made every _pin() below return
        # 401 instead of 200. Same guard as #39's fix in test_api_cameras.py.
        hermetic.pin_api_auth_off(self, api_server, self.root)
        patcher = mock.patch.object(analyze_images, "ollama_available",
                                    return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._restore)
        self.h = FakeHandler()

    def _restore(self):
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        for name, value in self._saved_api.items():
            setattr(api_server, name, value)
        self.td.cleanup()

    # --- fixtures ---------------------------------------------------------
    def _frame(self, cam, name, age_days=3, thumb=True):
        d = self.cams[cam]
        for target in (os.path.join(d, name),):
            with open(target, "wb") as f:
                f.write(b"x" * 200)
            stamp = time.time() - age_days * 86400
            os.utime(target, (stamp, stamp))
        if thumb:
            with open(os.path.join(d, "thumbs", name), "w") as f:
                f.write("t")
        return os.path.join(d, name)

    def _analysis(self, names):
        """The repo-root analysis.json the pipeline maintains (all cameras)."""
        with open(os.path.join(self.repo, "analysis.json"), "w") as f:
            json.dump({n: {"fast_pass": "negative"} for n in names}, f)

    def _legacy_pins(self, names):
        with open(os.path.join(self.repo, "pins.json"), "w") as f:
            json.dump(names, f)

    def _cam_pins(self, cam):
        path = os.path.join(self.cams[cam], "pins.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def _pin(self, cam, name, pinned=True):
        return self.h.run("POST", f"/api/pin?camera={cam}",
                          json.dumps({"filename": name,
                                      "pinned": pinned}).encode())

    def _sweep(self, **cfg):
        cfg = {"watch_dirs": list(self.cams.values()),
               "max_age_days": 1, "max_dir_gb": 100, **cfg}
        with open(self.settings_path, "w") as f:
            json.dump(cfg, f)
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=self.settings_path)
        # A skipped sweep still exits 0, so without this a frame surviving the
        # sweep would be evidence of nothing at all.
        hermetic.assert_sweep_ran(self, out.getvalue())

    # --- the defect -------------------------------------------------------
    def test_api_pin_survives_a_retention_sweep(self):
        """The headline repro: POST /api/pin returns 200, then the frame and
        its thumb are still there after a 1-day-age sweep."""
        self._frame("camA", "pin_me.jpg")
        self._frame("camA", "old_empty.jpg")
        self._analysis(["pin_me.jpg", "old_empty.jpg"])
        assert not os.path.exists(os.path.join(self.repo, "pins.json")), \
            "the API must not write a repo-root pins.json any more"

        code, body = self._pin("camA", "pin_me.jpg")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"ok": True, "pinned": True})
        self._sweep()

        self.assertTrue(os.path.exists(os.path.join(self.cams["camA"], "pin_me.jpg")),
                        "retention deleted a frame the user pinned through the API")
        self.assertTrue(os.path.exists(
            os.path.join(self.cams["camA"], "thumbs", "pin_me.jpg")))
        # The sweep still has to do its job around the pin.
        self.assertFalse(os.path.exists(os.path.join(self.cams["camA"], "old_empty.jpg")))

    def test_api_pin_survives_the_budget_pass_too(self):
        """Pins must also hold when the disk budget is what forces deletion,
        not just the age cutoff — the budget pass is the one that runs when a
        camera is over max_dir_gb mid-afternoon."""
        self._frame("camA", "keep.jpg", age_days=1000)
        self._frame("camA", "drop.jpg", age_days=1000)
        self._analysis(["keep.jpg", "drop.jpg"])
        self._pin("camA", "keep.jpg")

        # 300 bytes of frames against a 250-byte budget: one must go, and it
        # must not be the pin.
        self._sweep(max_age_days=3650, max_dir_gb=250 / (1024 ** 3))

        self.assertTrue(os.path.exists(os.path.join(self.cams["camA"], "keep.jpg")),
                        "the budget pass deleted a pinned frame")
        self.assertFalse(os.path.exists(os.path.join(self.cams["camA"], "drop.jpg")))

    def test_repo_root_pins_still_protect(self):
        """A pre-camera-scoping pin in <repo>/pins.json keeps its frame, and
        the sweep does not migrate it into a camera's file."""
        self._frame("camA", "legacy.jpg")
        self._frame("camA", "old_empty.jpg")
        self._analysis(["legacy.jpg", "old_empty.jpg"])
        self._legacy_pins(["legacy.jpg"])

        self._sweep()

        self.assertTrue(os.path.exists(os.path.join(self.cams["camA"], "legacy.jpg")),
                        "the legacy repo-root pin stopped protecting its frame")
        self.assertIsNone(self._cam_pins("camA"),
                          "the sweep materialized legacy pins into a camera's file")
        self.assertFalse(os.path.exists(os.path.join(self.cams["camA"], "old_empty.jpg")))

    def test_legacy_and_camera_pins_union_for_retention(self):
        """Both sources apply at once, per camera: a legacy pin protects a
        frame in either camera while a camera pin protects its own."""
        self._frame("camA", "legacy_a.jpg")
        self._frame("camA", "own_a.jpg")
        self._frame("camB", "legacy_b.jpg")
        self._frame("camB", "gone_b.jpg")
        self._analysis(["legacy_a.jpg", "own_a.jpg", "legacy_b.jpg", "gone_b.jpg"])
        self._legacy_pins(["legacy_a.jpg", "legacy_b.jpg"])
        self._pin("camA", "own_a.jpg")

        self._sweep()

        for cam, name in (("camA", "legacy_a.jpg"), ("camA", "own_a.jpg"),
                          ("camB", "legacy_b.jpg")):
            self.assertTrue(
                os.path.exists(os.path.join(self.cams[cam], name)),
                f"{cam}/{name} was pinned and retention deleted it")
        self.assertFalse(os.path.exists(os.path.join(self.cams["camB"], "gone_b.jpg")))

    # --- the cross-camera leak -------------------------------------------
    def test_pin_on_camera_b_does_not_import_camera_a_pins(self):
        self._frame("camA", "a_only.jpg")
        self._frame("camB", "b_only.jpg")
        self._pin("camA", "a_only.jpg")
        self.assertEqual(self._cam_pins("camA"), ["a_only.jpg"])

        self._pin("camB", "b_only.jpg")

        self.assertEqual(self._cam_pins("camB"), ["b_only.jpg"],
                         "camera B's pins.json picked up camera A's pins")
        code, body = self.h.run("GET", "/api/pins?camera=camB")
        self.assertEqual(code, 200)
        self.assertEqual(body, ["b_only.jpg"])

    def test_pin_on_b_does_not_materialize_legacy_pins(self):
        """The legacy fallback is read-only. A pin request on B must not copy
        the shared repo-root set into B's world-readable file — that is how A's
        pins used to show up in B's gallery."""
        self._frame("camB", "b_only.jpg")
        self._legacy_pins(["a_legacy.jpg", "another_legacy.jpg"])

        self._pin("camB", "b_only.jpg")

        self.assertEqual(self._cam_pins("camB"), ["b_only.jpg"])

    def test_unpin_of_a_legacy_pin_does_not_create_a_camera_file(self):
        """Unpinning a frame that only exists in the legacy file edits that one
        file and leaves the camera's own pins.json absent, so unpinning stays
        possible after the upgrade without importing anything."""
        self._frame("camB", "legacy_only.jpg")
        self._legacy_pins(["legacy_only.jpg", "keep_me.jpg"])

        code, body = self._pin("camB", "legacy_only.jpg", pinned=False)

        self.assertEqual(code, 200)
        self.assertFalse(body["pinned"])
        self.assertIsNone(self._cam_pins("camB"))
        with open(os.path.join(self.repo, "pins.json")) as f:
            self.assertEqual(json.load(f), ["keep_me.jpg"],
                             "unpin removed the wrong legacy entries")

    def test_unpin_of_a_camera_pin_keeps_the_legacy_file_untouched(self):
        self._frame("camA", "own.jpg")
        self._legacy_pins(["legacy.jpg"])
        self._pin("camA", "own.jpg")

        self._pin("camA", "own.jpg", pinned=False)

        self.assertEqual(self._cam_pins("camA"), [])
        with open(os.path.join(self.repo, "pins.json")) as f:
            self.assertEqual(json.load(f), ["legacy.jpg"])

    # --- #59: a pin in BOTH files could never be unpinned -------------------
    def test_unpin_clears_the_name_from_both_files(self):
        """The un-unpinnable pin (#59).

        `retention_pins()` unions `<cam>/pins.json` and the legacy
        `<repo>/pins.json`, but `set_pin(pinned=False)` returned as soon as the
        camera file was cleaned. So a name present in BOTH -- which is what
        every upgraded install has, since both pre-#39 sync steps wrote the
        repo file into every camera dir -- reported `pinned: false`, showed
        unpinned in the UI, and still protected the frame from every
        retention sweep forever."""
        self._frame("camA", "stuck.jpg", age_days=90)
        self._frame("camA", "old_empty.jpg", age_days=90)
        self._analysis(["stuck.jpg", "old_empty.jpg"])
        self._legacy_pins(["stuck.jpg"])
        with open(os.path.join(self.cams["camA"], "pins.json"), "w") as f:
            json.dump(["stuck.jpg"], f)  # the overlap, as an upgrade leaves it

        # It really is pinned, through either file alone.
        self._sweep()
        self.assertTrue(os.path.exists(os.path.join(self.cams["camA"], "stuck.jpg")))

        code, body = self._pin("camA", "stuck.jpg", pinned=False)
        self.assertEqual(code, 200)
        self.assertFalse(body["pinned"])
        self.assertEqual(self._cam_pins("camA"), [])
        self._legacy_pins([])
        code, body = self.h.run("GET", "/api/pins?camera=camA")
        self.assertEqual(code, 200)
        self.assertNotIn("stuck.jpg", body, "/api/pins still reports it pinned")

        # And the point of the whole thing: a sweep now really deletes it.
        self._sweep()
        self.assertFalse(os.path.exists(os.path.join(self.cams["camA"], "stuck.jpg")),
                         "the frame the operator released survived the sweep")
        self.assertFalse(
            os.path.exists(os.path.join(self.cams["camA"], "thumbs", "stuck.jpg")))

    def test_unpin_is_idempotent_across_the_union(self):
        """Unpinning twice, and unpinping a name that is in neither file,
        must both be quiet no-ops -- an unpin the API refuses to repeat is the
        same bug from the other side."""
        self._frame("camA", "both.jpg")
        self._legacy_pins(["both.jpg"])
        with open(os.path.join(self.cams["camA"], "pins.json"), "w") as f:
            json.dump(["both.jpg"], f)

        for _ in range(3):
            code, body = self._pin("camA", "both.jpg", pinned=False)
            self.assertEqual(code, 200)
            self.assertFalse(body["pinned"])
        code, body = self._pin("camA", "never_pinned.jpg", pinned=False)
        self.assertEqual(code, 404)  # no such frame: nothing to unpin
        self.assertEqual(self._cam_pins("camA"), [])
        with open(os.path.join(self.repo, "pins.json")) as f:
            self.assertEqual(json.load(f), [])

    def test_pins_json_does_not_grow_without_bound(self):
        """pins.json only ever grew: retention and /api/delete removed frames
        and neither rewrote the pin list, so the file accumulated every frame
        the camera had ever held and /api/pins reported names with nothing
        behind them. Names whose frame is gone are dropped on the next write
        to that camera -- retention has nothing to protect for them either."""
        self._frame("camA", "here.jpg")
        self._frame("camA", "also_here.jpg")
        with open(os.path.join(self.cams["camA"], "pins.json"), "w") as f:
            json.dump(["gone_1.jpg", "gone_2.jpg"], f)
        self._legacy_pins([])

        # Any write to this camera's pins reclaims them.
        self._pin("camA", "here.jpg")
        self._pin("camA", "also_here.jpg")
        self.assertEqual(self._cam_pins("camA"), ["also_here.jpg", "here.jpg"])
        self.assertTrue(os.path.exists(os.path.join(self.cams["camA"], "here.jpg")))
        self.assertTrue(
            os.path.exists(os.path.join(self.cams["camA"], "also_here.jpg")))

    def test_pins_gc_drops_nothing_when_it_cannot_see_the_camera_dir(self):
        """The one mistake that would lose data the operator asked to keep is
        dropping a *live* pin, so an unreadable or empty listing must drop
        nothing at all -- a vanished dir, a permissions problem or a
        filesystem hiccup must never wipe the pin list."""
        empty = os.path.join(self.root, "emptyCam")
        os.makedirs(empty)
        api_server.WATCH_DIRS = [empty]
        try:
            self.assertEqual(api_server._gc_pins({"a.jpg"}, "emptyCam"), {"a.jpg"})
        finally:
            api_server.WATCH_DIRS = list(self.cams.values())

        # And an unreadable dir (OSError) is equally not evidence of absence.
        with mock.patch("os.listdir", side_effect=OSError("EIO")):
            self.assertEqual(
                api_server._gc_pins({"a.jpg"}, "camA"), {"a.jpg"})

    def test_pins_gc_keeps_everything_a_populated_dir_still_has(self):
        self._frame("camA", "live.jpg")
        self.assertEqual(
            api_server._gc_pins({"live.jpg", "dead.jpg"}, "camA"), {"live.jpg"})

    def test_load_pins_reports_legacy_pins_for_the_ui(self):
        """Read paths keep seeing the legacy set, otherwise an upgraded
        install would show pinned frames as unpinned and re-pin them."""
        self._frame("camB", "own.jpg")
        self._legacy_pins(["legacy.jpg"])
        self._pin("camB", "own.jpg")

        code, body = self.h.run("GET", "/api/pins?camera=camB")
        self.assertEqual(code, 200)
        self.assertEqual(body, ["legacy.jpg", "own.jpg"])
        code, body = self.h.run("GET", "/api/pins")
        self.assertEqual(sorted(body), ["legacy.jpg", "own.jpg"])

    # --- the sync steps ---------------------------------------------------
    def test_no_sync_step_writes_pins_json(self):
        """watchdog.sh and create-index.sh pushed the repo-root pins.json over
        each camera's file, undoing the pin. Pins are written by the API alone,
        so no sync step may name pins.json as a destination."""
        offenders = []
        for rel in ("tools/watchdog.sh", "create-index.sh"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                for n, line in enumerate(f, 1):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if "pins.json" not in line:
                        continue
                    if re.search(r"(^|[\s|&;])(cp|mv|install|tee)(\s|$)|>|copy", line):
                        offenders.append(f"{rel}:{n}: {stripped}")
        self.assertEqual(offenders, [],
                         "a sync step writes pins.json again:\n" + "\n".join(offenders))


class RetentionPinsUnitTest(unittest.TestCase):
    """retention_pins() itself, including the shapes a hand-edited file takes."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.img = self.td.name
        self.addCleanup(self.td.cleanup)

    def _write(self, raw):
        with open(os.path.join(self.img, "pins.json"), "w") as f:
            f.write(raw)

    def test_missing_file_is_empty(self):
        self.assertEqual(analyze_images.retention_pins(self.img), set())

    def test_legacy_union(self):
        self._write('["a.jpg"]')
        self.assertEqual(analyze_images.retention_pins(self.img, {"l.jpg"}),
                         {"a.jpg", "l.jpg"})

    def test_corrupt_file_does_not_raise(self):
        # A truncated pins.json must not abort the sweep before retention runs.
        self._write('["a.jpg"')
        self.assertEqual(analyze_images.retention_pins(self.img), set())

    def test_non_list_file_is_ignored(self):
        self._write('{"a.jpg": true}')
        self.assertEqual(analyze_images.retention_pins(self.img), set())


if __name__ == "__main__":
    unittest.main()
