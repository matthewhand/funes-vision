# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **A missing or empty `analysis.json` no longer deletes the whole archive
  (#56).** The #29 partial-catalog protection armed only on a *parse failure*
  and was cleared by the next clean parse, so it did not cover the two ways
  the same data loss still happened. (a) A missing, blank, unreadable or `{}`
  catalog returned *before* the mark was armed, so every frame read as
  unanalysed backlog and the age pass deleted it before the analysis queue was
  built — precisely the state PR #40's risk section tells operators to create
  ("delete analysis.json to force a rebuild") and the residue #23 leaves
  behind. (b) The mark was dropped the moment the file parsed, which happens
  when the sweep's own flush completes the JSON, not when the lost rows are
  back: with a backlog larger than one sweep's deep-pass budget the second
  sweep deleted the frames the first had only partly re-catalogued. The mark
  is now armed by every state that can have lost rows and held until the
  frames the catalog cannot account for actually have rows again, with the
  operator warning naming the real reason. Transient backlog (a frame that
  arrived after the last catalog write) does not arm it, and unanalysed frames
  remain pass 3's last-resort eviction so a pipeline that cannot catalogue its
  backlog still cannot fill the disk. The `retention_log.json` /
  `alert_state.json` / `pins.json` branch of the loader, previously untested,
  no longer parks a `.recovered` sidecar or claims the original is "left
  intact" when its own writer replaces it wholesale.
- **Mistyped destructive settings no longer become data loss (#54).**
  `setting_num` clamped every numeric key to `lo=0.0`, which is the *safe* end
  for `max_dir_gb` (0 disables the budget passes) but the *destructive* end
  for the rest: `max_age_days: -1` became "expire every non-persistable frame
  now" and `persist_budget_pct: -1` became "the persist archive may occupy
  nothing", both while logging a reassuring `is below 0.0; clamping`.
  `max_age_days`, `persist_budget_pct`, `max_deep_passes` and
  `deep_concurrency` now floor at 1; the knobs whose low end is safe or inert
  keep clamping.
- **Secret temp files are never world-readable (#57).** `_atomic_write_json`
  created its temp file with `open(tmp, "w")` — 0666 & ~umask — and only
  `chmod`ed it to 0600 *after* the whole secret was written and fsynced, so a
  kill in that window left a `*.tmp.<pid>.<tid>` holding e.g. a Slack bot token
  at 0644, for ever. The mode is now set at creation with
  `os.open(..., O_WRONLY|O_CREAT|O_TRUNC, mode)`, matching
  `integrations/__init__.py`.
- **Non-numeric settings keys are guarded too (#58).** `setting_list()` and
  `setting_str()` mirror `setting_num()`: `watch_dirs`, `cameras`,
  `gate_ignore_labels`, `ignore_regions` and `ollama_url` now log and keep the
  previous value instead of being read raw, so `gate_ignore_labels: "car"`
  becomes `["car"]` rather than `set("car") == {"c", "a", "r"}`. The int knobs
  now accept an exactly-integral float (`"5.0"` → 5) like the float knobs do,
  and refuse a fractional one rather than truncating it.
- **A hostile `max_dir_gb` no longer takes `/api/status` and `/api/health`
  down (#54).** #28 gave the pipeline `analyze_images.setting_num` for exactly
  this and routed its own call sites through it, but both `api_server` call
  sites were left doing `settings.get("max_dir_gb", 5.0)` and
  `max_dir_gb * 1024**3` raw — the same "the two call sites disagreed" pattern
  #22 fixed on the pipeline side. `null`, `"5.0"`, `"abc"` and `{}` raised
  `TypeError` *while the response was being built*, so the handler thread died
  with nothing written and the client saw the connection close with no reply
  at all. Both endpoints are polled hard: the uptime monitor, the SPA, and
  `tools/watchdog.sh::cmd_check`, which then read `cams=[]` → `MAX_BUDGET=0` and
  lost its 90 %-budget retention kick silently. The call site is now routed
  through the same guarded accessor. Non-finite values (`1e309`, `NaN`) are
  refused too — they survived the multiply and reached the body as a bare
  `Infinity`/`NaN`, which `JSON.parse` rejects, so a `200` came back the
  browser could not read at all.
- **`redacted_settings()` recurses, and matches the key, not the value
  (#54).** It lowercased the *value* and tested the raw key, so `privateKey`
  sailed through and the marker `api_key` never matched the hyphen in
  `x-api-key`; and it did not recurse, so `integrations.slack.bot_token` and
  `servers[0].token` were serialized verbatim into an **unauthenticated**
  body. 17 of 36 hostile key shapes survived with the literal secret attached.
  The filter now walks dicts and lists, normalises key case and separators,
  treats any key ending in `key` as a secret, and drops a secret-named key's
  whole subtree. Benign nested values are kept, and the documented shape of
  `settings` in `/api/status` is unchanged.
- **Non-finite numbers can no longer reach a JSON body.** `json.dumps` writes
  `inf`/`nan` as bare `Infinity`/`NaN`, which is not JSON. Every response now
  goes through one sanitizer, so no field can reintroduce the hole by
  forgetting to sanitize itself.
- **Connections are capped, so a burst cannot exhaust threads (#58).**
  `ThreadingHTTPServer` starts a thread per accepted connection with no
  ceiling; the per-connection socket timeout from #33 bounds a thread's
  *lifetime*, not the thread *count*, so peers that connect and never finish a
  request line each cost a thread for the whole timeout window. `/api/health` —
  the uptime monitor and watchdog endpoint — is served by one of those threads.
  A slot is now taken before the thread exists and released however the thread
  ends; past `WEBCAM_API_MAX_CONNECTIONS` (default 64, `0` disables) the peer
  gets a 503 and the socket closes. The listen backlog is raised from the
  stdlib's 5 so a legitimate parallel load is queued before it is refused.
  Ordinary single-user use is 1–2 concurrent connections and is untouched.
- **A pin present in both pins files can be unpinned (#59).**
  `analyze_images.retention_pins()` unions `<cam>/pins.json` with the legacy
  `<repo>/pins.json`, but an unpin stopped as soon as the camera file was
  cleaned. So a name in **both** — which is what every upgraded install has,
  since both pre-#39 sync steps wrote the repo file into every camera dir —
  reported `pinned: false`, showed unpinned in the UI, and still protected the
  frame from every sweep forever. An unpin now clears both files, legacy first,
  so a crash in between fails toward "keep the frame". A name that only ever
  existed in the legacy file is still removed there, without materialising an
  empty `pins.json` in the camera dir.
- **`pins.json` no longer grows without bound (#59).** Pin names whose frame is
  gone from the camera dir are dropped on the next write to that file, so
  `/api/pins` stops reporting names with nothing behind them. An unreadable or
  empty directory listing drops nothing, so a hiccup can never wipe a pin list,
  and every reclaimed name is logged.
- **A blank `api_token` answers once, not twice (#57).** `_authorized()` sent a
  500 and the caller then sent a 401 after it, so one connection carried two
  full HTTP responses with the second status line glued to the first body —
  unparseable by anything, and the status that reached the client
  misdescribed the fault. The refusal is now a single response.
- **`WEBCAM_API_SOCKET_TIMEOUT` can no longer brick every request (#57).** It
  had no lower bound, and `socketserver.setup()` passes it straight to
  `settimeout()`, which raises `ValueError` for a negative number — inside
  `BaseRequestHandler.__init__`, so one typo in the systemd `Environment=`
  answered *no* request at all. A value `<= 0` now means "no read timeout"
  (`settimeout(0)` is non-blocking mode, which would raise on the next read
  anyway) and the startup banner says so.
- **`harden_secret_files()` no longer follows symlinks (#57).** `os.stat` and
  `os.chmod` both follow links, so a `settings.json` that was a symlink made
  the startup hardening pass chmod its *target* — stripping the permissions
  off an arbitrary file, reported under the config file's name. A symlink is
  now refused and logged. `os.lchmod` is deliberately not the fix: on Linux it
  is a no-op on a symlink, so the target would stay world-readable while the
  log claimed `0600`.
- **The CSRF origin gate no longer refuses the app's own writes (#51).** The
  `Origin` check added in #41 consulted only `WEBCAM_CORS_ORIGIN`, whose
  default lists neither documented access path, so **every** browser mutation
  (`pin`, `delete`, `settings`, `integrations`, `clip`) returned
  **403 `origin not allowed`** — including the reverse-proxy deployment, where
  the SPA and `/api/` are the same origin and the UI only showed a bare
  `API 403`. A mutation is now accepted when its `Origin` matches the `Host` it
  was addressed to, or the trusted `X-Forwarded-Host`, before the allowlist is
  consulted. Cross-origin writes are refused exactly as before. The trust
  boundary, the config surface and the operational changes are in
  [API.md](API.md#api-server).

### Changed

- **The user guide no longer documents the removed consensus-detection UI.** The detector/vision-model consensus path was deleted from the app along with the toggle that surfaced it, but the shipped `docs/USER-GUIDE.html` — the page the gallery’s Help button opens, and the file `create-index.sh` and `tools/deploy-webroot.sh` publish — still described a line-through badge on the card and a Settings row for that toggle, so a user read about a control that does not exist. Both rows are gone, and the amber-badge row now describes the lifecycle the code actually implements: `preliminary` (detector only, awaiting the vision pass) renders an amber `?` on any label and drops it once `_llm` merges; with **Vision deep passes** off there is no second pass, so the label renders plain. The same corrections are applied to `docs/USER-GUIDE.md`, the Era 4 history in `ARCHITECTURE.md`, and the verdict/badge entries in `ROADMAP.md`. The line-through styling that *does* exist — hidden object labels and OFF feature rows in Settings — is documented as such. This changelog’s own lifecycle entry was already correct. No app, test or build changes.
- **New env knob `WEBCAM_API_MAX_CONNECTIONS`** (default `64`, `0` disables),
  read per connection like the other limits — an `Environment=` edit applies
  to the next connection with no restart. Documented in
  [API.md — Request limits](API.md#request-limits) and
  [DEVELOP.md](DEVELOP.md#api_serverpy-port-8190).
- **SSE transport.** `/api/events` now tails the pipeline's append-only
  `events.jsonl` (`pipeline_events.py`) as the **primary** bus, so
  `image.new` / `detection.preliminary` / `new-detection` / `new-burst` are a
  real pipeline→API push instead of a 3 s catalog-mtime diff. `analysis.json` /
  `bursts.json` mtime diffing remains as a fallback for catalogs written by a
  pipeline that does not emit.
- **SSE heartbeat.** `event: ping` is now every 15 s
  (`WEBCAM_SSE_HEARTBEAT_S`), not every poll cycle.
- **SSE idle close.** A stream with no event traffic for
  `WEBCAM_SSE_IDLE_TIMEOUT_S` (default 600 s) is reaped with a new
  `event: close` (`{"reason": "idle_timeout"}`) and the socket ends.
- **SSE client cap.** Concurrent `/api/events` streams are bounded by
  `WEBCAM_SSE_MAX_CLIENTS` (default 8); past the cap a new connection gets
  **503** instead of another thread.
- **Ingest event.** The watcher reacts to `close_write,moved_to`, not `create`,
  so a half-uploaded JPEG no longer triggers a sweep.
- `systemd/install.sh` now renders and enables `webcam-compose.service` and the
  `webcam-healthcheck.{service,timer}` pair it already shipped, and installs
  `tools/webcam-healthcheck.sh` to `/usr/local/bin` where the unit expects it.

### Security

- **CORS is an origin allowlist.** The old `Access-Control-Allow-Origin: *` is
  gone. `Access-Control-Allow-Origin` is only sent for an allowlisted `Origin`,
  and a literal `*` in the env var is dropped.
  **Migration:** if you have more than one gallery origin, list them —
  `WEBCAM_CORS_ORIGIN="https://cam.example.com,https://cam.lan"`. Leaving it
  unset keeps the localhost-only default
  (`http://localhost:8180,http://127.0.0.1:8180`); a browser on any other origin
  will now get no CORS grant until you add it.
- **The write API binds loopback by default.** `WEBCAM_API_HOST` defaults to
  `127.0.0.1`; set it to `0.0.0.0` only behind a proxy that authenticates.
- **Documented token auth.** `WEBCAM_API_TOKEN` (or the `api_token` key in
  `settings.json`) gates every `POST` via `Authorization: Bearer <token>` or
  HTTP Basic; unset preserves the previous no-auth behaviour.
- **`cameras[]` registry.** Watch dirs are declared as camera objects
  (`id`/`label`/`kind`/`dir`) so ignore-regions and the HA schema survive a
  folder rename.
- **`MQTT_TOPIC_PREFIX`** joins `mqtt_topic_prefix` in `settings.json`
  (default `funes_vision`) as an env override.
- **Default branch renamed `master` → `main`.** Security fixes go to `main`.

### Planned

- Per-image LLM token streaming ("AI is looking at this…") and incremental DOM
  patching on top of the finished `events.jsonl` push.
- Formal OpenAPI specification for the REST endpoints and SSE event schema.

See [ROADMAP.md](ROADMAP.md) for the full list.

## [1.0.0] - 2026-09-26

First tagged release: a local-first, two-stage computer vision sentry for
camera motion stills.

### Added

- Event-driven ingest: an `inotifywait` watcher queues each new snapshot for
  analysis within seconds (no fixed-interval scanning).
- Tiered AI vision: a fast YOLOv4-tiny pass (person/car/bird/cat/dog) followed
  by a local vision model (`gemma4:e2b` via Ollama). The deep pass writes a
  fixed Home Assistant flag schema (`postal_delivery`, `porch_access`,
  `animal_detected`, `dog_walked`, `clothes_drying`, `weapon_detected`, ...)
  and never overwrites detector labels.
- Detection lifecycle: `preliminary` (detector-only) records that merge into
  full records as HA flags attach.
- Timeline of visits: contiguous detector presence grouped into one card per
  visit, with flipbook animation, Objects/All tabs, auto object filters, label
  aliasing and blacklist, day/hour activity charts, and HA scene badges.
- Pin-aware retention (file rotation): per-camera byte budget + 30-day age
  limit for empty frames; pinned images are never auto-deleted; detections and
  the unanalyzed backlog are pruned only under disk pressure.
- Cron watchdog: every 15 minutes checks services and stuck sweeps; hourly
  runs the same pin-aware retention from `settings.json`.
- Observability: `/api/status`, `/api/health` (4 checks), a live inference
  audit trail, camera-liveness and disk stats, and `GET /api/llm-schema`.
- Live updates (partial): an SSE `/api/events` stream pushing `image.new`,
  `new-detection`, `detection.preliminary`, and `new-burst` to the open
  gallery, with graceful fallback to polling.
- Integrations: Slack (image/animation + link-back) and optional Home
  Assistant MQTT (retained flags). ntfy as a config-file provider.
- Configurable display timezone: `WEBCAM_TZ` env > `settings.json` >
  `Australia/Sydney`, with DST-aware filename timestamp parsing.
- Installable web app manifest with iOS "Add to Home Screen" (no service
  worker).
- Saved searches (named filter snapshots, localStorage per-device).
- Clip export: download a visit as an animated GIF (`POST /api/clip`); MP4 is
  API-only.
- Test harness: stdlib Python `unittest` for backend helpers plus Node assert
  suites for the SPA's pure helpers, no third-party test dependencies.

### Security

- Local-first: cloud inference is off by default behind the `allow_cloud`
  kill switch.
- Gallery published on loopback by default; optional API and nginx basic auth.
- Secrets isolated in gitignored config and never web-synced.

[Unreleased]: https://github.com/matthewhand/funes-vision/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/matthewhand/funes-vision/releases/tag/v1.0.0
