"""The CSRF origin gate must refuse *cross*-origin writes and nothing else (#51).

PR #41 added `Handler._mutation_refusal()` to close a real hole: a
CORS-safelisted cross-origin POST (text/plain, urlencoded, multipart,
sendBeacon) needs no preflight and no token, so any page a user visited could
rewrite settings. It shipped a check that consulted *only* the CORS allowlist --
but every browser POST carries an `Origin`, including same-origin ones, and the
default allowlist listed neither documented access path. The result was a
production-breaking regression: pin/delete/settings/integrations/clip all 403'd
with a bare `API 403` in the UI, while the CSRF protection it was added for
kept working.

These tests pin both halves of that contract:

  * same-origin (Origin == Host, or the trusted X-Forwarded-Host from the
    reverse proxy) is allowed, and the write lands;
  * a cross-origin simple request from an unlisted origin is still refused with
    no side effect, `Origin: null` is refused, and a spoofed X-Forwarded-Host
    from a direct loopback caller does not lift the allowlist.

The gate and the CORS response headers share one decision
(`api_server.origin_allowed`), so the preflight answer and the mutation answer
can never disagree.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_server  # noqa: E402

# The documented reverse proxy (nginx.conf:57-70) forwards `Host: dogcam.lan`
# to the API, and the SPA is served from https://dogcam.lan -- so this is a
# SAME-origin write that #41 refused.
PROXY_HOST = "dogcam.lan"
PROXY_ORIGIN = "https://dogcam.lan"
EVIL = "https://evil.example"


class OriginCase(unittest.TestCase):
    """Camera roots, config and pins in a temp dir; never /mnt/models."""

    GLOBALS = ("WATCH_DIRS", "BASE_DIR", "SETTINGS_FILE", "INTEGRATIONS_FILE",
               "PINS_FILE")
    ENV = ("WEBCAM_CORS_ORIGIN", "WEBCAM_API_TOKEN")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="webcam_origin_")
        self.front = os.path.join(self.tmp, "front")
        os.makedirs(self.front)
        self.settings_path = os.path.join(self.tmp, "settings.json")
        self.integrations_path = os.path.join(self.tmp, "integrations.json")
        self._frame = 0
        self._orig = {g: getattr(api_server, g) for g in self.GLOBALS}
        self._orig_env = {k: os.environ.pop(k, None) for k in self.ENV}
        api_server.WATCH_DIRS = [self.front]
        api_server.BASE_DIR = self.tmp
        api_server.SETTINGS_FILE = self.settings_path
        api_server.INTEGRATIONS_FILE = self.integrations_path
        api_server.PINS_FILE = os.path.join(self.tmp, "pins.json")
        # The default deployment: no auth, loopback bind, one allowlisted origin.
        os.environ["WEBCAM_CORS_ORIGIN"] = "http://localhost:8180"
        self._n = 0

    def new_frame(self):
        """A fresh, on-disk frame, so a landed pin is observable."""
        self._frame += 1
        name = f"f{self._frame}.jpg"
        with open(os.path.join(self.front, name), "w") as f:
            f.write("x")
        return name

    def tearDown(self):
        for g, v in self._orig.items():
            setattr(api_server, g, v)
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_settings(self, **cfg):
        with open(self.settings_path, "w") as f:
            json.dump(cfg, f)
        return self.settings_path

    def snapshot(self):
        """Every observable side effect of a mutation: settings, integrations,
        and the per-camera pins file (pins are written under the camera dir,
        not BASE_DIR)."""
        out = {"settings": self._read(self.settings_path),
               "integrations": self._read(self.integrations_path),
               "pins": self._read(os.path.join(self.front, "pins.json")),
               "files": sorted(os.listdir(self.front))}
        return out

    @staticmethod
    def _read(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def post(self, path="/api/pin?camera=front", *, origin=None, peer="127.0.0.1",
             ctype="application/json", payload=None, headers=None):
        """Drive one POST through the real gate and return (code, body)."""
        self._n += 1
        payload = dict(payload or {})
        if "filename" not in payload and path.split("?")[0].rsplit("/", 1)[-1] in (
                "pin", "delete"):
            payload["filename"] = self.new_frame()
            if path.startswith("/api/pin"):
                payload.setdefault("pinned", True)
        if path == "/api/settings":
            payload.setdefault("deep_backfill", self._n % 2 == 0)
        hdrs = {"Host": f"127.0.0.1:{api_server.PORT}"}
        if origin is not None:
            hdrs["Origin"] = origin
        if ctype:
            hdrs["Content-Type"] = ctype
        hdrs.update(headers or {})

        h = api_server.Handler.__new__(api_server.Handler)
        raw = json.dumps(payload).encode()
        h.headers = dict(hdrs, **{"Content-Length": str(len(raw))})
        h.client_address = (peer, 51234)
        h.path = path
        h.rfile = io.BytesIO(raw)
        h.wfile = io.BytesIO()
        h.sent = []

        def _send(code, body):
            h.sent.append((code, body))

        h._send = _send
        h.do_POST()
        return h.sent[-1] if h.sent else (None, None)

    def assert_refused(self, path="/api/pin?camera=front", **kw):
        """403 from the gate, and nothing on disk changed.

        Any frame the call needs is created *before* the snapshot, so the only
        thing that can differ afterwards is a real side effect."""
        payload = dict(kw.pop("payload", None) or {})
        if "filename" not in payload and path.split("?")[0].rsplit("/", 1)[-1] in (
                "pin", "delete"):
            payload["filename"] = self.new_frame()
            if path.startswith("/api/pin"):
                payload.setdefault("pinned", True)
        before = self.snapshot()
        code, body = self.post(path, payload=payload, **kw)
        self.assertEqual(code, 403, msg=f"expected a refusal, got {code} {body}")
        self.assertEqual(body.get("error"), "origin not allowed")
        self.assertEqual(self.snapshot(), before, "a refused write had a side effect")


class TestSameOriginIsAllowed(OriginCase):
    """The regression: same-origin is not cross-origin."""

    def test_origin_equal_to_host_is_allowed_and_lands(self):
        # (a) required. The SPA and the API on one authority.
        origin = f"http://127.0.0.1:{api_server.PORT}"
        code, body = self.post(origin=origin)
        self.assertEqual(code, 200, body)
        self.assertTrue(
            self._read(os.path.join(self.front, "pins.json")),
            "the pin did not persist")

    def test_same_origin_behind_the_reverse_proxy_is_allowed_and_lands(self):
        # (b) required, and the documented production path: nginx.conf proxies
        # /api/ to :8190 with `Host: dogcam.lan`, the SPA is https://dogcam.lan.
        code, body = self.post(origin=PROXY_ORIGIN,
                               headers={"Host": PROXY_HOST})
        self.assertEqual(code, 200, body)
        self.assertTrue(
            self._read(os.path.join(self.front, "pins.json")),
            "the pin did not persist")

    def test_trusted_forwarded_host_from_a_loopback_proxy_is_allowed(self):
        # (b) required. `Host: $host` in nginx drops the port, so on a proxy
        # listening on a non-default port the forwarded host is the only place
        # :8443 survives -- without this the documented deployment 403s again.
        code, body = self.post(
            origin="https://dogcam.lan:8443",
            headers={"Host": PROXY_HOST, "X-Forwarded-Host": "dogcam.lan:8443"})
        self.assertEqual(code, 200, body)
        self.assertTrue(
            self._read(os.path.join(self.front, "pins.json")),
            "the pin did not persist")

    def test_shipped_default_allowlist_needs_no_entry_for_same_origin(self):
        # The regression in its purest form: the operator changed nothing, so
        # the allowlist is the stock one and the write must still land.
        self.assertEqual(api_server.cors_origins(), ["http://localhost:8180"])
        code, body = self.post(origin=PROXY_ORIGIN, headers={"Host": PROXY_HOST})
        self.assertEqual(code, 200, body)

    def test_same_origin_write_survives_an_empty_allowlist(self):
        # `WEBCAM_CORS_ORIGIN='*'` resolves to nothing, which #41 turned into a
        # total browser write outage. Same-origin is not on that list and never
        # was, so it must not depend on it.
        os.environ["WEBCAM_CORS_ORIGIN"] = "*"
        self.assertEqual(api_server.cors_origins(), [])
        code, body = self.post(origin=PROXY_ORIGIN, headers={"Host": PROXY_HOST})
        self.assertEqual(code, 200, body)

    def test_same_origin_keeps_the_415_on_a_non_json_body(self):
        # The content-type half of the gate is independent of the origin half
        # and must not be relaxed along with it: a same-origin page still has
        # to say application/json, so a CORS-safelisted body is never accepted.
        code, body = self.post(origin=PROXY_ORIGIN, ctype="text/plain",
                               headers={"Host": PROXY_HOST})
        self.assertEqual(code, 415, body)
        self.assertEqual(body.get("error"),
                         "content-type must be application/json")

    def test_origin_scheme_is_not_compared_against_the_socket(self):
        # Behind TLS termination the API only sees a plaintext socket, so it
        # cannot know the browser used https. Matching the authority is what
        # makes the reverse-proxy deployment work at all.
        self.assertTrue(api_server.same_origin(
            "https://dogcam.lan", ["dogcam.lan"]))
        self.assertTrue(api_server.same_origin(
            "https://dogcam.lan:8443", ["dogcam.lan:8443"]))
        # ...but a different authority is still a different origin.
        self.assertFalse(api_server.same_origin(
            "https://evil.example", ["dogcam.lan"]))

    def test_origin_comparison_ignores_case_and_trailing_slash(self):
        self.assertTrue(api_server.same_origin(
            "https://DOGCAM.lan", ["dogcam.lan"]))
        self.assertTrue(api_server.same_origin(
            "https://dogcam.lan/", ["dogcam.lan"]))
        # A comma-joined forwarded-host list must not match its first element.
        self.assertFalse(api_server.same_origin(
            "https://dogcam.lan", ["dogcam.lan, evil.example"]))


class TestCrossOriginStillRefused(OriginCase):
    """#24's protection must survive the fix intact."""

    def test_text_plain_simple_post_refused(self):
        # (c) required. CORS-safelisted: no preflight, so nothing but this
        # gate stands between a visited page and a settings rewrite.
        self.assert_refused(path="/api/settings", origin=EVIL, ctype="text/plain")

    def test_urlencoded_form_post_refused(self):
        self.assert_refused(path="/api/settings", origin=EVIL,
                            ctype="application/x-www-form-urlencoded")

    def test_multipart_post_refused(self):
        # sendBeacon's content type.
        self.assert_refused(path="/api/settings", origin=EVIL,
                            ctype="multipart/form-data; boundary=x")

    def test_cross_origin_delete_refused_with_no_side_effect(self):
        # A refused delete must not have removed anything.
        self.assert_refused(path="/api/delete?camera=front", origin=EVIL,
                            ctype="text/plain")

    def test_cross_origin_integration_write_refused(self):
        self.assert_refused(path="/api/integrations", origin=EVIL,
                            payload={"slack": {"bot_token": "xoxb-attacker"}})

    def test_lan_origin_refused_until_the_operator_lists_it(self):
        # The LAN/dev shape: the SPA is served by a camera container and points
        # cross-origin at :8190, so the origin is genuinely not ours and must
        # not be inferred from anything about the socket. Loopback peer and
        # stock allowlist, one unlisted LAN origin -> refused.
        self.assert_refused(path="/api/settings",
                            origin="http://192.168.1.50:8180")

    def test_an_allowlisted_origin_is_still_content_type_gated(self):
        # Being on the allowlist buys an origin, not a bypass: a CORS-safelisted
        # body from a *listed* origin still 415s, so it still needs a preflight.
        code, body = self.post(path="/api/settings",
                               origin="http://localhost:8180", ctype="text/plain")
        self.assertEqual(code, 415, body)

    def test_lan_origin_is_allowed_once_configured(self):
        # The companion half: the LAN/dev path is genuinely cross-origin, so it
        # needs an allowlist entry -- and one entry is enough.
        os.environ["WEBCAM_CORS_ORIGIN"] = (
            "http://localhost:8180,http://192.168.1.50:8180")
        code, body = self.post(origin="http://192.168.1.50:8180")
        self.assertEqual(code, 200, body)
        self.assertTrue(
            self._read(os.path.join(self.front, "pins.json")),
            "the pin did not persist")

    def test_origin_null_refused(self):
        # (d) required. `null` is what a sandboxed iframe, a file:// page and a
        # cross-document redirect all send. It is not the operator's gallery, and
        # it must not be a way in -- including via the allowlist, which is why
        # cors_origins() drops it.
        self.assert_refused(origin="null")
        os.environ["WEBCAM_CORS_ORIGIN"] = "http://localhost:8180,null"
        self.assertNotIn("null", api_server.cors_origins())
        self.assert_refused(origin="null")

    def test_wildcard_allowlist_still_refuses_everything_else(self):
        # `*` must not become a usable allow-all: it is dropped, so a
        # cross-origin write is still refused and the operator's 'just allow
        # everything' reflex cannot re-open #24.
        os.environ["WEBCAM_CORS_ORIGIN"] = "*"
        self.assertEqual(api_server.cors_origins(), [])
        self.assert_refused(origin=EVIL)

    def test_refusal_is_logged_not_silent(self):
        # The reported symptom was a bare `API 403` in the UI with nothing on
        # the host, so an operator could not tell a config mistake from a bug.
        with self.assertLogs("api_server", level="WARNING") as logs:
            self.post(origin=EVIL)
        joined = "\n".join(logs.output)
        self.assertIn("origin", joined.lower())
        self.assertIn("WEBCAM_CORS_ORIGIN", joined)

    def test_preflight_from_an_unlisted_origin_gets_no_cors_grant(self):
        # The preflight answer and the mutation answer come from the same
        # origin_allowed(); if they disagreed, one of the two is a bypass.
        h = api_server.Handler.__new__(api_server.Handler)
        h.headers = {"Origin": EVIL}
        h.client_address = ("127.0.0.1", 51234)
        sent = []
        h.send_response = lambda *a: None
        h.send_header = lambda k, v: sent.append((k, v))
        h.end_headers = lambda: None
        h._cors()
        self.assertEqual([k for k, _ in sent if k.startswith("Access-Control")],
                         [])

    def test_preflight_for_a_same_origin_write_is_granted(self):
        h = api_server.Handler.__new__(api_server.Handler)
        h.headers = {"Origin": PROXY_ORIGIN, "Host": PROXY_HOST}
        h.client_address = ("127.0.0.1", 51234)
        sent = []
        h.send_response = lambda *a: None
        h.send_header = lambda k, v: sent.append((k, v))
        h.end_headers = lambda: None
        h._cors()
        self.assertIn(("Access-Control-Allow-Origin", PROXY_ORIGIN), sent)

    def test_no_origin_is_unaffected(self):
        # curl and tools/watchdog.sh send no Origin and are not a browser
        # threat model; the gate must not touch them.
        code, body = self.post(origin=None)
        self.assertEqual(code, 200, body)


