#!/bin/bash
# tools/webcam-healthcheck.sh — verify the gallery container is actually up
# and serving, and bring it back if it died.
#
# Why this exists: systemd/webcam-compose.service is a oneshot that runs
# `docker compose up -d` once at boot. It reports "active (exited)" and never
# re-checks, so if the container later dies (clean exit, compose timeout,
# bind-mount hiccup) the unit still looks healthy and the gallery is dead —
# nothing restarts it. This script is run by a systemd timer every 60s and
# restarts the container only if it is down.
set -uo pipefail

COMPOSE="/usr/bin/docker compose -f /home/user/webcam/docker-compose.yml"
CONTAINER="webcam"
URL="http://127.0.0.1:8180/"

# 1. Is the container running?
if docker ps --filter "name=${CONTAINER}" --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  exit 0
fi

# 2. Is it serving (in case the container exists but nginx died inside)?
if curl -fsS -o /dev/null --max-time 5 "${URL}" 2>/dev/null; then
  exit 0
fi

# 3. Down — restart it.
echo "webcam healthcheck: container down, restarting" >&2
${COMPOSE} up -d "${CONTAINER}" 2>&1 >&2
sleep 3
if curl -fsS -o /dev/null --max-time 5 "${URL}" 2>/dev/null; then
  echo "webcam healthcheck: gallery back up" >&2
  exit 0
fi
echo "webcam healthcheck: restart failed — gallery still down" >&2
exit 1