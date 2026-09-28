"""A connection burst must not put a thread on the stack per connection.

#58. `ThreadingHTTPServer` starts one thread per accepted connection,
unconditionally. The per-connection socket timeout #33 added bounds a
thread's *lifetime*, not the thread *count*: a burst of peers that connect
and then never finish a request line still cost one thread each, for the whole
timeout window. The documented box has 4 cores, and /api/health -- the uptime
monitor and `tools/watchdog.sh` -- is served by one of those threads, so
exhausting them is an availability outage rather than a slow response.

/api/events already had its own cap (`WEBCAM_SSE_MAX_CLIENTS`, #71). This is
the same shape for every other connection: a slot taken before the thread
exists, a 503 for the ones past the ceiling, and the slot given back however
the thread ends.

The ceiling is deliberately generous (64). Normal single-user use is one or
two connections -- a browser opens at most 6 per host, and a reverse proxy
reuses one upstream connection -- so the tests below also pin that a normal
parallel page load is untouched, and that the cap is read per connection
rather than latched at startup.
"""
import http.client
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_server  # noqa: E402

CAP_ENV = "WEBCAM_API_MAX_CONNECTIONS"


class CounterCase(unittest.TestCase):
    """The slot accounting is module-global, so it has to be drained per test
    or a leaked count from one test becomes another's outage."""

    ENV = (CAP_ENV, "WEBCAM_API_SOCKET_TIMEOUT", "WEBCAM_CORS_ORIGIN")

    def setUp(self):
        self._orig_env = {k: os.environ.pop(k, None) for k in self.ENV}
        self.addCleanup(self._restore_env)
        self.addCleanup(self._drain)
        self._drain()

    def _drain(self):
        """Release every outstanding slot. `conn_release` is a no-op at zero,
        so this converges from any starting count."""
        deadline = time.time() + 10
        while api_server.conn_active() and time.time() < deadline:
            api_server.conn_release()
            time.sleep(0.01)
        self.assertEqual(api_server.conn_active(), 0,
                         "a test leaked a connection slot")

    def _restore_env(self):
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestSlotAccounting(CounterCase):
    def test_default_is_generous(self):
        self.assertEqual(api_server.conn_limit(), 64)
        # 64 concurrent connections is far above any single-user page load and
        # far below anything that could exhaust a 4-core box.
        self.assertGreaterEqual(api_server.conn_limit(), 16)

    def test_acquire_up_to_the_ceiling_then_refuse(self):
        limit = 5
        os.environ[CAP_ENV] = str(limit)
        for i in range(limit):
            self.assertTrue(api_server.conn_try_acquire(), f"slot {i}")
        self.assertFalse(api_server.conn_try_acquire())
        self.assertEqual(api_server.conn_active(), limit)
        api_server.conn_release()
        self.assertTrue(api_server.conn_try_acquire())
        self.assertEqual(api_server.conn_active(), limit)

    def test_zero_disables_the_cap(self):
        os.environ[CAP_ENV] = "0"
        for _ in range(50):
            self.assertTrue(api_server.conn_try_acquire())
        self.assertEqual(api_server.conn_active(), 50)

    def test_garbage_falls_back_to_the_default(self):
        for bad in ("banana", "", " "):
            with self.subTest(value=bad):
                os.environ[CAP_ENV] = bad
                self.assertEqual(api_server.conn_limit(), 64)

    def test_the_limit_is_read_per_call_not_latched(self):
        self.assertTrue(api_server.conn_try_acquire())
        os.environ[CAP_ENV] = "1"
        self.assertFalse(api_server.conn_try_acquire(),
                         "an Environment= edit must apply to the next "
                         "connection, with no restart")
        api_server.conn_release()

    def test_release_never_goes_negative(self):
        api_server.conn_release()
        api_server.conn_release()
        self.assertEqual(api_server.conn_active(), 0)