class TestForwardedHostTrustBoundary(OriginCase):
    """X-Forwarded-Host is attacker-controlled, so it is read only when the
    request has the shape of our own reverse proxy.

    The property that makes this safe to ship: reading X-Forwarded-Host is
    gated on conditions that a caller must already satisfy *without* it --
    a loopback socket peer, and a Host that does not name loopback. Forging
    Host to get the header read is itself enough to satisfy the same-origin
    test, so the header adds no reachable attack surface. It is a way to
    recover the port a port-stripping `Host: $host` lost, not a way in.
    """

    def test_spoofed_forwarded_host_from_a_direct_loopback_caller_refused(self):
        # (e) required. A direct caller sends `Host: 127.0.0.1:8190`; that is
        # loopback, so the forwarded header is not read and the allowlist
        # stands.
        self.assert_refused(path="/api/settings", origin=EVIL,
                            headers={"X-Forwarded-Host": EVIL.split("://")[1]})

    def test_spoofed_forwarded_host_from_a_remote_peer_refused(self):
        # Not our proxy, so the header is not read, whatever the Host says.
        self.assert_refused(path="/api/settings", origin=EVIL, peer="192.0.2.9",
                            headers={"Host": PROXY_HOST,
                                     "X-Forwarded-Host": "evil.example"})

    def test_forwarded_host_ignored_when_host_already_names_loopback(self):
        # The narrow condition, asserted directly rather than through a write.
        h = api_server.Handler.__new__(api_server.Handler)
        h.headers = {"Host": f"localhost:{api_server.PORT}",
                     "X-Forwarded-Host": PROXY_HOST}
        h.client_address = ("127.0.0.1", 51234)
        self.assertEqual(h._request_hosts(),
                         [f"localhost:{api_server.PORT}"])

    def test_forwarded_host_ignored_without_a_loopback_peer(self):
        h = api_server.Handler.__new__(api_server.Handler)
        h.headers = {"Host": PROXY_HOST, "X-Forwarded-Host": PROXY_HOST}
        h.client_address = ("198.51.100.7", 51234)
        self.assertEqual(h._request_hosts(), [PROXY_HOST])

    def test_forwarded_host_ignored_when_the_peer_is_unknown(self):
        # Fails closed: a handler with no real socket is not a trusted proxy.
        h = api_server.Handler.__new__(api_server.Handler)
        h.headers = {"Host": PROXY_HOST, "X-Forwarded-Host": PROXY_HOST}
        self.assertFalse(hasattr(h, "client_address"))
        self.assertEqual(h._request_hosts(), [PROXY_HOST])

    def test_loopback_authority_recognised_in_every_spelling(self):
        for authority in ("localhost:8190", "127.0.0.1:8190", "127.0.0.1",
                          "LOCALHOST", "app.localhost:8190", "[::1]:8190",
                          "[::1]", "127.0.0.53"):
            self.assertTrue(api_server._is_loopback_authority(authority),
                            msg=authority)
        for authority in ("dogcam.lan", "192.168.1.50:8190", "notlocalhost",
                          "localhost.evil.example", ""):
            self.assertFalse(api_server._is_loopback_authority(authority),
                             msg=authority)


