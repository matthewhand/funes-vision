"""#27: a corrupt settings.json must fail loud, not silently disable retention.

`load_settings()` degraded an unreadable/unparseable/non-object file to `{}`
with a `warning`, and `main()` then found no WATCH_DIRS, printed "No watch_dirs
configured in settings.json - nothing to do." and returned None -> the process
exited 0. Nothing was scanned, nothing was deleted, `run_health_checks` was
never reached, and create-index.sh went on to touch /tmp/webcam_analysis.lastrun
and log "Analysis and sync complete" -- so the watchdog's recent_sweep/ret_age
heuristics read a healthy box while retention was dead and the disk grew
without bound. A half-written file (interrupted write, full disk, an editor
truncating on save) is enough to trigger it.

The fix splits the two states that used to look identical:

* a *missing* settings.json (fresh install) and a valid object with no
  `watch_dirs` yet stay benign -> exit 0;
* a settings.json that cannot be read or parsed raises SettingsError -> ERROR
  log, message on stderr, `EXIT_SETTINGS_ERROR` (3), and the sweep is refused
  before the lock, before apply_settings, and before any catalog is touched.

The "deletes nothing / writes nothing" half of the contract is asserted, not
assumed: the tests put a 100-day-old analyzed frame and a populated catalog in
the tree and check both are byte-identical afterwards.

The last test drives the real `__main__` in a subprocess, because the exit code
create-index.sh keys the success marker off is the *process* exit code.

Run from the repo root:  python3 -m unittest discover -s tests
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_images

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# An interrupted write: valid prefix, no closing bracket, no newline.
CORRUPT = '{"watch_dirs": ["/mnt/models/Webcam21"'
# A well-formed JSON document that is not an object -- e.g. a stray `[]` or a
# truncated-then-repaired file that now holds a bare string.
NOT_AN_OBJECT = '["/mnt/models/Webcam21"]'
# The other direction of the same bug: a *valid* object whose values are junk is
# still a configuration (#28 keeps those lenient) and must not fail the sweep.
HOSTILE_VALUES = {"watch_dirs": None, "max_dir_gb": None, "max_age_days": "abc"}


class TestCorruptSettingsFailsLoud(unittest.TestCase):
    GLOBALS = ("BASE_DIR", "_settings_path", "WATCH_DIRS", "MAX_AGE_DAYS",
               "MAX_DIR_GB", "RETENTION_LOG", "PIPELINE_LOCK", "GATE_IGNORE_LABELS")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="webcam_settings_")
        self.root = self.td.name
        self.img = os.path.join(self.root, "cam1")
        os.makedirs(self.img)
        self.settings_path = os.path.join(self.root, "settings.json")
        self.analysis_path = os.path.join(self.root, "analysis.json")
        self.retention_log = os.path.join(self.root, "retention_log.json")
        # getattr default so the behavioural assertions still run (and fail
        # loudly) against a tree whose main() predates any of this.
        self._saved = {n: getattr(analyze_images, n, None) for n in self.GLOBALS}
        analyze_images.BASE_DIR = self.root
        analyze_images._settings_path = self.settings_path
        analyze_images.WATCH_DIRS = [self.img]
        analyze_images.RETENTION_LOG = self.retention_log
        # main() serialises on the global pipeline lock; keep it in the
        # throwaway tree so a live sweep cannot make these runs skip (#30).
        analyze_images.PIPELINE_LOCK = os.path.join(self.root, "pipeline.lock")
        analyze_images.GATE_IGNORE_LABELS = ["car"]
        health = mock.patch.object(analyze_images, "run_health_checks")
        self.health = health.start()
        ollama = mock.patch.object(analyze_images, "ollama_available", return_value=False)
        ollama.start()
        self.addCleanup(ollama.stop)
        self.addCleanup(self._restore)
        self._seed_tree()

    def _restore(self):
        self.health.stop()
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        self.td.cleanup()

    def _seed_tree(self):
        """A camera dir that retention WOULD prune, plus a populated catalog.

        Both are checked byte-for-byte after a refused sweep: the whole point of
        the fix is that an unreadable config stops the pipeline, it does not
        become an excuse to mutate or delete anything.
        """
        self.victim = os.path.join(self.img, "analyzed_old.jpg")
        with open(self.victim, "wb") as f:
            f.write(b"x" * 100)
        stamp = time.time() - 100 * 86400
        os.utime(self.victim, (stamp, stamp))
        with open(self.analysis_path, "w") as f:
            json.dump({"analyzed_old.jpg": {"fast_pass": "negative"}}, f, indent=2)
        with open(os.path.join(self.root, "pins.json"), "w") as f:
            json.dump([], f)
        self.analysis_before = self._read(self.analysis_path)

    @staticmethod
    def _read(path):
        with open(path, "rb") as f:
            return f.read()

    def _write_settings(self, text):
        with open(self.settings_path, "w") as f:
            f.write(text)

    def _run(self, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = analyze_images.main(retention_only=True,
                                     settings_path=self.settings_path, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    # --- the defect: corrupt in, silent success out -------------------------

    def test_corrupt_settings_exits_nonzero_and_logs_an_error(self):
        self._write_settings(CORRUPT)

        with self.assertLogs("analyze_images", level="ERROR") as cm:
            rc, out, err = self._run()

        self.assertEqual(rc, analyze_images.EXIT_SETTINGS_ERROR)
        self.assertNotEqual(rc, 0, "a refused sweep must not look successful")
        self.assertTrue(any("could not read settings" in m for m in cm.output),
                        f"no ERROR log: {cm.output}")
        # Loud on stderr too: the systemd journal shows both, but a cron caller
        # that only captures stderr must not see an empty run.
        self.assertIn("ERROR", err)
        self.assertIn("settings.json", err)
        # Not the benign message the old code printed on this exact input.
        self.assertNotIn("nothing to do", out)

    def test_corrupt_settings_deletes_nothing_and_writes_no_catalog(self):
        self._write_settings(CORRUPT)

        with self.assertLogs("analyze_images", level="ERROR"):
            self._run()

        self.assertTrue(os.path.exists(self.victim),
                        "retention ran off a config it could not read")
        self.assertEqual(self._read(self.analysis_path), self.analysis_before,
                         "analysis.json was rewritten by a refused sweep")
        self.assertFalse(os.path.exists(os.path.join(self.root, "bursts.json")),
                         "a refused sweep wrote bursts.json")
        self.assertFalse(os.path.exists(self.retention_log),
                         "a refused sweep wrote a retention log")

    def test_corrupt_settings_never_reaches_health_checks(self):
        """run_health_checks is where disk/camera/offline alerts come from; the
        old path returned before it, so a corrupt file silenced every alert."""
        self._write_settings(CORRUPT)

        with self.assertLogs("analyze_images", level="ERROR"):
            self._run()

        self.health.assert_not_called()

    def test_corrupt_settings_does_not_reconfigure_the_process(self):
        """apply_settings({}) would have cleared WATCH_DIRS/CAMERAS -- a corrupt
        file must not be able to reconfigure a live process either."""
        self._write_settings(CORRUPT)
        before = (analyze_images.WATCH_DIRS, analyze_images.MAX_AGE_DAYS,
                  analyze_images.MAX_DIR_GB)

        with self.assertLogs("analyze_images", level="ERROR"):
            self._run()

        self.assertEqual((analyze_images.WATCH_DIRS, analyze_images.MAX_AGE_DAYS,
                          analyze_images.MAX_DIR_GB), before)

    def test_non_object_settings_fails_loud(self):
        self._write_settings(NOT_AN_OBJECT)

        with self.assertLogs("analyze_images", level="ERROR") as cm:
            rc, _out, err = self._run()

        self.assertEqual(rc, analyze_images.EXIT_SETTINGS_ERROR)
        self.assertTrue(any("not a JSON object" in m for m in cm.output),
                        f"no ERROR log: {cm.output}")
        self.assertTrue(os.path.exists(self.victim))

    def test_unreadable_settings_file_fails_loud(self):
        """A chmod 000 file (or an EIO/ENOSPC read) is the same fault class as a
        truncated one: the pipeline cannot know its watch dirs or budgets."""
        self._write_settings(CORRUPT)
        os.chmod(self.settings_path, 0o000)
        self.addCleanup(os.chmod, self.settings_path, 0o600)
        if os.access(self.settings_path, os.R_OK):
            self.skipTest("running as a user that can read a 0000 file")

        rc, _out, err = self._run()

        self.assertEqual(rc, analyze_images.EXIT_SETTINGS_ERROR)
        self.assertIn("ERROR", err)
        self.assertTrue(os.path.exists(self.victim))

    def test_failure_is_reported_even_when_another_run_holds_the_lock(self):
        """The lock-busy path is a benign exit 0, so the settings check must run
        before the lock -- otherwise a corrupt config is reported as a clean
        skip on a busy box and the success marker gets stamped again."""
        import fcntl
        handle = open(analyze_images.PIPELINE_LOCK, "a")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        self.addCleanup(lambda: (fcntl.flock(handle.fileno(), fcntl.LOCK_UN),
                                 handle.close()))
        self._write_settings(CORRUPT)

        with self.assertLogs("analyze_images", level="ERROR"):
            rc, _out, _err = self._run()

        self.assertEqual(rc, analyze_images.EXIT_SETTINGS_ERROR)

    # --- the two benign states that must NOT regress ------------------------

    def test_missing_settings_is_still_a_benign_zero(self):
        """Fresh install: no settings.json yet is 'nothing configured', not a
        fault. The exit code must stay 0 or create-index.sh would report a
        failure on every brand-new box."""
        self.assertFalse(os.path.exists(self.settings_path))
        # A fresh import leaves WATCH_DIRS empty -- only settings.json fills it.
        analyze_images.WATCH_DIRS = []

        rc, out, err = self._run()

        self.assertEqual(rc, analyze_images.EXIT_OK)
        self.assertIn("nothing to do", out)
        self.assertEqual(err, "")
        self.assertTrue(os.path.exists(self.victim),
                        "retention ran with no settings file at all")

    def test_settings_without_watch_dirs_is_still_a_benign_zero(self):
        """A valid object that simply has nothing configured yet -- the state
        that must stay distinguishable from a corrupt file."""
        self._write_settings(json.dumps({"max_age_days": 1, "max_dir_gb": 100}))
        analyze_images.WATCH_DIRS = []

        rc, out, err = self._run()

        self.assertEqual(rc, analyze_images.EXIT_OK)
        self.assertIn("nothing to do", out)
        self.assertEqual(err, "")
        self.health.assert_not_called()

    def test_hostile_values_still_sweep(self):
        """#28 stays fixed: a *readable* file with junk values is coerced per
        key and retention still runs. Only the file itself is a hard failure."""
        self._write_settings(json.dumps({**HOSTILE_VALUES, "watch_dirs": [self.img],
                                         "max_age_days": 1, "max_dir_gb": 100}))

        rc, out, _err = self._run()

        self.assertEqual(rc, analyze_images.EXIT_OK)
        self.assertIn("Retention-only: skipped analysis", out)
        self.assertFalse(os.path.exists(self.victim),
                         "a readable settings.json must still age frames out")


class TestLoadSettingsContract(unittest.TestCase):
    """The seam itself: strict for the sweep, lenient for import.

    ``apply_settings()`` runs at module import, so raising there would take the
    whole module down before the CLI could report anything (#28). The default
    therefore keeps degrading to {}, and only the sweep asks to be told.
    """

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="webcam_load_settings_")
        self.path = os.path.join(self.td.name, "settings.json")
        self.addCleanup(self.td.cleanup)

    def _write(self, text):
        with open(self.path, "w") as f:
            f.write(text)
        return self.path

    def test_missing_file_is_not_an_error_in_either_mode(self):
        missing = os.path.join(self.td.name, "absent.json")
        self.assertEqual(analyze_images.load_settings(missing), {})
        self.assertEqual(analyze_images.load_settings(missing, strict=True), {})

    def test_strict_raises_where_lenient_warns(self):
        path = self._write(CORRUPT)
        with self.assertLogs("analyze_images", level="WARNING") as cm:
            self.assertEqual(analyze_images.load_settings(path), {})
        self.assertTrue(any("could not read settings" in m for m in cm.output))
        with self.assertRaises(analyze_images.SettingsError):
            analyze_images.load_settings(path, strict=True)

    def test_settings_error_names_the_file(self):
        path = self._write(CORRUPT)
        with self.assertRaises(analyze_images.SettingsError) as cm:
            analyze_images.load_settings(path, strict=True)
        self.assertIn(path, str(cm.exception))


class TestProcessExitCode(unittest.TestCase):
    """The exit code create-index.sh keys the success marker off.

    create-index.sh and tools/watchdog.sh both read `$?` from the *process*, so
    an in-process return value proves nothing about the contract that broke in
    #27. This copies the analyzer and its local modules into a throwaway tree --
    so `_settings_path` (derived from the module's own directory) points at the
    copy, never at the repo's real settings.json -- and runs the real
    `__main__`.
    """

    MODULES = ("analyze_images.py", "log_config.py", "scans.py", "taxonomy.py",
               "catalog.py", "zones.py", "pipeline_events.py", "ha_mqtt.py")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="webcam_cli_")
        self.base = self.td.name
        for name in self.MODULES:
            shutil.copy2(os.path.join(REPO_ROOT, name), os.path.join(self.base, name))
        self.settings = os.path.join(self.base, "settings.json")
        self.addCleanup(self.td.cleanup)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "analyze_images.py", *args], cwd=self.base,
            capture_output=True, text=True, timeout=120)

    def test_cli_exits_three_on_a_corrupt_settings_file(self):
        with open(self.settings, "w") as f:
            f.write(CORRUPT)

        result = self._run("--retention-only")

        self.assertEqual(result.returncode, analyze_images.EXIT_SETTINGS_ERROR,
                         result.stdout + result.stderr)
        self.assertIn("ERROR", result.stderr)
        self.assertNotIn("nothing to do", result.stdout + result.stderr)

    def test_cli_exits_zero_with_no_settings_file(self):
        result = self._run("--retention-only")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("nothing to do", result.stdout)

    def test_cli_exits_zero_when_the_sweep_runs(self):
        """A real configured sweep still exits 0 -- the gate is not a blanket
        failure, only the settings fault turns it red."""
        with open(self.settings, "w") as f:
            json.dump({"watch_dirs": [os.path.join(self.base, "cam")]}, f)
        os.makedirs(os.path.join(self.base, "cam"))

        result = self._run("--retention-only")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