class TestBurstOverARealListener(CounterCase):
    """The defect is about threads, so it is observed as threads."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="webcam_conncap_")
        self.front = os.path.join(self.tmp, "front")
        os.makedirs(self.front)
        self._orig = {g: getattr(api_server, g) for g in
                      ("WATCH_DIRS", "BASE_DIR", "SETTINGS_FILE",
                       "INTEGRATIONS_FILE", "PINS_FILE")}
        api_server.WATCH_DIRS = [self.front]
        api_server.BASE_DIR = self.tmp
        api_server.SETTINGS_FILE = os.path.join(self.tmp, "settings.json")
        api_server.INTEGRATIONS_FILE = os.path.join(self.tmp, "integrations.json")
        api_server.PINS_FILE = os.path.join(self.tmp, "pins.json")
        os.environ["WEBCAM_CORS_ORIGIN"] = "http://localhost:8180"
        # Long, so a stalled connection holds its slot for the whole burst
        # instead of quietly recycling and letting the cap look unenforced.
        # Cleanup does not depend on it: closing the client socket ends the
        # handler thread, which is what the finally-block below relies on.
        os.environ["WEBCAM_API_SOCKET_TIMEOUT"] = "300"
        self.addCleanup(self._restore)
        self.srv = api_server.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), api_server.Handler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)

    def _restore(self):
        for g, v in self._orig.items():
            setattr(api_server, g, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _get(self, path="/api/health", timeout=10):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def _stall(self, wait=0.4):
        """A connection that has sent a partial request line and will never
        finish it: readline() blocks, so the thread it owns stays alive and
        countable.

        Returns (socket, first bytes). A refused peer answers 503 at once; a
        granted one says nothing, so `wait` only has to be long enough to see
        the refusal -- not long enough to wait out a response that is never
        coming."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1")
        s.settimeout(wait)
        try:
            return s, s.recv(200)
        except OSError:
            return s, b""

    def _settle(self, timeout=15):
        """Wait for every server thread to hand its slot back, so no zombie
        thread from this test can release a slot a later one is holding."""
        deadline = time.time() + timeout
        while api_server.conn_active() and time.time() < deadline:
            time.sleep(0.02)
        return api_server.conn_active()

    def test_a_burst_beyond_the_cap_does_not_spawn_unbounded_threads(self):
        cap = 8
        burst = 60
        os.environ[CAP_ENV] = str(cap)
        baseline = threading.active_count()
        open_socks, refused, refusal = [], 0, b""
        try:
            for _ in range(burst):
                s, head = self._stall()
                if head.startswith(b"HTTP/1.0 503"):
                    refused += 1
                    refusal = head
                    s.close()
                    continue
                self.assertEqual(head, b"", "a slot was granted and it answered")
                open_socks.append(s)

            peak = 0
            for _ in range(40):
                time.sleep(0.05)
                peak = max(peak, threading.active_count() - baseline)

            self.assertLessEqual(
                peak, cap, f"spawned {peak} handler threads for a cap of {cap}")
            self.assertEqual(len(open_socks), cap)
            self.assertEqual(refused, burst - cap)
            self.assertEqual(api_server.conn_active(), cap)
            # The refusal is a real HTTP answer, not a dropped connection: the
            # client learns why, instead of seeing a reset it cannot explain.
            self.assertIn(b"too many concurrent connections", refusal)
            self.assertTrue(refusal.endswith(
                api_server.BUSY_RESPONSE_BODY.encode()), refusal)
        finally:
            for s in open_socks:
                s.close()
            self.assertEqual(self._settle(), 0,
                             "closing the stalled sockets must free the slots")

    def test_a_normal_parallel_page_load_is_untouched(self):
        """The cap must not ration ordinary use. A browser opens up to 6
        connections per host, and the SPA polls status and health; a page
        load must not be able to reach the ceiling."""
        results = []
        lock = threading.Lock()

        def hit():
            try:
                status, _ = self._get("/api/health")
            except OSError as e:  # pragma: no cover - a failure, not a 503
                status = repr(e)
            with lock:
                results.append(status)

        threads = [threading.Thread(target=hit) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(len(results), 6)
        self.assertTrue(all(r in (200, 503) for r in results), results)
        # And the ceiling is still there afterwards: the default is 64.
        self.assertEqual(api_server.conn_limit(), 64)
        self.assertEqual(self._settle(), 0,
                         "every slot must come back once the request ends")

    def test_a_slot_is_released_when_the_handler_raises(self):
        """A crash in one request must not strand its slot -- otherwise a
        handful of bad requests would silently shrink the cap until the API
        refused everyone."""
        def boom(_self):
            raise RuntimeError("handler exploded")

        with unittest.mock.patch.object(api_server.Handler, "do_GET", boom):
            try:
                self._get("/api/health", timeout=5)
            except (OSError, http.client.HTTPException):
                pass  # the thread died mid-response; that is the point
        self.assertEqual(self._settle(), 0,
                         "a raising handler leaked its connection slot")

    def test_the_server_binds_a_backlog_worth_queueing_into(self):
        # The stdlib default is 5, which refuses a legitimate parallel load
        # before the cap is even in play.
        self.assertGreaterEqual(
            api_server.BoundedThreadingHTTPServer.request_queue_size, 32)
        self.assertTrue(api_server.BoundedThreadingHTTPServer.daemon_threads)

    def test_the_default_server_used_in_production_is_the_bounded_one(self):
        """`python3 api_server.py` must not fall back to the unbounded stdlib
        server, or the cap is dead code."""
        with open(api_server.__file__) as f:
            source = f.read()
        entry = source.split('if __name__ == "__main__":')[-1]
        self.assertIn("BoundedThreadingHTTPServer", entry)
        # A bare `ThreadingHTTPServer(` on its own line, not the subclass.
        self.assertIsNone(re.search(r"^\s+ThreadingHTTPServer\(",
                                    entry, re.M),
                          "the entry point still binds the unbounded server")


if __name__ == "__main__":
    unittest.main()