class TestCorsConfigurationIsShippableAndLoud(OriginCase):
    """Requirement 2 and 3: the variable has to be configurable, and a
    configuration that cannot work has to say so."""

    def test_configured_origins_are_normalized(self):
        # A trailing slash or upper case in the env must not become a silent
        # 403: a browser Origin is always lowercase and never ends in '/'.
        os.environ["WEBCAM_CORS_ORIGIN"] = (
            "https://Dogcam.Lan/ , http://192.168.1.50:8180")
        self.assertEqual(api_server.cors_origins(),
                         ["https://dogcam.lan", "http://192.168.1.50:8180"])
        code, body = self.post(origin="https://dogcam.lan")
        self.assertEqual(code, 200, body)

    def test_empty_allowlist_warns_at_startup(self):
        # (f) required.
        os.environ["WEBCAM_CORS_ORIGIN"] = ""
        out = io.StringIO()
        with self.assertLogs("api_server", level="WARNING"):
            api_server.report_cors_configuration(stream=out.write)
        self.assertIn("EMPTY", out.getvalue())
        self.assertIn("WEBCAM_CORS_ORIGIN", out.getvalue())

    def test_wildcard_allowlist_warns_at_startup(self):
        # (f) required. The reported "just allow everything" upgrade, which
        # resolved to an empty list and 403'd every browser write with no log
        # line anywhere.
        os.environ["WEBCAM_CORS_ORIGIN"] = "*"
        out = io.StringIO()
        with self.assertLogs("api_server", level="WARNING"):
            api_server.report_cors_configuration(stream=out.write)
        logged = out.getvalue()
        self.assertIn("EMPTY", logged)
        self.assertIn("'*' is never allowed", logged)

    def test_malformed_entry_warns_at_startup(self):
        # (f) required: a bare hostname or a path glob can never match an
        # Origin, and the operator would otherwise have no way to know.
        os.environ["WEBCAM_CORS_ORIGIN"] = "localhost:8180,http://x.example/*"
        out = io.StringIO()
        with self.assertLogs("api_server", level="WARNING"):
            api_server.report_cors_configuration(stream=out.write)
        logged = out.getvalue()
        self.assertIn("localhost:8180", logged)
        self.assertIn("scheme://host[:port]", logged)
        self.assertIn("http://x.example/*", logged)

    def test_startup_reports_a_usable_allowlist_without_warnings(self):
        os.environ["WEBCAM_CORS_ORIGIN"] = "http://localhost:8180"
        self.assertEqual(api_server.cors_origin_problems(), [])
        out = io.StringIO()
        api_server.report_cors_configuration(stream=out.write)
        self.assertIn("http://localhost:8180", out.getvalue())
        self.assertNotIn("WARNING", out.getvalue())

    def test_env_example_ships_the_variable(self):
        # The variable was documented in prose and present in no install path,
        # which is what made the default so hard to escape from.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("systemd/webcam.env.example", "systemd/install.sh"):
            with open(os.path.join(root, name)) as f:
                text = f.read()
            self.assertIn("WEBCAM_CORS_ORIGIN", text, msg=name)
        # ...and install.sh must give it a working default, not an empty string
        # (which resolves to no origins at all).
        with open(os.path.join(root, "systemd/install.sh")) as f:
            install = f.read()
        self.assertIn('WEBCAM_CORS_ORIGIN="${WEBCAM_CORS_ORIGIN:-http://',
                      install)

    def test_origin_allowed_is_the_single_shared_decision(self):
        # The gate and the CORS headers read the same function, so they cannot
        # disagree about what is allowed.
        self.assertTrue(api_server.origin_allowed(PROXY_ORIGIN, [PROXY_HOST]))
        self.assertTrue(api_server.origin_allowed("http://localhost:8180"))
        self.assertFalse(api_server.origin_allowed(EVIL, [PROXY_HOST]))
        self.assertFalse(api_server.origin_allowed("null", [PROXY_HOST]))
        # No Origin is not a browser write.
        self.assertTrue(api_server.origin_allowed("", []))


if __name__ == "__main__":
    unittest.main()
