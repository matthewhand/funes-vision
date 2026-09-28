"""#30: every entry path takes the global pipeline lock.

The flock was taken only in the --rescan-days branch, so the cron
--retention-only watchdog ran unlocked: it deleted frames while a live sweep
was mid-cv2.imread on them, and the shared 1s-debounced catalog flush made the
last writer win. The lock now lives in main(), so every caller is serialised.

Taking the lock in main() is not the same as owning it, and #52 was what made
the difference bite. Both production callers (create-index.sh:97,
tools/watchdog.sh:157) take this very lock and then exec the analyzer inside
their flock subshell, so the descriptor is INHERITED. flock() is per
open-file-description, so a fresh open()+flock() in the child is refused by its
own parent: every production sweep became a silent no-op behind a green lastrun
marker, and --rescan-days blocked forever. take_pipeline_lock() now flocks the
inherited descriptor instead of a second one.

So the two states that used to be conflated — and that this file used to assert
as one — are now distinct and each has a test:

* another PROCESS holds the lock -> take the lock -> None, sweep skipped;
* WE hold it through a descriptor already open in this process -> proceed.

An earlier version of this file held the lock on its own fd and asserted
take_pipeline_lock returned None. That asserted the #52 bug as correct: the
test process was in exactly the inherited-descriptor position, and the answer
was "skip", so no sweep could ever run under either real caller.

Run from the repo root:  python3 -m unittest discover -s tests
"""
import fcntl
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_images

# A real second process: open the lock, flock it, say so, then wait. Used to
# stand in for "a live sweep is running" without this process holding anything.
HOLDER = (
    "import fcntl, os, sys, time\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o666)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "open(sys.argv[2], 'w').close()\n"
    "time.sleep(float(sys.argv[3]))\n"
)


