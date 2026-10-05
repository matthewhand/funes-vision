# API reference

The host write-API (`api_server.py`, port **8190**) — a stdlib
`ThreadingHTTPServer`, the single write channel (the gallery itself is served by
read-only nginx mounts). All responses are JSON (`Content-Type:
application/json`) except the SSE stream. `OPTIONS` on any path returns `204`.

**CORS is an origin allowlist, never a wildcard.** A request is granted
`Access-Control-Allow-Origin` (and allowed to `POST`) when its `Origin` is
either **same-origin** — the authority in `Origin` matches the `Host` the
request was addressed to, or the trusted `X-Forwarded-Host` — or listed in
`WEBCAM_CORS_ORIGIN` (comma-separated, default
`http://localhost:8180,http://127.0.0.1:8180`). Every browser `POST` carries an
`Origin`, so the same-origin case is what keeps the normal reverse-proxy
deployment (`nginx.conf` proxies `/api/`, SPA and API on one host) working
without any allowlist entry; the list is only needed when the gallery is
served from a *different* origin, e.g. opening a camera container directly at
`http://<host>:8180`. A request with no `Origin` (curl, `tools/watchdog.sh`) is
not a browser threat model and is unaffected.

`X-Forwarded-Host` is read **only** when the socket peer is loopback *and* the
`Host` does not itself name loopback — the shape our own reverse proxy leaves
behind, since it rewrites `Host` to the public vhost. A direct loopback caller
sends `Host: 127.0.0.1:8190`, so a spoofed forwarded header is ignored. Forging
`Host` as well is no new attack surface: that alone already satisfies the
same-origin test, and a local caller could omit `Origin` altogether.

