"""#27: create-index.sh must not stamp the success marker on a refused sweep.

`/tmp/webcam_analysis.lastrun` is the whole visible contract between the
pipeline and the watchdog: `/api/health`'s `recent_sweep` and
`tools/watchdog.sh`'s `sweep_age`/`ret_age` heuristics are derived from it. So
a marker written after a sweep that did nothing is not a cosmetic bug -- it is
how a box with dead retention kept reading as healthy while the disk grew
without bound (see the sibling analyzer test for the exit-code half).

create-index.sh already gates the `touch` on the analyzer's exit status; the
defect was upstream, in the analyzer never producing a non-zero status. These
tests drive the real script in a sandbox to keep that gate load-bearing:

* the script, the analyzer and its local modules are copied into a temp dir, so
  a corrupt settings.json there is the one the copied analyzer reads
  (`_settings_path` is derived from the module's own directory) and the repo's
  real settings.json is out of reach;
* `WEBCAM_LOCK`/`WEBCAM_MARKER` are overridden so neither the global analysis
  lock nor the real /tmp marker is touched, and a 45s debounce cannot mask the
  result;
* `inotifywait` (not installed everywhere, and a blocking tail in production)
  is stubbed, so the script's foreground loop ends and the backgrounded initial
  sweep is the only run;
* a valid settings.json points at an empty camera dir, so the real sweep has
  nothing to analyse and finishes in a second.

Run from the repo root:  python3 -m unittest discover -s tests
"""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREATE_INDEX = os.path.join(REPO_ROOT, "create-index.sh")

# The analyzer and everything it imports at module level, so the copied
# analyze_images.py is importable with nothing from the repo on sys.path.
MODULES = ("analyze_images.py", "log_config.py", "scans.py", "taxonomy.py",
           "catalog.py", "zones.py", "pipeline_events.py", "ha_mqtt.py",
           "create-index.sh", "index.html", "manifest.json", "icon.svg",
           "favicon.ico", "lucide.min.js")

# Half-written: the process was killed (or the disk filled) between the two
# writes, leaving a valid prefix and no closing bracket.
CORRUPT = '{"watch_dirs": ["/mnt/models/Webcam21"'
# The exit code analyze_images.EXIT_SETTINGS_ERROR uses for this fault.
EXIT_SETTINGS_ERROR = 3


class CreateIndexMarkerGateTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="webcam_create_index_")
        self.base = self.td.name
        self.bin = os.path.join(self.base, "bin")
        self.cam = os.path.join(self.base, "cam")
        self.marker = os.path.join(self.base, "lastrun")
        self.lock = os.path.join(self.base, "analysis.lock")
        self.out = os.path.join(self.base, "stdout.log")
        os.makedirs(self.bin)
        os.makedirs(self.cam)
        for name in MODULES:
            src = os.path.join(REPO_ROOT, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(self.base, name))
        self.settings = os.path.join(self.base, "settings.json")

        # A no-op stand-in: the real one tails the camera dir forever, which
        # would hang the test after the backgrounded run finished.
        stub = os.path.join(self.bin, "inotifywait")
        with open(stub, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(stub, 0o755)
        self.addCleanup(self.td.cleanup)

    def _write_settings(self, text):
        with open(self.settings, "w") as f:
            f.write(text)

    def _write_valid_settings(self):
        self._write_settings(json.dumps({"watch_dirs": [self.cam],
                                         "idle_sweep_seconds": 3600}))

    def _start(self, timeout=180):
        env = dict(os.environ)
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env["WEBCAM_LOCK"] = self.lock
        env["WEBCAM_MARKER"] = self.marker
        # Output goes to a file, never a pipe: the script backgrounds
        # `idle_sweep &`, which inherits the pipe and would hold it open long
        # after the foreground inotifywait loop ended.
        log = open(self.out, "a")
        # Start the script in its own process group so the idle_sweep loop and
        # anything it spawned die with the test, whatever happens below.
        proc = subprocess.Popen(["bash", os.path.join(self.base, "create-index.sh"),
                                 self.cam], env=env, cwd=self.base,
                                stdout=log, stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)
        log.close()
        self.addCleanup(self._reap, proc)
        return proc

    def _run(self, timeout=180):
        proc = self._start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.fail(f"create-index.sh did not finish\n{self._log()}")
        return self._log()

    def _reap(self, proc):
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=30)

    def _log(self):
        try:
            with open(self.out) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def _wait_for(self, needle, timeout=90):
        self._start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            out = self._log()
            if needle in out:
                return out
            time.sleep(0.1)
        self.fail(f"never saw {needle!r} in create-index.sh output:\n{self._log()}\n"
                  f"marker={os.path.exists(self.marker)}")

    def test_corrupt_settings_leaves_the_success_marker_unstamped(self):
        self._write_settings(CORRUPT)

        out = self._wait_for("Analysis FAILED")

        self.assertIn(f"exit {EXIT_SETTINGS_ERROR}", out,
                      f"expected the settings exit code in:\n{out}")
        self.assertNotIn("Analysis and sync complete", out)
        self.assertIn("lastrun not advanced", out)
        # The load-bearing assertion: no successful sweep was reported.
        self.assertFalse(os.path.exists(self.marker),
                         "create-index.sh stamped the success marker after a "
                         "refused sweep -- the watchdog would read this box as "
                         "healthy with retention dead")
        # And the fault is visible in the pipeline's own log, not just the
        # marker: someone reading `journalctl -u webcam-pipeline` sees why.
        self.assertIn("ERROR", out)
        self.assertIn("settings.json", out)

    def test_successful_sweep_does_stamp_the_marker(self):
        """The gate must not become skip-everything: a configured, successful
        run still advances the marker, or every healthy box looks stale."""
        self._write_valid_settings()

        out = self._wait_for("Analysis and sync complete")

        self.assertNotIn("Analysis FAILED", out)
        self.assertTrue(os.path.exists(self.marker),
                        "a successful sweep did not advance the success marker")

    def test_missing_settings_file_still_stamps_the_marker(self):
        """A fresh install has no settings.json yet. That is a benign exit 0
        (nothing configured), and must not look like a pipeline failure."""
        self.assertFalse(os.path.exists(self.settings))

        out = self._wait_for("Analysis and sync complete")

        self.assertNotIn("Analysis FAILED", out)
        self.assertTrue(os.path.exists(self.marker))

    def test_marker_is_not_touched_by_the_debounce_exit(self):
        """Sanity on the gate itself: a fresh run with a marker already touched
        is a debounce skip, which must neither re-stamp nor claim success."""
        self._write_valid_settings()
        with open(self.marker, "w") as f:
            f.write("")
        before = os.stat(self.marker).st_mtime_ns

        self._run()

        self.assertEqual(os.stat(self.marker).st_mtime_ns, before)
        self.assertNotIn("Analysis and sync complete", self._log())


if __name__ == "__main__":
    unittest.main()
