"""The watchdog's retention pass must never kill a running sweep (#31).

`tools/watchdog.sh` runs `analyze_images.py --retention-only` from
systemd/webcam-watchdog.cron at :05 every hour. A live sweep holds the global
analysis lock for hours (MAX_DEEP_PASSES deep passes at ~40s each), so the lock
being busy at :05 is the normal case, not a fault. The script used to answer
that with `pkill -f analyze_images.py` plus `fuser -k "$LOCK"`, so the watchdog
SIGKILLed the in-flight sweep once an hour — discarding a vision call and
resetting the deep-pass budget every time.

These tests drive the real script in a sandbox:

* `watchdog.sh` derives BASE from its own location, so the script and a stub
  `analyze_images.py` are copied into a temp dir. That keeps the real analyzer
  (and any real watch dir) out of reach even if the test is wrong about the
  lock being held.
* `systemctl`/`sudo`/`logger`/`pkill`/`fuser` are stubbed on PATH, so nothing
  on the test machine is restarted and a kill attempt is *recorded* rather than
  only inferred from a process's death.
"""
import os
import shutil
import signal
import subprocess
import tempfile
import textwrap
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCHDOG = os.path.join(REPO_ROOT, "tools", "watchdog.sh")

# Marks a kill attempt instead of performing one, so "did the watchdog try to
# kill anything?" is answerable even when the victim's death is ambiguous.
KILL_MARKER = "kill_attempted"


def _stub(name, body):
    return textwrap.dedent(body).lstrip()