class TestPipelineLock(unittest.TestCase):
    GLOBALS = ("BASE_DIR", "WATCH_DIRS", "_settings_path", "MAX_AGE_DAYS",
               "MAX_DIR_GB", "PERSIST_BUDGET_PCT", "RETENTION_LOG",
               "GATE_IGNORE_LABELS", "PIPELINE_LOCK")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.img = os.path.join(self.root, "cam1")
        os.makedirs(self.img)
        self.lock_path = os.path.join(self.root, "pipeline.lock")
        # getattr default keeps the behavioural tests runnable (and failing)
        # against a tree whose main() predates the pipeline lock.
        self._saved = {n: getattr(analyze_images, n, None) for n in self.GLOBALS}
        analyze_images.BASE_DIR = self.root
        analyze_images._settings_path = os.path.join(self.root, "settings.json")
        analyze_images.WATCH_DIRS = [self.img]
        analyze_images.MAX_AGE_DAYS = 1
        analyze_images.MAX_DIR_GB = 100
        analyze_images.PERSIST_BUDGET_PCT = 20.0
        analyze_images.RETENTION_LOG = os.path.join(self.root, "retention_log.json")
        analyze_images.GATE_IGNORE_LABELS = ["car"]
        analyze_images.PIPELINE_LOCK = self.lock_path
        with open(os.path.join(self.root, "analysis.json"), "w") as f:
            f.write("{}")
        self.victim = os.path.join(self.img, "old.jpg")
        with open(self.victim, "wb") as f:
            f.write(b"x" * 100)
        stamp = time.time() - 100 * 86400
        os.utime(self.victim, (stamp, stamp))
        with open(analyze_images._settings_path, "w") as f:
            json.dump({"watch_dirs": [self.img], "max_age_days": 1,
                       "max_dir_gb": 100}, f)
        patcher = mock.patch.object(analyze_images, "ollama_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        self.td.cleanup()

    def _hold_lock(self):
        """A real OTHER PROCESS holds the lock, so this sweep must skip.

        Not a descriptor in this process: take_pipeline_lock() treats an
        open descriptor on the lock file as "the caller already holds it" and
        proceeds, which is the production shape (an exec'd caller) and the
        whole point of #52. Excluding the lock here therefore has to be done
        with a genuine second process, or the test would be asserting the bug.
        """
        held = os.path.join(self.root, "lock_held")
        proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER, self.lock_path, held, "120"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._stop, proc)
        deadline = time.time() + 20
        while not os.path.exists(held) and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(os.path.exists(held), "the lock holder never started")
        return proc

    def _stop(self, proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=20)

    def _hold_lock_inherited(self):
        """This process already holds the lock on a descriptor it was given.

        The production shape is a shell that flocks fd 200 and then execs us, so
        the analyzer arrives with a descriptor on the lock file that already
        holds it (flock is per open-file-description). take_pipeline_lock() must
        re-lock THAT descriptor and let the sweep run -- taking a second one is
        refused by this very process, which is what made every sweep a no-op.
        """
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o666)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.addCleanup(self._drop, fd)
        return fd

    def _drop(self, fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass  # already dropped by the test itself

    def test_retention_only_does_nothing_while_another_process_holds_the_lock(self):
        self._hold_lock()
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        log = out.getvalue()
        self.assertIn("Pipeline lock", log)
        self.assertIn("nothing analysed, nothing deleted", log)
        self.assertNotIn("Scanning", log)
        self.assertNotIn("Retention:", log)
        # The whole point: the frame a live sweep might be reading survives.
        self.assertTrue(os.path.exists(self.victim))

    def test_full_sweep_does_nothing_while_another_process_holds_the_lock(self):
        self._hold_lock()
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(settings_path=analyze_images._settings_path)
        self.assertIn("Pipeline lock", out.getvalue())
        self.assertTrue(os.path.exists(self.victim))

    def test_sweep_runs_when_the_caller_already_holds_the_lock_for_us(self):
        """#52: both production callers exec us holding this same lock.

        Refusing here made every sweep a no-op: nothing analysed, deleted,
        pruned or alerted, while the lastrun marker still went green.
        """
        self._hold_lock_inherited()
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        log = out.getvalue()
        self.assertNotIn("Pipeline lock", log)
        self.assertIn("Retention:", log)
        self.assertFalse(os.path.exists(self.victim),
                         "a sweep inside a caller-held lock did no retention")

    def test_full_sweep_runs_when_the_caller_already_holds_the_lock_for_us(self):
        self._hold_lock_inherited()
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(settings_path=analyze_images._settings_path)
        self.assertNotIn("Pipeline lock", out.getvalue())
        self.assertIn("Scanning", out.getvalue())

    def test_borrowed_lock_is_not_released_while_the_caller_still_holds_it(self):
        """release() must not LOCK_UN a descriptor the caller lent us.

        Unlocking a borrowed open-file-description drops the caller's lock too:
        in tools/watchdog.sh that would free /tmp/webcam_analysis.lock before
        run_retention() finished touching the marker and syncing the web roots,
        letting a live sweep start on top of a half-finished retention pass.
        """
        fd = self._hold_lock_inherited()
        handle = analyze_images.take_pipeline_lock(blocking=False)
        self.assertIsNotNone(handle)
        self.assertFalse(handle.owned)
        handle.release()
        # The caller's lock is intact: another process is still excluded.
        other = subprocess.run(
            ["flock", "-n", "-x", self.lock_path, "-c", "true"],
            capture_output=True)
        self.assertNotEqual(other.returncode, 0,
                            "releasing the borrowed lock let a second run in")
        # ...and once the caller itself lets go, it is free again.
        self._drop(fd)
        again = subprocess.run(
            ["flock", "-n", "-x", self.lock_path, "-c", "true"],
            capture_output=True)
        self.assertEqual(again.returncode, 0)

    def test_inherited_descriptor_on_the_lock_file_that_is_barely_open_still_excludes(self):
        """A descriptor on the lock file is NOT taken as proof of ownership.

        If a caller opened the file without flocking it, we must actually
        acquire the lock on that descriptor -- so a third process holding it
        still shuts this run out. Otherwise the fix would be a hole in exactly
        the mutual exclusion #30 was for.
        """
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o666)
        self.addCleanup(os.close, fd)  # opened, never flocked
        self._hold_lock()
        self.assertIsNone(analyze_images.take_pipeline_lock(blocking=False))
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        self.assertIn("held by another run", out.getvalue())
        self.assertTrue(os.path.exists(self.victim))

    def test_sweep_runs_and_deletes_when_lock_is_free(self):
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        self.assertNotIn("Pipeline lock", out.getvalue())
        self.assertFalse(os.path.exists(self.victim))

    def test_lock_is_released_after_the_sweep(self):
        analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        # A second run must not be blocked by the first one's lock.
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        self.assertNotIn("Pipeline lock", out.getvalue())

    def test_lock_is_released_when_the_sweep_raises(self):
        with mock.patch.object(analyze_images, "_run_sweep", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                analyze_images.main(retention_only=True)
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        self.assertNotIn("Pipeline lock", out.getvalue())

    def test_take_lock_returns_none_when_another_process_holds_it(self):
        self._hold_lock()
        self.assertIsNone(analyze_images.take_pipeline_lock(blocking=False))

    def test_take_lock_joins_a_lock_the_caller_already_holds(self):
        """The inherited-descriptor case: we return a usable handle, not None."""
        self._hold_lock_inherited()
        handle = analyze_images.take_pipeline_lock(blocking=False)
        self.assertIsNotNone(handle,
                             "a lock held for us on an inherited descriptor "
                             "read as 'busy', which is #52")
        handle.release()

    def test_take_lock_returns_handle_when_free(self):
        handle = analyze_images.take_pipeline_lock(blocking=False)
        self.assertIsNotNone(handle)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def test_take_lock_creates_the_lock_file(self):
        self.assertFalse(os.path.exists(self.lock_path))
        handle = analyze_images.take_pipeline_lock(blocking=False)
        self.assertTrue(os.path.exists(self.lock_path))
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def test_take_lock_borrows_rather_than_creates_a_second_descriptor(self):
        fd = self._hold_lock_inherited()
        handle = analyze_images.take_pipeline_lock(blocking=False)
        self.assertNotEqual(handle.fileno(), fd,
                            "the lock was taken on a second descriptor, which "
                            "its own process then refuses (#52)")
        handle.close()
        # Closing the handle must not close the descriptor the caller lent us.
        os.fstat(fd)

    def test_default_lock_path_is_the_documented_one(self):
        # The CLI (systemd timer / cron) shares one lock with the live sweep.
        # setUp points PIPELINE_LOCK at the throwaway tree, so compare against
        # the module default captured before that override.
        self.assertEqual(self._saved["PIPELINE_LOCK"],
                         os.environ.get("WEBCAM_LOCK") or "/tmp/webcam_analysis.lock")

    def test_default_lock_path_is_unaffected_by_the_env_override(self):
        """Operators' /tmp/webcam_analysis.lock stays the default. Guarded in a
        child process so the module import happens with WEBCAM_LOCK cleared."""
        out = subprocess.run(
            [sys.executable, "-c",
             "import analyze_images, sys; sys.stdout.write(analyze_images.PIPELINE_LOCK)"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env={k: v for k, v in os.environ.items() if k != "WEBCAM_LOCK"},
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "/tmp/webcam_analysis.lock")


class TestLockPathOverride(unittest.TestCase):
    """WEBCAM_LOCK must move the WHOLE pipeline's lock, not just the shell half.

    Pre-fix the analyzer hardcoded /tmp/webcam_analysis.lock, so every test that
    redirected the callers' WEBCAM_LOCK still had the analyzer grabbing the real
    global lock, and the two never collided -- which is how #52 shipped behind a
    green suite (tests/test_create_index_settings_marker.py).

    The collision itself is the thing to pin: run the real analyzer with
    WEBCAM_LOCK set and confirm it never touches the default path.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _resolve(self, lock):
        out = subprocess.run(
            [sys.executable, "-c",
             "import analyze_images, sys; sys.stdout.write(analyze_images.PIPELINE_LOCK)"],
            cwd=self.REPO, env={**os.environ, "WEBCAM_LOCK": lock},
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_analyzer_honours_the_weblocam_lock_env_var(self):
        target = "/tmp/does-not-exist-webcam-test.lock"
        self.assertEqual(self._resolve(target), target)

    def test_empty_weblocam_lock_falls_back_to_the_default(self):
        self.assertEqual(self._resolve(""), "/tmp/webcam_analysis.lock")


if __name__ == "__main__":
    unittest.main()
