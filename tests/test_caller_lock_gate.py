"""#52: a sweep that arrives inside a caller's flock must actually sweep.

Every production caller takes the global pipeline lock and then execs the
analyzer *inside* that flock subshell — `create-index.sh:97` and
`tools/watchdog.sh:157` both do `( ... ) 200>$LOCKFILE` with `flock -x 200`.
flock() is per open-file-description, so the analyzer inherits fd 200 and its
own fresh `open()` + `flock(LOCK_EX|LOCK_NB)` is refused by its own parent.
`take_pipeline_lock()` returned None, `main()` returned EXIT_OK having done
nothing, and because the exit code was 0 both callers still touched the
lastrun marker and logged success. On a real box: nothing analysed, nothing
deleted, nothing pruned, no alerts, unbounded disk, and `/api/health` reading
green the whole time. `--rescan-days` took a *blocking* lock and hung forever
under the same inherited descriptor.

These tests drive the real scripts, not the Python functions, because the bug
lived in the interaction between them:

* `create-index.sh` and `tools/watchdog.sh` and the analyzer and everything it
  imports at module level are copied into a temp dir, so the sweep the test
  observes is the copied analyzer reading the copied `settings.json`;
* `WEBCAM_LOCK`/`WEBCAM_MARKER` (and `WATCHDOG_*` for the watchdog) are
  overridden, so the global `/tmp/webcam_analysis.lock` is never touched and the
  45s debounce cannot mask a result. This is only a faithful override because
  the analyzer reads `WEBCAM_LOCK` too — pre-fix it hardcoded
  `/tmp/webcam_analysis.lock`, so redirecting the callers could not make the
  two collide and the suite stayed green through the regression;
* `inotifywait` is stubbed (the real one tails the camera dir forever), and so
  are `systemctl`/`sudo`/`logger`/`curl` for the watchdog, so nothing on the
  test machine is restarted;
* a 100-day-old `old.jpg` with no parseable camera clock in its name (retention
  ages it by mtime, the documented fallback) plus a fresh `fresh.jpg`: the
  assertions are about real unlinking, not about log text alone.

Every background process is started in its own session and the PGID is recorded
at spawn so the whole group is killed in cleanup. `create-index.sh` leaves an
unbounded `idle_sweep` loop behind (see #53), so without that a run of this
suite leaves orphans behind it.

Run from the repo root:  python3 -m unittest discover -s tests
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The analyzer and everything it imports at module level, so the copied
# analyze_images.py runs with nothing from the repo on sys.path.
MODULES = ("analyze_images.py", "log_config.py", "scans.py", "taxonomy.py",
           "catalog.py", "zones.py", "pipeline_events.py", "ha_mqtt.py",
           "create-index.sh", "index.html", "manifest.json", "icon.svg",
           "favicon.ico", "lucide.min.js")

# The two invocations that, on a real box, run the analyzer inside a held flock.
RESCAN_GATE = """\
#!/bin/bash
# Byte-for-byte the production shape: take the lock on fd 200, then exec the
# analyzer inside the subshell that still holds it.
LOCKFILE="${WEBCAM_LOCK:-/tmp/webcam_analysis.lock}"
(
  if flock -x -w 60 200; then
    exec python3 "$BASE/analyze_images.py" --rescan-days "$1"
  fi
  exit 99
) 200>"$LOCKFILE"
"""

# A real lock holder that announces itself, so the test can run a second
# analyzer against a lock that is provably taken (no sleeping and hoping).
GATED_SWEEP = """\
#!/bin/bash
LOCKFILE="${WEBCAM_LOCK:-/tmp/webcam_analysis.lock}"
(
  if flock -x -w 60 200; then
    : > "$BASE/gate_ready"
    while [ ! -f "$BASE/gate_go" ]; do sleep 0.05; done
    exec python3 "$BASE/analyze_images.py" "$@"
  fi
  exit 99
) 200>"$LOCKFILE"
"""


def _stub(path, body):
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + textwrap.dedent(body).lstrip() + "\n")
    os.chmod(path, 0o755)


class CallerGateSandbox(unittest.TestCase):
    """A throwaway deployment: one camera dir, one lock, one marker."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="webcam_gate52_")
        self.base = self.td.name
        self.bin = os.path.join(self.base, "bin")
        self.cam = os.path.join(self.base, "cam")
        self.lock = os.path.join(self.base, "analysis.lock")
        self.marker = os.path.join(self.base, "lastrun")
        self.out = os.path.join(self.base, "stdout.log")
        self.watchdog_log = os.path.join(self.base, "watchdog.log")
        os.makedirs(self.bin)
        os.makedirs(self.cam)
        for name in MODULES:
            src = os.path.join(REPO_ROOT, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(self.base, name))
        os.makedirs(os.path.join(self.base, "tools"))
        shutil.copy2(os.path.join(REPO_ROOT, "tools", "watchdog.sh"),
                     os.path.join(self.base, "tools", "watchdog.sh"))
        self._write_script("rescan_gate.sh", RESCAN_GATE)
        self._write_script("gated_sweep.sh", GATED_SWEEP)

        # old.jpg has no parsable camera clock in the name, so retention ages it
        # by mtime. fresh.jpg must survive: it is the control that proves the
        # sweep is selective and not just deleting everything.
        self.old = self._frame("old.jpg", 100 * 86400)
        self.fresh = self._frame("fresh.jpg", 0)

        with open(os.path.join(self.base, "settings.json"), "w") as f:
            json.dump({"watch_dirs": [self.cam], "max_age_days": 1,
                       "max_dir_gb": 100, "idle_sweep_seconds": 3600}, f)
        for name in ("analysis.json", "bursts.json"):
            with open(os.path.join(self.base, name), "w") as f:
                f.write("{}")
        # Both frames get a catalog row. An *uncatalogued* frame is exempt
        # from retention while the catalog cannot account for every frame on
        # disk (#56), so a `{}` catalog would protect old.jpg and prove
        # nothing about the lock gate this suite exists to test.
        with open(os.path.join(self.base, "analysis.json"), "w") as f:
            json.dump({"old.jpg": {"fast_pass": "negative"},
                       "fresh.jpg": {"fast_pass": "negative"}}, f)

        # inotifywait really tails the camera dir forever; systemctl/sudo/logger
        # really do things to the machine.
        _stub(os.path.join(self.bin, "inotifywait"), "exit 0")
        _stub(os.path.join(self.bin, "systemctl"), "echo active")
        _stub(os.path.join(self.bin, "sudo"), "exit 0")
        _stub(os.path.join(self.bin, "logger"), "exit 0")
        _stub(os.path.join(self.bin, "curl"), "exit 7")
        self.addCleanup(self.td.cleanup)

    def _write_script(self, name, body):
        path = os.path.join(self.base, name)
        with open(path, "w") as f:
            f.write(textwrap.dedent(body).lstrip().replace("$BASE", self.base))
        os.chmod(path, 0o755)
        return path

    def _frame(self, name, age_s):
        path = os.path.join(self.cam, name)
        with open(path, "wb") as f:
            f.write(b"x" * 128)
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
        return path

    def _env(self, **extra):
        env = dict(os.environ)
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env["WEBCAM_LOCK"] = self.lock
        env["WEBCAM_MARKER"] = self.marker
        env["WEBCAM_BASE"] = self.base
        # No cloud key: the sweep stays a detector-only pass and finishes in
        # about a second, so the test measures the lock and nothing else.
        env["OPENROUTER_API_KEY"] = ""
        env.update(WATCHDOG_LOCK=self.lock, WATCHDOG_MARKER=self.marker,
                   WATCHDOG_LOG=self.watchdog_log,
                   WATCHDOG_API="http://127.0.0.1:1")
        env.update(extra)
        return env

    def _spawn(self, argv, **env_extra):
        """Start a process in its own session; kill the whole group later.

        The PGID is read once, here, while the leader is alive: by cleanup time
        the leader may have exited and the group would be unreachable. This is
        what stops `create-index.sh`'s unbounded `idle_sweep` loop (and the gate
        scripts' own subshells) from outliving the test (#53).
        """
        log = open(self.out, "a")
        proc = subprocess.Popen(argv, env=self._env(**env_extra), cwd=self.base,
                                stdout=log, stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)
        log.close()
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError):
            pgid = None
        self.addCleanup(self._killpg, proc, pgid)
        return proc

    def _killpg(self, proc, pgid):
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass

    def _log(self):
        try:
            with open(self.out) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def _wait_for(self, needle, proc=None, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            out = self._log()
            if needle in out:
                return out
            if proc is not None and proc.poll() is not None:
                return out
            time.sleep(0.1)
        self.fail(f"never saw {needle!r} in the pipeline output:\n{self._log()}")

    def _watchdog_log(self):
        try:
            with open(self.watchdog_log) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def assertSweptAgedFrameOnly(self, out):
        self.assertFalse(os.path.exists(self.old),
                         f"the 100-day-old frame survived a sweep that reported "
                         f"success -- nothing was ever deleted (#52):\n{out}")
        self.assertTrue(os.path.exists(self.fresh),
                        "retention deleted a frame inside max_age_days")

    def _hold_lock(self, seconds=60):
        """A real second process takes the lock and keeps it for `seconds`.

        A live sweep looks like this to everybody else, and unlike a descriptor
        in this process it cannot be mistaken for "the caller handed me the
        lock" — which is the whole point of the fix.
        """
        held = os.path.join(self.base, "lock_held")
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, os, sys, time\n"
             "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o666)\n"
             "fcntl.flock(fd, fcntl.LOCK_EX)\n"
             "open(sys.argv[2], 'w').close()\n"
             "time.sleep(float(sys.argv[3]))\n",
             self.lock, held, str(seconds)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._kill, holder)
        deadline = time.time() + 30
        while not os.path.exists(held):
            if time.time() > deadline:
                self.fail("the lock holder never started")
            time.sleep(0.05)
        return holder

    def _kill(self, proc):
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass

    def _run(self, argv, timeout=180):
        """Run a script to completion with group cleanup; return (rc, output).

        Not subprocess.run: watchdog.sh runs the analyzer inside its own flock
        subshell, so a timeout there would kill the script and leave the
        analyzer running — with the lock. The PGID is recorded at spawn, so the
        whole group goes instead.
        """
        proc = self._spawn(argv)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.fail(f"{argv} did not finish in {timeout}s:\n{self._log()}")
        return proc.returncode, self._log()


