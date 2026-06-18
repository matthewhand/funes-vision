# API reference

The host write-API (`api_server.py`, port **8190**) — a stdlib
`ThreadingHTTPServer`, the single write channel (the gallery itself is served by
read-only nginx mounts). All responses are JSON (`Content-Type:
application/json`) except the SSE stream. CORS is open (`Access-Control-Allow-Origin:
*`); `OPTIONS` on any path returns `204`.

In production the UI reaches this **same-origin at `/api/`** via the reverse
proxy (basic-auth, TLS); it falls back to `http://<host>:8190` only when a page
is opened directly on a camera container (`:8180` / `:8280`). See
[DEPLOYMENT.md](DEPLOYMENT.md).

Facts here are verified against `api_server.py`. If they drift, the code wins —
please update this file in the same change.

## REST endpoints

### `GET /api/pins`
Sorted list of pinned filenames.
- **200** → `["10.0.0.21_..._MOTDEC.jpg", ...]`

### `POST /api/pin`
Pin or unpin an image (pins are excluded from auto-cleanup).
- **Body** `{"filename": "<name>", "pinned": true|false}`
- **200** → `{"ok": true, "pinned": true|false}` (the resulting state)
- **404** → `{"error": "image not found"}` if the filename isn't in a camera dir
- **400** → `{"error": "bad request"}` on unparseable JSON

`filename` must be a bare basename (no path separators, no leading `.`); path
traversal is rejected.

### `POST /api/delete`
Permanently remove an image and its thumbnail, and unpin it.
- **Body** `{"filename": "<name>"}`
- **200** → `{"ok": true}`
- **404** → `{"error": "image not found"}`

### `GET /api/settings`
The mutable settings subset only.
- **200** → `{"fast_pass_engine", "deep_backfill", "deep_passes_enabled", "idle_sweep_seconds"}`

### `POST /api/settings`
Update one or more mutable settings (validated; others ignored).
- **Body** any subset of:
  - `fast_pass_engine` ∈ `"yolo" | "haar"`
  - `deep_backfill` ∈ `true | false`
  - `deep_passes_enabled` ∈ `true | false`
  - `idle_sweep_seconds` ∈ integer `15..3600`
- **200** → `{"ok": true, ...changed}`
- **400** → `{"error": "<key> must be <choices|range>"}` on an invalid value,
  or `{"error": "no recognized settings in payload"}` if nothing applied

### `GET /api/integrations`
**Redacted** integration config — presence of tokens, never their values.
- **200** → `{"slack": {"enabled", "has_bot_token", "has_app_token",
  "channel_id", "public_base_url", "notify_mode", "last_delivery"}}`

### `POST /api/integrations`
Merge Slack settings into `integrations.json` (gitignored, mode 600). Blank
token fields preserve the stored secret (the redacted GET can't echo it back).
- **Body** `{"slack": {"enabled"?, "bot_token"?, "app_token"?, "channel_id"?,
  "public_base_url"?, "notify_mode"?}}`
- **200** → `{"ok": true, ...redacted_integrations()}`
- **400** → `{"error": ...}` if `public_base_url` lacks an `http(s)://` scheme,
  `notify_mode` ∉ `context|objects|all`, or no recognized fields were sent

### `POST /api/integrations/test`
Send a Slack test message using the stored config.
- **200** → `{"ok": true, "detail": "..."}` on success
- **400** → `{"ok": false, "detail": "<reason>"}` on failure

### `GET /api/status`
Live pipeline snapshot. Shape (keys may be absent if a source is unavailable):
- `watch_dirs`, `settings` (full settings.json)
- `trigger` → `{inotify_active, idle_sweep_seconds, last_sweep_age_s}`
- `llm` → `{model, reachable, allow_cloud}`
- `queue` → `{images_on_disk, unanalyzed, unverified_partials,
  awaiting_backfill, llm_verified}`
- `cameras[]` → `{name, images, bytes, budget_pct, last_frame_age_s, stale}`
- `filesystem` → `{free_gb, total_gb, used_pct}`
- `timezone` (resolved: `WEBCAM_TZ` env > settings > `Australia/Sydney`)
- `metrics` → inference rollup `{window_min, count, ok, failures, success_rate,
  local, cloud, avg_s, p95_s}` (just `{window_min, count:0}` when idle)
- `retention` → last retention event, or `null`
- **200** always.

### `GET /api/health`
Compact health for an external uptime monitor.
- **200** → `{"status": "ok", "checks": {...}, "cameras": [...]}`
- **503** → same shape with `"status": "degraded"`
- `checks` → `{inotify, llm_reachable, recent_sweep, disk_space}` (booleans;
  `llm_reachable` is skipped/true when deep passes are off)

### `GET /api/inference_log`
The 50 most recent LLM audit entries, newest first.
- **200** → `[{started, ok, duration_s, model, ...}, ...]`

### `POST /api/clip`
Build and download a visit/sequence as an animated clip, on the fly (reuses the
`integrations/media.py` builders). Read-only: each filename is validated against
the camera dirs (no traversal) and capped at 300; the builder samples ≤24 frames.
- **Body** `{"files": ["<name>", ...], "format": "gif" | "mp4"}` (default `gif`)
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

**Bridge.** The API polls `analysis.json` / `bursts.json` mtimes every ~3 s and
diffs them — it is *not* a true pipeline→API push, so latency is ~3 s. The first
pass seeds state **silently** (no backlog blast); only subsequent changes emit.
A `: ping` comment is sent each cycle as a heartbeat.

**Events** (`event:` name + JSON `data:`):

| Event | Payload | Fires when |
|-------|---------|-----------|
| `image.new` | `{"file": "<name>"}` | A frame first appears in `analysis.json` (right after the fast pass) — the earliest new-frame signal the API has. |
| `detection.preliminary` | `{"file": "<name>", "labels": ["car", ...]}` | A detector-only hit (a `fast_pass` record with a true label) appears, before/without an LLM verdict. Suppressed once promoted to verified. The only live detections while deep passes are off. |
| `new-detection` | `{"file": "<name>", "labels": ["person", ...]}` | An image gains a final LLM verdict with ≥1 true label. |
| `new-burst` | `{"id": "<burst-id>", "summary": "<text>"}` | A new burst/visit is written to `bursts.json`. |

### Not yet implemented

`analysis.llm` live token streaming ("AI is looking at this…" with the caption
typing in) requires a streaming Ollama call and a true pipeline→API push; see
[ROADMAP.md](ROADMAP.md). The SPA also still does a (coalesced) full
`loadData()` refetch per event rather than an incremental DOM patch.
