"""Shared isolation helpers for the Python suites.

Three pieces of *machine* state leak into these tests unless every file pins
them, and all three produced failures that only reproduced on a deployed box --
green in CI, red on the operator's laptop, or (worse) green for the wrong
reason.

1. ``api_server.SETTINGS_FILE`` and the ``WEBCAM_API_TOKEN`` env var.
   ``api_token()`` falls back to the settings.json sitting next to the script,
   so on a box with a real token configured every mutating endpoint answers
   401 and the pin tests fail. #39 fixed this in ``test_api_cameras.py``; the
   same guard is in :func:`pin_api_auth_off` for the newer pin suite.

2. ``analyze_images.PIPELINE_LOCK``, hardcoded to the machine-global
   ``/tmp/webcam_analysis.lock``. ``main()`` takes it *non-blocking* and prints
   "lock ... is held by another run" then returns a benign exit 0 when it is
   busy -- so a real multi-hour sweep turns a test's sweep into a silent
   no-op. That is the normal state of a production box, and it makes deletion
   assertions fail, or worse, pass, because exit 0 from "busy" is
   indistinguishable from exit 0 from "nothing configured".
   :func:`pin_private_lock` redirects it into the test's own temp tree and
   :func:`assert_sweep_ran` refuses to accept a skipped sweep.

3. The ``create-index.sh`` process tree. The script backgrounds ``idle_sweep &``
   *before* its foreground inotifywait loop, so once the session leader is
   gone the backgrounded loop is still running -- one leaked loop per spawn,
   each re-invoking ``analyze_images.py`` and contending for the global lock,
   which is what makes (2) so easy to hit. :func:`record_group` /
   :func:`reap_all` capture the PGID while the leader is alive and kill the
   whole group.

Importing this module also runs :func:`check_global_lock` once per process.
See its docstring for why it reports instead of raising.

No pytest here -- the suites are stdlib ``unittest`` (see CONTRIBUTING.md), so
this is imported directly by the files that need it:

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import hermetic
"""
import fcntl
import os
import signal
import sys
import time

# analyze_images.py:85. Machine-global, so every caller in main() shares it.
GLOBAL_PIPELINE_LOCK = "/tmp/webcam_analysis.lock"

# Substring of main()'s lock-busy line, and of the benign "nothing to do" it
# shares an exit code with. assert_sweep_ran() treats either as a no-op sweep.
LOCK_BUSY = "is held by another run"
NOTHING_TO_DO = "nothing to do"

_CHECKED = False
_SPAWNED = []


# --- the global pipeline lock -------------------------------------------------

def lock_is_held(path=GLOBAL_PIPELINE_LOCK):
    """True if some other process currently holds `path` under flock.

    Cheap (one non-blocking flock + unlock) and side-effect free, so it is safe
    to call at session start. An unopenable path counts as *not* held: there is
    then nothing to contend for, and take_pipeline_lock() fails closed on its
    own.
    """
    try:
        fh = open(path, "a")
    except OSError:
        return False
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return False