class TestCreateIndexSweepRuns(CallerGateSandbox):
    """(i) the real systemd entry path: create-index.sh -> run_analysis()."""

    def _run_until(self, needle):
        proc = self._spawn(["bash", os.path.join(self.base, "create-index.sh"),
                            self.cam])
        # The foreground ends as soon as the stubbed inotifywait returns, but
        # the sweep itself is one of the *backgrounded* run_analysis calls, so
        # the leader exiting is not "the run is over" — keep waiting for the
        # needle, which only the backgrounded sweep can print.
        out = self._wait_for(needle)
        if proc.poll() is None:
            proc.wait(timeout=60)
        return out

    def test_create_index_actually_deletes_aged_frames_and_stamps_the_marker(self):
        out = self._run_until("Analysis and sync complete")

        self.assertNotIn("Analysis FAILED", out)
        self.assertNotIn("held by another run", out,
                         "the analyzer refused its own caller's lock (#52):\n" + out)
        self.assertSweptAgedFrameOnly(out)
        # A marker stamped by a sweep that did nothing is how #27 recurred as
        # #52: /api/health's recent_sweep and the watchdog's sweep_age both
        # read it, so it must follow real work, not an exit code.
        self.assertTrue(os.path.exists(self.marker))

    def test_create_index_still_defers_to_a_live_sweep_holding_the_lock(self):
        """The fix must not become "always proceed": the shell gate still wins.

        A real second process takes the lock for a minute. create-index.sh must
        stay queued on it, run nothing, and — because nothing ran — must not
        stamp the marker. The assertion is a bounded negative window: a sweep
        that owns the lock cannot finish inside it, so its absence is the fact.
        This is the counterpart to the test above; together they say the analyzer
        defers to a lock held by someone else and joins a lock held for it, and
        nothing in between.
        """
        self._hold_lock(seconds=60)
        proc = self._spawn(["bash", os.path.join(self.base, "create-index.sh"),
                            self.cam])
        time.sleep(6)
        out = self._log()
        self.assertNotIn("Starting gated analysis", out,
                         "create-index.sh entered the gated section while "
                         "another process held the lock:\n" + out)
        self.assertNotIn("Analysis and sync complete", out)
        # The frame a live sweep might be reading is intact...
        self.assertTrue(os.path.exists(self.old))
        # ...and nothing reported a successful sweep, so the box reads stale
        # rather than healthy.
        self.assertFalse(os.path.exists(self.marker),
                         "the success marker was stamped by a run that never "
                         "got the lock")
        del proc

    def _kill(self, proc):
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass



