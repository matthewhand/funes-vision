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

docker compose -f /home/user/webcam/docker-compose.yml up -d gallery
# exec so systemd tracks the wait process as the main PID: when the container
# exits, this process exits, and Restart=always brings the whole thing back.
exec docker wait gallery