def check_global_lock():
    """Report once per session whether the global pipeline lock is free.

    A test that accidentally uses the machine-global lock turns a busy box into
    a test failure with no obvious cause, so say it out loud, once, before the
    results rather than leaving it to be reverse-engineered from a diff.

    This *reports* rather than raises, deliberately. Every caller of ``main()``
    now pins its own lock, so a held global lock cannot change any result --
    that is the property the lock-contention run is meant to demonstrate -- and
    raising here would reintroduce exactly the environment-dependence this
    module exists to remove, turning every run on a production box red for a
    condition that is now harmless.
    """
    global _CHECKED
    if _CHECKED:
        return
    _CHECKED = True
    if not lock_is_held():
        return
    print("=" * 72, file=sys.stderr)
    print(f"WARNING: {GLOBAL_PIPELINE_LOCK} is HELD by another process.", file=sys.stderr)
    print("A real sweep is in progress on this box. Tests must pin their own", file=sys.stderr)
    print("PIPELINE_LOCK (hermetic.pin_private_lock); if one is failing, this is", file=sys.stderr)
    print("the first thing to look at.", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    sys.stderr.flush()


def pin_private_lock(testcase, module, directory, attr="PIPELINE_LOCK",
                     filename="pipeline.lock"):
    """Point `module.<attr>` at a lock only this test can ever hold.

    `directory` should be the test's own throwaway tree, so the file is removed
    with everything else and no two tests can collide. Restores the previous
    value through ``testcase.addCleanup``.
    """
    saved = getattr(module, attr)
    path = os.path.join(directory, filename)
    setattr(module, attr, path)

    def restore():
        setattr(module, attr, saved)

    testcase.addCleanup(restore)
    return path


def assert_sweep_ran(testcase, output):
    """Fail unless main() actually swept.

    A skipped sweep is a *clean* exit 0: "lock busy" and "nothing configured"
    are both benign, so an assertion downstream of main() can go green because
    the pipeline never ran. Call this on captured stdout wherever a test's
    real subject is the sweep's effect.
    """
    for marker, why in ((LOCK_BUSY, "the global pipeline lock was held"),
                        (NOTHING_TO_DO, "nothing was configured to sweep")):
        if marker in output:
            testcase.fail(
                f"the sweep was skipped, not run ({why}), so this assertion "
                f"would pass for the wrong reason:\n{output}")


# --- api auth ----------------------------------------------------------------

def pin_api_auth_off(testcase, api_module, directory,
                     filename="no_settings.json"):
    """Neutralise api_token() for a test that drives the API in-process.

    The same guard as #39's fix in test_api_cameras.py: point SETTINGS_FILE at
    a file that does not exist and drop WEBCAM_API_TOKEN from the environment,
    so a deployed settings.json carrying a real token (or a token exported in
    the developer's shell) cannot turn every mutating request into a 401 --
    and, on the other side, a settings.json with a whitespace-only api_token
    cannot raise BlankTokenError (#25) out of the middle of a pin test.
    """
    saved_settings = api_module.SETTINGS_FILE
    saved_token = os.environ.pop("WEBCAM_API_TOKEN", None)
    api_module.SETTINGS_FILE = os.path.join(directory, filename)

    def restore():
        api_module.SETTINGS_FILE = saved_settings
        if saved_token is not None:
            os.environ["WEBCAM_API_TOKEN"] = saved_token

    testcase.addCleanup(restore)
    return api_module.SETTINGS_FILE


# --- process groups ----------------------------------------------------------

def group_alive(pgid):
    """True while any process remains in the group `pgid`."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_group(pgid, sig=signal.SIGKILL):
    """Signal the whole process group. Missing/alien groups are not an error."""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def record_group(popen, pgid):
    """Remember a spawned session group so reap_all() can guarantee it is gone.

    The PGID *must* be read while the session leader is still alive. With
    ``start_new_session=True`` the group's id is the leader's pid, so once the
    leader exits ``os.getpgid(proc.pid)`` raises ProcessLookupError and the
    still-running backgrounded children become unreachable by that route.
    """
    _SPAWNED.append((popen, pgid))
    return pgid


def reap_all(timeout=15.0):
    """Kill every recorded group; return the pgids that refused to die.

    Empty list is the pass condition -- an empty list is what "no orphans" is
    supposed to look like, and asserting it is the only way a leak in a future
    refactor of a test in this file gets noticed.
    """
    recorded = list(_SPAWNED)
    _SPAWNED.clear()
    pgids = [pgid for _, pgid in recorded]
    for popen, pgid in recorded:
        kill_group(pgid)
        if popen.poll() is None:
            try:
                popen.wait(timeout=timeout)
            except Exception:
                pass
    # The leader's children take a moment to be torn down after SIGKILL, so
    # poll rather than checking once. The pgid is a just-reaped pid, so the
    # window in which it could be recycled underneath us is tiny.
    deadline = time.time() + timeout
    survivors = list(pgids)
    while survivors and time.time() < deadline:
        survivors = [pg for pg in survivors if group_alive(pg)]
        if survivors:
            time.sleep(0.05)
    for pgid in survivors:
        kill_group(pgid)
    return survivors


check_global_lock()