class TestWatchdogRetentionRuns(CallerGateSandbox):
    """(ii) the real cron path: tools/watchdog.sh retention."""

    def test_watchdog_retention_actually_deletes_aged_frames(self):
        rc, out = self._run(["bash", os.path.join(self.base, "tools",
                                                  "watchdog.sh"), "retention"])
        self.assertEqual(rc, 0, out)

        self.assertNotIn("held by another run", out,
                         "the retention pass refused its own caller's lock "
                         "(#52):\n" + out)
        self.assertSweptAgedFrameOnly(out)
        self.assertIn("retention-only: complete", self._watchdog_log())
        self.assertTrue(os.path.exists(self.marker),
                        "the watchdog stamped no marker after a real pass")

    def test_watchdog_still_skips_a_live_sweep_and_leaves_it_alone(self):
        self._hold_lock(seconds=60)

        rc, out = self._run(["bash", os.path.join(self.base, "tools",
                                                  "watchdog.sh"), "retention"])

        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(self.old))
        self.assertFalse(os.path.exists(self.marker))
        self.assertIn("lock busy", self._watchdog_log())


class TestRescanDaysDoesNotHang(CallerGateSandbox):
    """(iii) --rescan-days is the one mode that WAITS for the lock, so under the
    inherited descriptor it blocked forever (verified: killed at 25s, rc=137).
    """

    def test_rescan_completes_inside_the_callers_lock(self):
        started = time.time()
        # Spawned in its own session with the PGID recorded, NOT
        # subprocess.run(timeout=...): on the pre-fix code this is the test that
        # hangs, and run() would kill the script's bash and leave the exec'd
        # analyzer behind. The failure mode this guards is "never returns", so
        # the test must be able to clean up after itself in exactly that case.
        proc = self._spawn(["bash", os.path.join(self.base, "rescan_gate.sh"),
                            "7"])
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.fail("analyze_images.py --rescan-days hung on the lock its own "
                      "caller is holding (#52)")
        elapsed = time.time() - started

        out = self._log()
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("Rescan last 7d", out)
        self.assertNotIn("held by another run", out)
        # The gate waits up to 60s, so anything near that is the hang returning.
        self.assertLess(elapsed, 30,
                        f"--rescan-days took {elapsed:.1f}s under the outer gate")

    def test_rescan_still_waits_for_a_lock_someone_else_holds(self):
        """The gate is not a bypass: another holder must still be respected.

        flock(1) with a 0-second timeout against a held lock, so the test does
        not have to wait 60s to learn the analyzer did not barge in.
        """
        self._hold_lock(seconds=60)

        probe = os.path.join(self.base, "rescan_contended.sh")
        with open(probe, "w") as f:
            f.write(textwrap.dedent(f"""\
                #!/bin/bash
                LOCKFILE="${{WEBCAM_LOCK:-/tmp/webcam_analysis.lock}}"
                (
                  if flock -x -w 0 200; then
                    exec python3 "{self.base}/analyze_images.py" --retention-only
                  fi
                  exit 99
                ) 200>"$LOCKFILE"
                """))
        os.chmod(probe, 0o755)

        result_rc, result_out = self._run(["bash", probe], timeout=60)

        # exit 99 = the gate never got the lock, so the analyzer never ran.
        self.assertEqual(result_rc, 99,
                         "a contended run executed the analyzer anyway:\n"
                         + result_out)
        self.assertTrue(os.path.exists(self.old))


