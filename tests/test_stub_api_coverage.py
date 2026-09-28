"""The screenshot/demo stub must serve EVERY /api/* path the SPA fetches.

Issue #66: the stub in tools/screenshots/proxy.py predated the camera
abstraction and canonical-taxonomy work, so GET /api/cameras and
GET /api/taxonomy 404'd. Nothing noticed, because the SPA falls back to an
embedded taxonomy and derives cameras from the directory listing — a 404 and a
success look identical on screen, and the published demo bundle (built by
tools/demo/build_demo.py) was generated against the stale API surface.

So the endpoint list is *derived from index.html* here, not hardcoded: this
test fails when the SPA grows an endpoint the stub does not serve, which is the
only way that class of bug gets caught. tools/screenshots/proxy.py's
LIVE_API_ALLOWLIST is checked against the same derived list, so a new endpoint
cannot be added to the SPA and forgotten in live mode either.
"""
import http.client
import importlib.util
import json
import os
import re
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = os.path.join(HERE, "tools", "screenshots", "proxy.py")
INDEX = os.path.join(HERE, "index.html")

# /api/events is a long-lived SSE connection, not a fixture read: assert 200
# and the content-type on the response head, never read the body.
STREAM_ROUTES = frozenset({"/api/events"})
# Live mode refuses /api/events by design — see
# test_screenshot_proxy.test_live_api_404s_events.
LIVE_REFUSED = frozenset({"/api/events"})

# A /api/<name> literal anywhere in a URL position, e.g.
#   apiFetch('/api/cameras', ...)       apiPost('/api/pin', ...)
#   apiFetch(`/api/catalogs?camera=`)   fetch(`${API_BASE}/api/clip`, ...)
#   new EventSource(`${API_BASE}/api/events`)
#   withCamera() appends ?camera= to a path that already names the endpoint, so
#   it never changes which route is hit.
API_LITERAL = re.compile(r"""(?<=[^\s])/api/[a-z0-9_-]+(?:/[a-z0-9_-]+)*""")
# The nearest enclosing call, and whether it is a write.
CALLS = (
    re.compile(r"\bapiPost\s*\("),
    re.compile(r"\bfetch\s*\("),
    re.compile(r"\bapiFetch\s*\("),
    re.compile(r"\bnew\s+EventSource\s*\("),
    re.compile(r"\bapiGet\s*\("),
)


