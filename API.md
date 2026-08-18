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
- **200** → `{"fast_pass_engine", "deep_backfill", "deep_passes_enabled", "burst_summaries_enabled", "idle_sweep_seconds"}`

### `POST /api/settings`
Update one or more mutable settings (validated; others ignored).
- **Body** any subset of:
  - `fast_pass_engine` ∈ `"yolo" | "haar"`
  - `deep_backfill` ∈ `true | false`
  - `deep_passes_enabled` ∈ `true | false`
  - `burst_summaries_enabled` ∈ `true | false`
  - `idle_sweep_seconds` ∈ integer `15..3600`
- **200** → `{"ok": true, ...changed}`
- **400** → `{"error": "<key> must be <choices|range>"}` on an invalid value,
  or `{"error": "no recognized settings in payload"}` if nothing applied

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
- `watch_dirs`, `settings` (full settings.json — includes `max_age_days`,
  `max_dir_gb`, even though those are not `POST /api/settings`-mutable)
- `trigger` → `{inotify_active, idle_sweep_seconds, last_sweep_age_s}`
  (`last_sweep_age_s` is seconds since `/tmp/webcam_analysis.lastrun`, or
  JSON `null` if the marker is missing; the UI treats null as “unknown”.
  The cron watchdog and `recent_sweep` health check use this)
- `llm` → `{model, reachable, allow_cloud}`
- `inference` → `{}` when idle, otherwise the live `inference_status.json`
  object plus `running_for_s` (the ℹ panel’s “Analyzing now” line)
- `queue` → `{images_on_disk, unanalyzed, unverified_partials,
  awaiting_backfill, llm_verified}` (`queue_from_analysis`):
  - `unverified_partials` — `fast_pass == "partial"`
  - `awaiting_backfill` — `fast_pass == "negative"` **or**
    `_llm_skip == "no_trigger"` (car-only skip). Counted even when idle
    backfill is off, so the number stays honest.
  - `llm_verified` — successful merge only: `_llm` is a dict **and**
    `_llm_skip` is absent. Missing `fast_pass` is **not** enough.
    `{car: true, _llm_skip: "no_trigger"}` and bare `{person: true}` are
    not verdicts.
- `cameras[]` → `{name, images, bytes, budget_pct, last_frame_age_s, stale}`
  (`budget_pct` = image bytes in that camera dir vs `max_dir_gb`; UI warns
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
- **200** always.

### `GET /api/health`
Compact health for an external uptime monitor (and the cron watchdog).
- **200** → `{"status": "ok", "checks": {...}, "cameras": [...]}`
- **503** → same shape with `"status": "degraded"` — **body is still JSON**;
  clients must not treat HTTP 503 as “no response” (e.g. avoid bare `curl -f`
  if you need the checks object).
- `checks` → `{inotify, llm_reachable, recent_sweep, disk_space}` (booleans;
  `llm_reachable` is skipped/true when deep passes are off;
  `recent_sweep` is true when `last_sweep_age_s` &lt; 1 h;
  `disk_space` is true when host free_gb &gt; 1.0)

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
A named `event: ping` heartbeat is sent each cycle.

**Events** (`event:` name + JSON `data:`):

| Event | Payload | Fires when |
|-------|---------|-----------|
| `image.new` | `{"file": "<name>"}` | A frame first appears in `analysis.json` (right after the fast pass) — the earliest new-frame signal the API has. |
| `detection.preliminary` | `{"file": "<name>", "labels": ["car", ...]}` | A detector-only hit (`fast_pass` present **or** `_llm_skip == "no_trigger"` with ≥1 YOLO True key) that is not `is_llm_verified`. Labels are `YOLO_PRESENCE_KEYS` only. Suppressed once promoted to verified. The only live detections while deep passes are off. Car-only `{car: true, _llm_skip: "no_trigger"}` is preliminary. |
| `new-detection` | `{"file": "<name>", "labels": ["person", ...]}` | An image gains a successful LLM merge (`_llm` dict, no `_llm_skip`) with ≥1 true label. Absence of `fast_pass` is not a verdict. |
| `ping` | `{}` | Heartbeat every ~3 s; lets the client detect a silently-stalled connection (also keeps proxies unbuffered). |
| `new-burst` | `{"id": "<burst-id>", "summary": "<text>"}` | A new burst/visit is written to `bursts.json`. |

### Not yet implemented

`analysis.llm` live token streaming ("AI is looking at this…" with the caption
typing in) requires a streaming per-image Ollama call and a true pipeline→API
push; see [ROADMAP.md](ROADMAP.md). `image.new` / `detection.preliminary` /
`new-detection` patch in-memory state. Only `new-burst` still does a
`loadData()` refetch.

### `GET /api/llm-schema`
Read-only prompt + front/back JSON schemas the pipeline sends to the vision
model. Used by the ℹ panel.
- **200** → `{prompt, schemas: {front_door, dog_cam}, ...}`