Configured origins are lowercased and stripped of a trailing `/` (an `Origin`
header is always lowercase and never ends in `/`). A literal `*` or `null` is
**dropped** — the wildcard let any page a user visited POST deletes at the LAN
host, and `null` is what a sandboxed iframe or `file://` page sends. Startup
prints the effective allowlist and warns when it resolves empty or holds an
entry that is not `scheme://host[:port]`, because an unusable list 403s every
browser write while curl keeps working, with no other symptom (#51). Allowed
responses also carry `Vary: Origin` and `Access-Control-Allow-Credentials: true`.

**Bind.** `WEBCAM_API_HOST` (default `127.0.0.1`) is the only exposure control;
the startup line prints `auth=token`, `auth=off` or `auth=broken` (see
[Authentication](#authentication)) so an operator can see at a glance whether the
write API is gated. It is read **once at import**, so it needs
`sudo systemctl restart webcam-api`.

**Request limits.** Two per-connection resource guards (a 30 s socket timeout
and a 1 MiB `POST` body cap that answers **413**) and the `/api/status` +
`/api/health` probe cache are all env-tunable — see
[Request limits](#request-limits) below. The full API environment surface is
tabulated in [DEVELOP.md](DEVELOP.md#api_serverpy-port-8190).

## Authentication

Auth is **opt-in and off by default**, which is why the loopback bind is the
default. `_authorized()` gates **every `POST`** (`/api/pin`, `/api/delete`,
`/api/clip`, `/api/settings`, `/api/integrations`, `/api/integrations/test`)
before any body is parsed; `GET` stays open so the read-only gallery keeps
working without credentials.

| Source | Precedence |
|--------|------------|
| `WEBCAM_API_TOKEN` env var | wins when set and non-blank |
| `api_token` key in `settings.json` | fallback (keeps the secret beside the rest of the config) |
| neither set | auth disabled — `_authorized()` returns `True` for everything |

With no token configured, POSTs are open. With a token configured, supply it as
either:

- `Authorization: Bearer` followed by the token, or
- HTTP Basic with the secret as **either** the username or the password, so
  both `curl -u :SECRET` and `curl -u SECRET:` work.

Comparison is constant-time (`hmac.compare_digest`). A missing, malformed, or
wrong credential is **401** `{"error": "unauthorized"}`; `OPTIONS` is not gated
(preflight must succeed for the browser to send the real request). The gallery
SPA and nginx are a second, independent layer — see
[SECURITY.md](SECURITY.md) and [DEPLOYMENT.md](DEPLOYMENT.md).

**A blank configured token fails closed, it is not "no auth".** A whitespace-only
`WEBCAM_API_TOKEN` (an unset `Environment=`, an unexpanded `${VAR}`) is stripped
and falls through to `settings.json`. But an `api_token` key in `settings.json`
that is *present and blank* — somebody meant to configure auth and shipped an
empty value — raises at startup: the banner prints `auth=broken`, the error
explains why, and the process exits **1**, so writes stay closed until it is
fixed. If a configured token later goes blank under a live process, `_authorized()`
answers **500** `{"error": "api_token is configured but blank; ..."}` rather than
letting the write through — and that is the **only** response on the connection.
The startup banner has three states, not two:
`auth=token`, `auth=off`, `auth=broken`.

In production the UI reaches this **same-origin at `/api/`** via the reverse
proxy (basic-auth, TLS); it falls back to `http://<host>:8190` only when a page
is opened directly on the camera container (`:8180`, which serves both the
front feed at `/` and the dogcam feed at `/Webcam22/`). See
[DEPLOYMENT.md](DEPLOYMENT.md).

Facts here are verified against `api_server.py`. If they drift, the code wins —
please update this file in the same change.

## Request limits

Three per-connection resource guards plus one probe cache. All four are
env-tunable (no code change to tighten them) and all four are read **per
request / per call**, not latched at startup — so an edit to the systemd
`Environment=` takes effect on the next request, with no restart.

| Env | Default | Unit | Read | Governs |
|-----|---------|------|------|---------|
| `WEBCAM_API_SOCKET_TIMEOUT` | `30` | seconds | per connection | Read timeout on one client socket |
| `WEBCAM_API_MAX_CONNECTIONS` | `64` | connections | per connection | Concurrent connections served at once (`0` disables) |
| `WEBCAM_API_MAX_BODY` | `1048576` | bytes (1 MiB) | per `POST` | Largest accepted `Content-Length` |
| `WEBCAM_PROBE_TTL` | `5` | seconds | per probe call | How long `/api/status` and `/api/health` reuse a probe answer |

**`WEBCAM_API_SOCKET_TIMEOUT`** — `ThreadingHTTPServer` runs one thread per
connection, so a peer that opened a socket and never finished its request line
would park a thread in `readline()` indefinitely, and a handful of partial
requests could starve `/api/health` — the endpoint the uptime monitor and the
cron watchdog both depend on. An idle `GET /api/events` SSE stream is
unaffected: that path only writes, so it never trips a read timeout. *Guidance:*
at the default, a client that takes more than 30 s to send its request is
dropped. Raise it only for a client on a genuinely slow link — a stalled peer is
exactly what this defends against. A value of `0` or less is **not** a short
timeout: it disables the read timeout entirely, because `settimeout(0)` is
non-blocking mode (the next read would raise rather than wait) and a negative
number raises `ValueError` inside `socketserver.setup()`, which would answer
*no* request at all. The server prints a startup warning when it is running
with no read timeout.

**`WEBCAM_API_MAX_CONNECTIONS`** — the thread-count ceiling. The socket timeout
above bounds a thread's *lifetime*; this bounds how many there are. Each
accepted connection takes a slot for its lifetime; past the ceiling the peer
gets **503** `{"error": "too many concurrent connections; retry later"}` and
the socket closes, so a burst has a bounded answer instead of one thread per
connection. *Guidance:* the default is deliberately far above ordinary use —
a browser opens at most 6 connections per host and a reverse proxy reuses one
upstream connection, so single-user use is 1–2 concurrent. Lower it only if
something on the box is opening connections it does not need; `0` restores the
uncapped behaviour. The refusal is deliberately not logged per connection: the
burst is the interesting case, and a log line per connection is itself the
flood. `GET /api/events` has its own, separate cap
(`WEBCAM_SSE_MAX_CLIENTS`).

**`WEBCAM_API_MAX_BODY`** — every `POST` validates the declared
`Content-Length` **before reading a byte of it**, so `Content-Length:
2000000000` cannot buffer 2 GB of RAM in a handler thread and `-1` (chunked)
cannot park the reader waiting for bytes that never arrive.

| `POST` condition | Response |
|------------------|----------|
| `Content-Length` is not an integer | **400** `{"error": "bad request"}` |
| `Content-Length` < 0, or > `WEBCAM_API_MAX_BODY` | **413** `{"error": "request body too large"}` |
| body is not valid JSON | **400** `{"error": "bad request"}` |

*Guidance:* at the default, 1 MiB is generous — every mutating endpoint is small
JSON — so leave it alone; raise it only if an endpoint starts carrying real
payload. The check runs **after** the cross-origin guard and after
`Authorization`, so an unauthenticated caller still gets **401** and a
cross-origin one still **403**/**415**: the cap never leaks whether a body
existed. (The `Content-Length` header itself is only a *claim* — the cap bounds
what is read, not what the caller asserts.)

**`WEBCAM_PROBE_TTL`** — `/api/status` and `/api/health` are unauthenticated and
polled hard (the SPA, `tools/watchdog.sh` and the uptime monitor all hit them).
An uncached call forks `pgrep` **and** opens a socket to Ollama's
`/api/version`, so 20 polls used to cost 20 forks and 20 connects on the
documented 4-core box. Both answers are stable for seconds, so they are
memoised in-process. Only `trigger.inotify_active` and `llm.reachable` (and
therefore `checks.inotify` / `checks.llm_reachable`) are cached —
`last_sweep_age_s`, `disk_space` and the queue counts are recomputed per call.
*Guidance:* at the default, 20 polls cost 2 probes and those two values may be
up to ~5 s stale. Set `0` to disable caching (every call re-probes — always
correct, and the reason `/api/health` can be slow while Ollama is down). The
cache is in-process and TTL-bounded; `reset_probe_cache()` drops it immediately
(tests, and ops after a pipeline restart).

## REST endpoints

### `GET /api/pins`
Sorted list of pinned filenames.
- **Query** `?camera=<id>` optional; omitted (or `all`) returns every camera's pins
- **200** → `["10.0.0.21_..._MOTDEC.jpg", ...]`
- **400** → `{"error": "unknown camera ..."}` if `camera` is not a known id

### `POST /api/pin`
Pin or unpin an image (pins are excluded from auto-cleanup).
Requires `?camera=<id>` (folder basename from `GET /api/cameras`). Writing without a camera is not supported.
- **Query** `?camera=<id>` **required**
- **Body** `{"filename": "<name>", "pinned": true|false}`
- **200** → `{"ok": true, "pinned": true|false}` (the resulting state)
- **404** → `{"error": "image not found"}` if the filename isn't in that camera's dir
- **400** → `{"error": "camera is required for this endpoint (known: ...)"}` if `camera` is missing
- **400** → `{"error": "unknown camera ..."}` if `camera` is not a known id
- **400** → `{"error": "bad request"}` on unparseable JSON

`filename` must be a bare basename (no path separators, no leading `.`); path
traversal is rejected.

**An unpin clears the name from every file that can pin it.** `<cam>/pins.json`
is the canonical file, but `analyze_images.retention_pins()` still unions it
with the pre-camera-scoping `<repo>/pins.json`, and on any upgraded install the
name is in both (both pre-#39 sync steps wrote the repo file into every camera
dir). A name present in both used to be un-unpinnable: the camera file was
emptied and the API answered `pinned: false`, but retention kept protecting the
frame from every sweep forever, while the UI showed it as unpinned. An unpin
now removes the entry from the legacy file and the camera's own, in that order,
so a crash in between leaves the frame pinned (failing toward "keep the frame",
never toward "silently delete one the operator believes they released"). A name
that only ever existed in the legacy file is removed there, without
materialising an empty `pins.json` in the camera dir.

Pin names whose frame is no longer in the camera dir are dropped on the next
write to that camera's `pins.json` — retention has nothing to protect for them
either, and without that the file only ever grew. The reclaim is deliberately
narrow: an unreadable or empty directory listing drops **nothing**, so a
vanished dir, a permissions problem or a filesystem hiccup can never wipe a pin
list, and every reclaimed name is logged.

### `POST /api/delete`
Permanently remove an image and its thumbnail, and unpin it.
Requires `?camera=<id>` (same as pin).
- **Query** `?camera=<id>` **required**
- **Body** `{"filename": "<name>"}`
- **200** → `{"ok": true}`
- **404** → `{"error": "image not found"}`
- **400** → `{"error": "camera is required for this endpoint (known: ...)"}` if `camera` is missing
- **400** → `{"error": "unknown camera ..."}` if `camera` is not a known id

### `GET /api/cameras`
Configured watch dirs. `id` is the folder basename (`Webcam21`, ...) — the value
`?camera=` resolves against. `kind` is `front`/`back` (settings ignore-regions
and HA schema), kept separate so renaming a folder does not break zones.
- **200** → `[{id, kind, label, source_dir, index}, ...]`

### `GET /api/catalogs`
One camera's `images` / `analysis` / `bursts` / `pins` plus `thumbUrl` (relative
path to the latest thumb, so a dashboard at `/` can load another camera's card).
Missing catalog files are empty (`[]` for images/pins, `{}` for analysis/bursts),
not errors. A camera that has never been swept is a valid empty payload.
- **Query** `?camera=<id>` optional; omitted (or `all`) returns every camera
  keyed by id
- **200** (scoped) → `{images, analysis, bursts, pins, thumbUrl}`
- **200** (all) → `{ "<id>": {images, analysis, bursts, pins, thumbUrl}, ... }`
- **400** → `{"error": "unknown camera ..."}` if `camera` is not a known id

### `GET /api/settings`
The mutable settings subset, plus a live `cameras` registry (not POST-able).
- **200** → `{"fast_pass_engine", "decision_backend", "deep_backfill", "deep_passes_enabled", "burst_summaries_enabled", "idle_sweep_seconds", "ignore_regions", "cameras"}`
  `cameras` is `[{id, kind, label, source_dir, index}, ...]` from the process's
  `watch_dirs` (folder basename = `id`; `kind` is front/back).

### `POST /api/settings`
Update one or more mutable settings (validated; others ignored).
- **Body** any subset of:
  - `fast_pass_engine` ∈ `"yolo" | "haar"`
  - `decision_backend` ∈ `"ollama" | "imajev"` — typed deep-pass decisions only; burst summaries remain on the Ollama/OpenRouter caption path
  - `deep_backfill` ∈ `true | false`
  - `deep_passes_enabled` ∈ `true | false`
  - `burst_summaries_enabled` ∈ `true | false`
  - `idle_sweep_seconds` ∈ integer `15..3600`
  - `ignore_regions` ∈ list of `{camera, polygon, labels?, enabled?, mode?, scan?, id?}`.
    `polygon` is 3–8 points in `[0,1]` image fractions (origin top-left).
    `mode=ignore` (default): YOLO drops a listed label when the box centre
    is inside an enabled polygon (parked-car bay). `mode=gate` + `scan=porch`:
    do **not** drop the person; skip the e2b porch question and set
    `porch_access` from whether the person centre sits in the polygon.
    Empty list = no mask.
- **200** → `{"ok": true, ...changed}`
- **400** → `{"error": "<key> must be <choices|range>"}` on an invalid value,
  or `{"error": "no recognized settings in payload"}` if nothing applied.
  Invalid `ignore_regions` uses a list-shape message (`camera`, `polygon[3+]`,
  `labels?`, `enabled?`, `mode?`, `scan?`, `id?`), not the min..max template.
- **409** → `{"error": "settings.json is unreadable; refusing to overwrite"}`
  if the file is present but corrupt; bytes on disk are unchanged.

### `GET /api/integrations`
**Redacted** integration config — presence of tokens, never their values.
- **200** → `{"slack": {"enabled", "has_bot_token", "has_app_token",
  "channel_id", "public_base_url", "notify_mode", "last_delivery"}}`
  Missing file or empty file → the same shape with Slack unset (`has_bot_token`
  false).
- **409** → `{"ok": false, "detail": "integrations.json is unreadable",
  "unreadable": true}` if the file is present, non-empty, and unparseable.
  No Slack defaults are invented (the UI must not show “not configured”).

### `POST /api/integrations`
Merge Slack settings into `integrations.json` (gitignored, mode 600). Blank
token fields preserve the stored secret (the redacted GET can't echo it back).
Only the `slack` object is written; existing `mqtt` / `ntfy` blocks are left
intact. If the file is missing, a slack-only object is created. If the file
exists but is unreadable, unparseable, whitespace-only, or not a JSON object,
the write is refused so MQTT/ntfy secrets are never truncated away.
- **Body** `{"slack": {"enabled"?, "bot_token"?, "app_token"?, "channel_id"?,
  "public_base_url"?, "notify_mode"?}}`
- **200** → `{"ok": true, ...redacted_integrations()}`
- **400** → `{"error": ...}` if `public_base_url` lacks an `http(s)://` scheme,
  `notify_mode` ∉ `context|objects|all`, or no recognized fields were sent
- **409** → `{"error": "...refusing to overwrite"}` if `integrations.json` is
  present but corrupt or unreadable (`IntegrationsUnreadable`). Bytes on disk
  are unchanged.
- **500** → `{"error": "could not write integrations: ..."}` on a write
  `OSError` after a successful read

### `POST /api/integrations/test`
Send a Slack test message using the stored config.
- **200** → `{"ok": true, "detail": "..."}` on success
- **400** → `{"ok": false, "detail": "<reason>"}` on failure

### `GET /api/status`
Live pipeline snapshot. Shape (keys may be absent if a source is unavailable):
- `watch_dirs`, `settings` (full settings.json, minus secrets at **every**
  depth — see [Redaction](#redaction) — including `max_age_days`,
  `max_dir_gb`, `persist_budget_pct`, even though those are not
  `POST /api/settings`-mutable)
- `trigger` → `{inotify_active, idle_sweep_seconds, last_sweep_age_s}`
  (`last_sweep_age_s` is seconds since `/tmp/webcam_analysis.lastrun`, or
  JSON `null` if the marker is missing; the UI treats null as “unknown”.
  The cron watchdog and `recent_sweep` health check use this)
- `llm` → `{model, reachable, allow_cloud}`
- `inference` → `{}` when idle, otherwise the live `inference_status.json`
  object plus `running_for_s` (the ℹ panel’s “Analyzing now” line)
- `queue` → `{images_on_disk, unanalyzed, unverified_partials,
  awaiting_backfill, llm_verified, deep_s_per_frame, deep_eta_s}`:
  - `unverified_partials` — `fast_pass == "partial"` **or** a legacy
    detector-only row (`{person: true}` with no `_llm` / skip / `fast_pass`)
  - `awaiting_backfill` — `fast_pass == "negative"` **or**
    `_llm_skip == "no_trigger"` (car-only skip). Counted even when idle
    backfill is off, so the number stays honest.
  - `llm_verified` — `_llm` is a **non-empty** dict and `_llm_skip` is
    absent. `{}` and missing `fast_pass` are not verdicts.
  - `deep_s_per_frame` / `deep_eta_s` — serial LLM budget: window
    average seconds (or 40) × priority count. Both `null` when deep
    passes are off. This is wall-clock honesty, not a promise.
- `cameras[]` → `{name, images, bytes, budget_pct, last_frame_age_s, stale}`
  (`budget_pct` = image+thumb bytes in that camera dir vs `max_dir_gb`; UI warns
  above ~85%; pipeline Slack alert and watchdog `auto` kick at ~90%)
- `filesystem` → `{free_gb, total_gb, used_pct}` (host volume for the data
  paths — not the per-camera budget), or `null` if `statvfs` failed
- `timezone` (resolved: `WEBCAM_TZ` env > settings > `Australia/Sydney`)
- `metrics` → inference rollup `{window_min, count, ok, failures, success_rate,
  local, cloud, avg_s, p95_s}` (just `{window_min, count:0}` when idle)
- `retention` → last event from `retention_log.json`, or `null`:
  `{ts, dir, count, bytes_freed}` (Unix seconds, camera basename, images
  removed, bytes freed). Written by `apply_retention` on full sweeps and
  `--retention-only` (cron watchdog).
- **200** on success. **400** `{"error": "unknown camera ..."}` if `?camera=`
  is present and is not a known id (`_camera_param` runs before the snapshot).
- `watch_dirs` and `cameras[]` come from **import-time** `WATCH_DIRS`. `settings`
  is re-read from disk each request, so the two can disagree until the API restarts
  after a `watch_dirs` edit.
- `trigger.inotify_active` and `llm.reachable` are **memoised** for
  `WEBCAM_PROBE_TTL` seconds (default 5; `0` = re-probe every call) — see
  [Request limits](#request-limits). Every other value in the snapshot is read
  fresh.
- `max_dir_gb` is read through the same guarded accessor the pipeline uses
  (`analyze_images.setting_num`), so a hostile value — `null`, `"5.0"`,
  `"abc"`, `{}` — leaves the default **5 GB** budget in place instead of
  raising. A non-positive budget means "no disk budget" (eviction skipped),
  never "delete until empty", and `cameras[].budget_pct` is then `null`.
- Every number in the response is **finite**. `json.dumps` would otherwise
  write a non-finite float as a bare `Infinity` / `NaN`, which `JSON.parse`
  rejects — so one hostile setting produced a `200` the browser could not read
  at all, and `tools/watchdog.sh`, which parses the same body, silently
  computed `MAX_BUDGET=0` and never reached its 90 %-budget retention kick.
  Non-finite values are serialized as `null` instead.

### Redaction

`GET /api/status` is **unauthenticated** — the gallery, the uptime monitor and
`tools/watchdog.sh` all read it — so it is the widest possible hole: a
serialized `api_token` hands every reader the key that authorizes deleting
frames, rewriting settings and swapping the Slack bot token. `settings` is
therefore filtered on the way out, and the filter is **recursive**: it walks
dicts and lists and applies the secret-key test at every level, so a nested
`integrations.slack.bot_token` or `servers[0].token` is dropped as thoroughly
as a top-level `api_token`. A key that names a secret takes its **whole
subtree** with it; a benign key keeps its non-secret children.

Key matching is case- and separator-insensitive: the key is lowercased and
stripped of non-alphanumerics before being tested, so `api_key`, `api-key`,
`API-Key` and `apiKey` are one word, and `privateKey` matches `privatekey`. A
key that *ends* in `key` (`ssh_key`, `openai_key`) is treated as a secret, since
that shape is otherwise open-ended. The cost is a false positive on a
non-secret key that happens to end in "key"; none of the settings keys the SPA
reads out of this endpoint does, and dropping one from a read-only view is not
a security regression.

The same treatment applies to every other `json.dumps` on the response path, so
no field can reintroduce a non-finite number by forgetting to sanitize itself.
`GET /api/integrations` is filtered separately and stays a fixed,
hand-written shape (presence of tokens, never their values).

### `GET /api/health`
Compact health for an external uptime monitor (and the cron watchdog).
- **200** → `{"status": "ok", "checks": {...}, "cameras": [...]}`
- **503** → same shape with `"status": "degraded"` — **body is still JSON**;
  clients must not treat HTTP 503 as “no response” (e.g. avoid bare `curl -f`
  if you need the checks object).
- **400** → `{"error": "unknown camera ..."}` if `?camera=` is present and unknown
  (same `_camera_param` gate as `/api/status`; monitors should omit `camera`).
- `checks` → `{inotify, llm_reachable, recent_sweep, disk_space}` (booleans;
  `llm_reachable` is skipped/true when deep passes are off;
  `recent_sweep` is true when `last_sweep_age_s` &lt; 1 h;
  `disk_space` is true when host free_gb &gt; 1.0)
- `checks.inotify` and `checks.llm_reachable` are derived from the same
  `WEBCAM_PROBE_TTL` probe cache as `/api/status` and can be up to one TTL
  stale; `recent_sweep` and `disk_space` are computed per call. `0` disables
  caching — see [Request limits](#request-limits).

### `GET /api/inference_log`
The 50 most recent LLM audit entries, newest first.
- **200** → `[{started, ok, duration_s, model, ...}, ...]`

### `POST /api/clip`
Build and download a visit/sequence as an animated clip, on the fly (reuses the
`integrations/media.py` builders). Read-only: each filename is validated against
the camera dirs (no traversal) and capped at 300; the builder samples ≤24 frames.
- **Body** `{"files": ["<name>", ...], "format": "gif" | "mp4", "width": <px>, "fps": <n>}`
  (`format` defaults to `gif`; `width` and `fps` are optional)
- **`width`** — downscale width in px, clamped to `160..960`; frames narrower
  than it are never upscaled. Omit to keep the builder default (480).
- **`fps`** — MP4 playback rate; clamped to `0.5..30`. Omit to derive it from
  the sampled cadence (median inter-frame gap).
- **Cadence** — frames are timed by the camera filename clock
  (`_YYYYMMDDHHMMSSmmm_`). GIF holds each frame for its real inter-frame gap,
  clamped to `80..4000 ms`, and falls back to `400 ms` when timestamps are
  absent/unparseable, so motion plays at true speed rather than the fixed
  flipbook rate.
- **200** → the clip bytes, `Content-Type: image/gif` or `video/mp4`,
  `Content-Disposition: attachment; filename="visit.gif"`
- **400** → `{"error": "files[] required"}`
- **404** → `{"error": "no valid frames for that selection"}`
- **500** → `{"error": "<fmt> build failed (ffmpeg/PIL available?)"}` (mp4 needs
  ffmpeg; gif needs Pillow)

---

## SSE event stream

### `GET /api/events`
A long-lived `text/event-stream` (sets `X-Accel-Buffering: no` so it survives
the proxy un-buffered). The UI subscribes once via `EventSource` and falls back
to polling if SSE is unsupported or the connection drops.

**Transport — `events.jsonl` is the primary bus.** The analyzer appends one JSON
line per pipeline event to `events.jsonl` (`pipeline_events.emit()`, fsynced,
rotated at 2 MB / 5000 writes) and the API tails it by **byte offset** with
`pipeline_events.iter_since()`. So `image.new`, `new-detection`,
`detection.preliminary` and `new-burst` are a genuine pipeline→API push with
sub-second latency, emitted as each frame is persisted rather than at the end of
a sweep. **Fallback:** `analysis.json` / `bursts.json` mtime diffing still runs
so catalogs written by an older pipeline that does not emit are not lost; the
tail loop runs every **1 s** with that bus and every **3 s** without it. The first
pass seeds state **silently** (no backlog blast); only subsequent changes emit.

**Heartbeat and idle close.** A named `event: ping` is sent every
`WEBCAM_SSE_HEARTBEAT_S` (default **15 s**, floored at 1 s) — a bare `: ping`
comment is invisible to `EventSource`, so the client can detect a silently
stalled connection and proxies stay unbuffered. A connection that sees no real
event for `WEBCAM_SSE_IDLE_TIMEOUT_S` (default 600 s) is reaped with
`event: close` and the socket ends; `0` disables the timeout.

**Client cap.** Concurrent streams are bounded by `WEBCAM_SSE_MAX_CLIENTS`
(default 8, `0` disables). Past the cap a new connection gets **503**
`{"error": "too many event streams; retry later", "limit": <n>}` instead of
another unbounded thread, so a page that reconnects in a loop cannot pin the box.
The reserved slot is always released, even on a write error. All three SSE
knobs are tabulated with when-to-change guidance in
[DEVELOP.md — api_server.py](DEVELOP.md#api_serverpy-port-8190).

**Events** (`event:` name + JSON `data:`):

| Event | Payload | Fires when |
|-------|---------|-----------|
| `image.new` | `{"file": "<name>"}` | The pipeline persisted a frame (`events.jsonl`); on the mtime fallback, a frame first appears in `analysis.json` (right after the fast pass). |
| `detection.preliminary` | `{"file": "<name>", "labels": ["car", ...]}` | A detector-only hit (`fast_pass` present **or** `_llm_skip == "no_trigger"` with ≥1 YOLO True key) that is not `is_llm_verified`. Labels are `YOLO_PRESENCE_KEYS` only. Suppressed once promoted to verified. The only live detections while deep passes are off. Car-only `{car: true, _llm_skip: "no_trigger"}` is preliminary. |
| `new-detection` | `{"file": "<name>", "labels": ["person", ...]}` | An image gains a successful LLM merge (`_llm` dict, no `_llm_skip`) with ≥1 true label. Absence of `fast_pass` is not a verdict. |
| `new-burst` | `{"id": "<burst-id>", "summary": "<text>"}` | A new burst/visit is written to `bursts.json`. |
| `ping` | `{}` | Heartbeat every ~15 s (`WEBCAM_SSE_HEARTBEAT_S`). |
| `close` | `{"reason": "idle_timeout"}` | No event traffic for `WEBCAM_SSE_IDLE_TIMEOUT_S`; the server closes the stream. Reconnect with `EventSource` as usual. |

### Not yet implemented

`analysis.llm` live token streaming ("AI is looking at this…" with the flags
typing in) requires a streaming per-image Ollama call; the pipeline→API push
itself is done (see Transport above) and the remaining work is incremental DOM
patching — see [ROADMAP.md](ROADMAP.md). `image.new` / `detection.preliminary` /
`new-detection` patch in-memory state. Only `new-burst` still does a
`loadData()` refetch.

### `GET /api/llm-schema`
Read-only prompt + front/back JSON schemas the pipeline sends to the vision
model. Used by the ℹ panel.
- **200** → `{prompt, schemas: {front_door, dog_cam}, ...}`
