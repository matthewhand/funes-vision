# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Report privately
using GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
via the repository's **Security → Report a vulnerability** tab:

<https://github.com/matthewhand/funes-vision/security/advisories/new>

Include what you can: affected file or endpoint, reproduction steps, impact,
and any suggested fix. You should get an acknowledgement within a few days.
Please allow a reasonable window for a fix before public disclosure.

## Scope

funes-vision runs a local HTTP API, an SSH-less FTP-style ingest (files land in
watched directories), and an nginx-served gallery. In scope:

- The API server (`api_server.py`) and its routes/authentication.
- The gallery SPA (`index.html`) and `nginx.conf` (including `auth_basic`).
- The ingest and retention logic (`create-index.sh`, `scans.py`, watchdog).
- Integrations that can send data off-box (Slack, Home Assistant MQTT).

Out of scope: vulnerabilities in the upstream third-party components listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) (report those upstream); issues
that require an already-compromised host; and misconfiguration where the
operator knowingly exposes the loopback-only service.

## Secret handling

- **Never commit secrets.** Slack bot/app tokens (`integrations.json` → `slack`),
  MQTT credentials, the API token, and passwords belong in the gitignored
  `settings.json` / `integrations.json` files, not in tracked files or issue
  bodies. `GET /api/status` is unauthenticated, so it filters `settings.json`
  on the way out — **recursively**, through nested dicts and lists, and with
  key matching normalised for case and separators so `privateKey`, `x-api-key`
  and `integrations.slack.bot_token` are all dropped. Report that a secret
  exists; never its value.
- The gallery is published on **loopback only** by default
  (`127.0.0.1:8180` in `docker-compose.yml`). The write API binds
  **`127.0.0.1:8190` on the host** — that bind comes from
  `systemd/webcam-api.service` running `api_server.py` directly, not from
  compose, and is controlled by `WEBCAM_API_HOST` (never `docker-compose.yml`).
  Before exposing either to a LAN or the internet, bind deliberately and enable
  authentication: `auth_basic` in `nginx.conf` in front of `/api/`, plus
  `WEBCAM_API_TOKEN` for the mutating endpoints; put TLS in front of it.
- Browser access to the API is scoped by `WEBCAM_CORS_ORIGIN`, an origin
  allowlist — a literal `*` is dropped, so list every origin you use. Writes
  whose `Origin` matches the `Host` they were sent to are same-origin and
  always allowed, which is the normal reverse-proxy deployment; the list is
  only for a gallery served from a different origin (e.g. `http://<host>:8180`
  opened directly).
- Cloud inference is off unless you explicitly set `allow_cloud: true`. Slack
  and HA MQTT are the only default-outbound paths and are opt-in.
- State files (`analysis.json`, `alert_state.json`, `inference_log.json`, ...)
  are gitignored. If you believe a secret was committed, rotate it and open a
  private advisory so the history can be handled.
- **Config files must be regular files.** The startup pass that tightens
  `settings.json` / `integrations.json` to `0600` refuses a **symlink** and
  logs it instead of following it — `chmod` through a link would change the
  permissions of a file the API does not own, while reporting the change under
  the config file's name. If you point a config at a symlink, set the mode at
  the target yourself.
- **The write API is resource-capped.** Concurrent connections are bounded by
  `WEBCAM_API_MAX_CONNECTIONS` (default 64; a burst past it gets 503 rather
  than another thread), concurrent `/api/events` streams by
  `WEBCAM_SSE_MAX_CLIENTS` (default 8), each connection's read by
  `WEBCAM_API_SOCKET_TIMEOUT` (default 30 s), and each `POST` body by
  `WEBCAM_API_MAX_BODY` (default 1 MiB). A misconfigured value is clamped to a
  usable one rather than left to raise inside the request.

## Supported versions

This is a homelab project without long-term support branches. Security fixes go
to `main` (the default branch) and the latest release; see
[CHANGELOG.md](CHANGELOG.md).