class WatchdogLockTestBase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="webcam_watchdog_")
        self.base = self.td.name
        self.bin = os.path.join(self.base, "bin")
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.base, "tools"))
        os.makedirs(os.path.join(self.base, "cam"))
        shutil.copy2(WATCHDOG, os.path.join(self.base, "tools", "watchdog.sh"))
        self.addCleanup(self.td.cleanup)

        self.lock = os.path.join(self.base, "analysis.lock")
        self.log = os.path.join(self.base, "watchdog.log")
        self.marker = os.path.join(self.base, "lastrun")
        self.kill_marker = os.path.join(self.base, KILL_MARKER)
        self.analyzer_marker = os.path.join(self.base, "analyzer_ran")

        # `systemctl is-active` must answer "active" so ensure_units() is a
        # no-op; the rest are inert.
        for name, body in (
            ("systemctl", "echo active"),
            ("sudo", "exit 0"),
            ("logger", "exit 0"),
            ("pkill", f'touch "{self.kill_marker}"\nexit 0'),
            ("fuser", f'touch "{self.kill_marker}"\nexit 0'),
        ):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(f"#!/bin/sh\n{_stub(name, body)}\n")
            os.chmod(path, 0o755)

        # Stand-in for the analyzer. Touches a marker (so "retention ran" is
        # observable) then exits 0 — a successful, instant sweep.
        with open(os.path.join(self.base, "analyze_images.py"), "w") as f:
            f.write(textwrap.dedent(f"""\
                import os, sys
                open({self.analyzer_marker!r}, "w").close()
                if "--retention-only" not in sys.argv:
                    raise SystemExit("expected --retention-only")
                """))
        with open(os.path.join(self.base, "settings.json"), "w") as f:
            f.write('{"watch_dirs": ["%s"]}\n' % os.path.join(self.base, "cam"))
        # Repo-root catalogs for sync_web_roots to push into the web root.
        with open(os.path.join(self.base, "analysis.json"), "w") as f:
            f.write('{"frame.jpg": {"person": true}}')
        with open(os.path.join(self.base, "bursts.json"), "w") as f:
            f.write('{"b1": {"summary": "x"}}')

    def _env(self):
        env = dict(os.environ)
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env.update(WATCHDOG_LOCK=self.lock, WATCHDOG_LOG=self.log,
                   WATCHDOG_MARKER=self.marker, WEBCAM_BASE=self.base)
        return env

    def _run(self, mode="retention", timeout=60):
        return subprocess.run(["bash", os.path.join(self.base, "tools", "watchdog.sh"),
                               mode], env=self._env(), capture_output=True,
                              text=True, timeout=timeout)

    def _hold_lock(self, seconds=120):
        """Start a live sweep that holds the global lock, as create-index does.

        Returns the Popen. It writes its pid to analyzer_pid so the test can
        check the exact process, and it installs no handler for SIGTERM, so a
        kill would end it immediately.
        """
        script = textwrap.dedent("""\
            import fcntl, os, sys, time
            f = open(sys.argv[1], "a+")
            fcntl.flock(f, fcntl.LOCK_EX)
            open(sys.argv[2], "w").write(str(os.getpid()))
            time.sleep(float(sys.argv[3]))
            """)
        proc = subprocess.Popen(
            ["python3", "-c", script, self.lock,
             os.path.join(self.base, "holder_pid"), str(seconds)],
            env=self._env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid_file = os.path.join(self.base, "holder_pid")
        deadline = time.time() + 20
        while not os.path.exists(pid_file) and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(pid_file),
                        "lock holder never started")
        self.addCleanup(self._stop, proc)
        return proc

    def _stop(self, proc):
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=20)

    def _holder_pid(self):
        with open(os.path.join(self.base, "holder_pid")) as f:
            return int(f.read().strip())

    def _alive(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _log(self):
        with open(self.log) as f:
            return f.read()


class TestRetentionDoesNotKillSweep(WatchdogLockTestBase):
    def test_live_sweep_holding_the_lock_is_left_running(self):
        proc = self._hold_lock()

        result = self._run("retention")

        self.assertEqual(result.returncode, 0,
                         f"a busy lock must be a clean skip, got rc={result.returncode}\n"
                         f"{result.stdout}\n{result.stderr}")
        pid = self._holder_pid()
        self.assertTrue(self._alive(pid),
                        "the watchdog killed the sweep that held the lock")
        self.assertIsNone(proc.poll(),
                          "the analyzer process exited during the watchdog run")
        self.assertFalse(os.path.exists(self.kill_marker),
                         "the watchdog ran pkill/fuser against a live sweep")
        self.assertIn("lock busy", self._log())
        self.assertIn("skipping this cycle", self._log())
        # Skipped, not silently failed: no retention ran and no lastrun marker.
        self.assertFalse(os.path.exists(self.analyzer_marker))
        self.assertFalse(os.path.exists(self.marker))

    def test_repeated_cron_runs_never_kill_a_persistent_sweep(self):
        """The cron fires hourly against a multi-hour sweep, so the invariant
        is over repeated runs, not one lucky race."""
        proc = self._hold_lock()
        for _ in range(3):
            self.assertEqual(self._run("retention").returncode, 0)
        self.assertIsNone(proc.poll())
        self.assertFalse(os.path.exists(self.kill_marker))

    def test_free_lock_still_runs_retention(self):
        """The skip must not become a skip-forever: with the lock free the
        analyzer runs, the lastrun marker advances, and the web roots sync."""
        result = self._run("retention")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(self.analyzer_marker),
                        "retention did not run when the lock was free")
        self.assertTrue(os.path.exists(self.marker))
        self.assertIn("retention-only: complete", self._log())

    def test_sync_after_retention_leaves_each_cameras_pins_alone(self):
        """The other half of #21: the post-retention sync must not push a
        repo-root pins.json over the camera's own file."""
        cam_pins = os.path.join(self.base, "cam", "pins.json")
        repo_pins = os.path.join(self.base, "pins.json")
        with open(repo_pins, "w") as f:
            f.write('["stale.jpg"]')
        with open(cam_pins, "w") as f:
            f.write('["keep_me.jpg"]')

        self.assertEqual(self._run("retention").returncode, 0)

        with open(cam_pins) as f:
            self.assertEqual(f.read().strip(), '["keep_me.jpg"]')
        # analysis.json/bursts.json are still synced, so sync_web_roots is not
        # simply dead — only pins.json is excluded.
        self.assertTrue(os.path.exists(os.path.join(self.base, "cam", "analysis.json")))

    def test_auto_mode_also_leaves_a_live_sweep_alone(self):
        proc = self._hold_lock()

        result = self._run("auto")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNone(proc.poll())
        self.assertFalse(os.path.exists(self.kill_marker))


class TestNoKillCodeInRetentionPath(unittest.TestCase):
    """Cheap source guard: the retention path has no kill primitives at all.

    A behavioural test only catches a kill on the path it exercises; this
    catches one reintroduced anywhere in the script.
    """

    def test_watchdog_contains_no_kill_primitives(self):
        with open(WATCHDOG) as f:
            lines = f.read().splitlines()
        code = [ln.strip() for ln in lines
                if ln.strip() and not ln.strip().startswith("#")]
        offenders = [ln for ln in code
                     if any(tok in ln for tok in ("pkill", "killall", "fuser -k",
                                                 "kill -9", "killall5"))]
        self.assertEqual(offenders, [],
                         "watchdog.sh grew a kill primitive:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