def load_proxy():
    spec = importlib.util.spec_from_file_location("webcam_shot_proxy_coverage", PROXY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def spa_api_routes():
    """{path: {methods}} for every /api/* route index.html asks for.

    Derived by scanning the SPA source: every /api/ literal is attributed to
    its nearest enclosing fetch call, and a write is any call site that says
    method POST (apiPost, or fetch with an explicit method). The stub answers
    every POST with an ack, so a GET needs a real fixture route.
    """
    with open(INDEX, encoding="utf-8") as f:
        src = f.read()
    methods = {}
    for m in API_LITERAL.finditer(src):
        path = m.group(0)
        before = src[: m.start()]
        hits = [(mm.start(), c.pattern) for c in CALLS for mm in c.finditer(before)]
        if not hits:
            methods.setdefault(path, set())
            continue
        name = max(hits)[1]
        # A call site's own arguments: from the opening paren to the next
        # blank-line statement, which is far more than any one fetch needs.
        tail = src[m.end() : m.end() + 400]
        is_write = name == r"\bapiPost\s*\(" or re.search(
            r"method\s*:\s*['\"]POST['\"]", tail
        )
        methods.setdefault(path, set()).add("POST" if is_write else "GET")
    return methods


def app_taxonomy():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import taxonomy

        return taxonomy.payload()
    finally:
        if sys.path and sys.path[0] == HERE:
            sys.path.pop(0)


class _StubServerMixin:
    @classmethod
    def setUpClass(cls):
        cls.routes = spa_api_routes()
        cls.proxy = load_proxy()
        # The SSE stub pings every SSE_STUB_INTERVAL seconds; shorten it so the
        # stream assertion does not sit through the production 2s cadence.
        cls.proxy.SSE_STUB_INTERVAL = 0.05

    def _request(self, method, path, body=None, read=65536):
        srv = self.proxy.TS(("127.0.0.1", 0), self.proxy.H)
        threading.Thread(
            target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            headers = {"Content-Length": str(len(body or b""))}
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            # read1, not read: the SSE stub holds its response open forever with
            # no Content-Length, so read(amt) would block until the peer closed.
            payload = resp.read1(read) if read else b""
            out = (resp.status, dict(resp.getheaders()), payload)
            conn.close()
            return out
        finally:
            srv.shutdown()
            srv.server_close()

    def _get(self, path):
        return self._request("GET", path)


class TestStubApiCoverage(_StubServerMixin, unittest.TestCase):
    def test_derivation_finds_the_known_endpoints(self):
        # Guard the guard: a regex that stopped matching would make every
        # assertion below vacuously pass.
        for path in (
            "/api/cameras",
            "/api/catalogs",
            "/api/clip",
            "/api/delete",
            "/api/events",
            "/api/inference_log",
            "/api/integrations",
            "/api/llm-schema",
            "/api/pin",
            "/api/pins",
            "/api/settings",
            "/api/status",
            "/api/taxonomy",
        ):
            self.assertIn(path, self.routes, f"{path} not found by the index.html scan")
        self.assertIn("POST", self.routes["/api/pin"])
        self.assertIn("POST", self.routes["/api/delete"])
        self.assertIn("POST", self.routes["/api/clip"])
        self.assertIn("GET", self.routes["/api/taxonomy"])
        self.assertIn("GET", self.routes["/api/cameras"])

    def test_stub_serves_every_spa_get_route(self):
        missing = []
        for path, methods in sorted(self.routes.items()):
            if "GET" not in methods and path not in STREAM_ROUTES:
                continue  # write-only endpoint; the stub's POST ack covers it
            if path in STREAM_ROUTES:
                status, headers, first = self._request("GET", path)
                self.assertEqual(status, 200, path)
                self.assertIn("text/event-stream", headers.get("Content-Type", ""), path)
                self.assertIn(b"event: ping", first, path)
                continue
            status, headers, body = self._get(path)
            if status != 200:
                missing.append(f"{path} -> HTTP {status}")
                continue
            self.assertIn("application/json", headers.get("Content-Type", ""), path)
            if path != "/api/llm-schema":  # a JSON *schema*, not a JSON value
                self.assertIsInstance(json.loads(body), (list, dict), path)
        self.assertEqual(missing, [], f"stub does not serve SPA routes: {missing}")

    def test_stub_write_routes_ack(self):
        for path, methods in sorted(self.routes.items()):
            if "POST" not in methods:
                continue
            status, _, body = self._request("POST", path, body=b'{"filename":"x.jpg"}')
            self.assertEqual(status, 200, path)
            self.assertIn(b'"ok":true', body, path)

    def test_live_allowlist_covers_every_spa_route(self):
        allow = self.proxy.LIVE_API_ALLOWLIST
        gaps = sorted(p for p in self.routes if p not in allow and p not in LIVE_REFUSED)
        self.assertEqual(gaps, [], f"SPA routes missing from LIVE_API_ALLOWLIST: {gaps}")

    def test_stub_taxonomy_is_the_apps(self):
        status, _, body = self._get("/api/taxonomy")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), app_taxonomy())

    def test_stub_cameras_match_the_gallery(self):
        status, _, body = self._get("/api/cameras")
        self.assertEqual(status, 200)
        cams = json.loads(body)
        self.assertEqual([c["id"] for c in cams], ["Webcam21", "Webcam22"])
        for cam in cams:
            self.assertEqual(set(cam), {"id", "kind", "label", "source_dir", "index"})
            self.assertNotIn("/mnt/models", cam["source_dir"])

    def test_stub_catalogs_match_the_gallery(self):
        status, _, body = self._get("/api/catalogs")
        self.assertEqual(status, 200)
        every = json.loads(body)
        self.assertEqual(sorted(every), ["Webcam21", "Webcam22"])
        status, _, scoped = self._get("/api/catalogs?camera=Webcam21")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(scoped), every["Webcam21"])
        self.assertTrue(
            all(n.startswith("10.0.0.21_") for n in every["Webcam21"]["images"])
        )
        self.assertEqual(self._get("/api/catalogs?camera=nope")[0], 400)


class TestDemoBundleApiCoverage(unittest.TestCase):
    """#66's real victim: the published static demo, not just the stub."""

    @classmethod
    def setUpClass(cls):
        cls.routes = spa_api_routes()
        demo = os.path.join(HERE, "tools", "demo")
        if demo not in sys.path:
            sys.path.insert(0, demo)
        try:
            from build_demo import network_payload
        finally:
            if demo in sys.path:
                sys.path.remove(demo)
        cls.static = network_payload()["static"]

    def test_demo_table_covers_every_spa_get_route(self):
        # A route the SPA only POSTs needs no fixture entry: the shim acks every
        # non-GET with a "won't persist" toast. /api/events is the shim's canned
        # ping. Everything the SPA GETs must be in the static table.
        gaps = sorted(
            p
            for p, methods in self.routes.items()
            if p not in STREAM_ROUTES
            and (methods - {"POST"})
            and p not in self.static
        )
        self.assertEqual(gaps, [], f"demo bundle unmocked: {gaps}")

    def test_demo_taxonomy_is_the_apps(self):
        self.assertIn("/api/taxonomy", self.static)
        self.assertEqual(self.static["/api/taxonomy"]["body"], app_taxonomy())

    def test_demo_cameras_and_catalogs_agree(self):
        cams = self.static["/api/cameras"]["body"]
        cats = self.static["/api/catalogs"]["body"]
        self.assertEqual(sorted(cats), sorted(c["id"] for c in cams))
        for cam in cams:
            scoped = f"/api/catalogs?camera={cam['id']}"
            self.assertIn(scoped, self.static)
            self.assertEqual(self.static[scoped]["body"], cats[cam["id"]])
            self.assertTrue(cats[cam["id"]]["images"])
        self.assertIn("/api/catalogs?camera=all", self.static)


if __name__ == "__main__":
    unittest.main()