class TestConcurrentAnalyzersExcludeEachOther(CallerGateSandbox):
    """(iv) two REAL analyzer processes, one gate: the exclusion is intact.

    The regression was fixed by making the analyzer join a lock it was given
    rather than refuse it, so the risk is that "given" got too generous. This
    pins the other side: while a first process genuinely owns the lock, a
    second one does no work at all, and once the first lets go the second runs
    to completion — no double work, no wedged lock.
    """

    def test_a_second_analyzer_does_nothing_until_the_first_releases(self):
        ready = os.path.join(self.base, "gate_ready")
        first = self._spawn(["bash", os.path.join(self.base, "gated_sweep.sh"),
                             "--retention-only"])
        deadline = time.time() + 30
        while not os.path.exists(ready):
            if time.time() > deadline:
                self.fail("the gated sweep never took the lock")
            time.sleep(0.05)

        second = subprocess.run(
            [sys.executable, os.path.join(self.base, "analyze_images.py"),
             "--retention-only"],
            env=self._env(), capture_output=True, text=True, timeout=120,
            cwd=self.base)
        second_out = second.stdout + second.stderr

        self.assertEqual(second.returncode, 0, second_out)
        self.assertIn("held by another run", second_out,
                      "a second analyzer barged into a lock a real process held")
        self.assertTrue(os.path.exists(self.old),
                        "the second analyzer deleted a frame under a live sweep")
        self.assertNotIn("Retention:", second_out)

        # Release the first run; it holds the lock, so it must sweep.
        with open(os.path.join(self.base, "gate_go"), "w") as f:
            f.write("")
        first.wait(timeout=120)
        self.assertEqual(first.returncode, 0, self._log())
        self.assertSweptAgedFrameOnly(self._log())

        # And the skip was a skip, not a wedge: the lock is free again.
        third = subprocess.run(
            [sys.executable, os.path.join(self.base, "analyze_images.py"),
             "--retention-only"],
            env=self._env(), capture_output=True, text=True, timeout=120,
            cwd=self.base)
        third_out = third.stdout + third.stderr
        self.assertEqual(third.returncode, 0, third_out)
        self.assertNotIn("held by another run", third_out,
                         "the lock was never released by the run that held it")
        # Exactly one of the two sweeps that could do work did it.
        self.assertEqual((self._log() + third_out).count("Retention: removed"), 1)


if __name__ == "__main__":
    unittest.main()
