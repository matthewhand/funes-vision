#!/bin/bash
# Owns the gallery container's lifetime.
#
# `docker compose up -d` ensures the container exists (idempotent: a no-op if
# it is already running), then `docker wait` blocks until it exits. So this
# process stays alive for as long as the container does, and systemd's
# Restart=always restarts it the moment it dies.
#
# That is the whole point. The old unit was Type=oneshot: `compose up -d`
# returned immediately in detached mode, the unit reported "active (exited)",
# and a container that died an hour later was simply never noticed — the
# gallery was down for two hours while systemd thought the service was fine.
# A separate 60s healthcheck timer catches that case too, but it should not
# be the only thing standing between the gallery and downtime.
set -eu

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="/run/lock/funes-gallery-compose.lock"

# The healthcheck can also repair the container. Serialize compose
# mutations so a timer firing during service restart cannot race `up -d`.
exec 9>"$LOCK_FILE"
flock -x 9
docker compose -f "$REPO_DIR/docker-compose.yml" up -d gallery
flock -u 9
# exec so systemd tracks the wait process as the main PID: when the container
# exits, this process exits, and Restart=always brings the whole thing back.
exec docker wait gallery