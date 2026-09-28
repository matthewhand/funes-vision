# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

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
