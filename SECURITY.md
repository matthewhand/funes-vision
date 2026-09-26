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

- **Never commit secrets.** `slack_webhook`, MQTT credentials, tokens, and
  passwords belong in the gitignored `settings.json` / `integrations.json`
  files, not in tracked files or issue bodies.
- The gallery and API are published on **loopback only** by default
  (`127.0.0.1:8180` in `docker-compose.yml`). Before exposing them to a LAN or
  the internet, bind deliberately and enable authentication (`auth_basic` in
  `nginx.conf`, optional API auth); put TLS in front of it.
- Cloud inference is off unless you explicitly set `allow_cloud: true`. Slack
  and HA MQTT are the only default-outbound paths and are opt-in.
- State files (`analysis.json`, `alert_state.json`, `inference_log.json`, ...)
  are gitignored. If you believe a secret was committed, rotate it and open a
  private advisory so the history can be handled.

## Supported versions

This is a homelab project without long-term support branches. Security fixes go
to `master` and the latest release; see [CHANGELOG.md](CHANGELOG.md).
