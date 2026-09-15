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

# 0. nginx alias roots must match the compose bind mounts.
#
# nginx.conf has a location per camera (/cameras/<id>/ ... root <alias>), and
# the alias root is the bind-mount *target* inside the container. nginx cannot
# read settings.json or docker-compose.yml at runtime, so these two files are
# the only contract between them — if a camera is renamed in settings.json
# and the location isn't updated, the SPA's relative URLs 404 as HTML and the
# failure is silent (every asset returns the SPA fallback with a 200). This
# guard cross-checks them at startup so a drift is loud instead of invisible.
check_alias_roots() {
  local conf="/etc/nginx/conf.d/default.conf"
  [ -f "$conf" ] || return 0
  # alias roots from nginx.conf: `root /data/webcam22;` inside a location block
  local roots targets missing=()
  roots=$(grep -oE '^\s*root\s+[^;]+;' "$conf" | sed -E 's/^\s*root\s+//; s/;$//' | sort -u)
  [ -n "$roots" ] || return 0
  # bind-mount targets from docker-compose.yml: `<host>:<target>:ro`.
  # Run from the repo dir — the compose file path is relative.
  local tfile compose_dir
  compose_dir="$(cd "$(dirname "$0")/.." && pwd)"
  tfile=$(mktemp)
  grep -oE ':[^:]+:ro$' "$compose_dir/docker-compose.yml" \
    | sed -E 's/^://; s/:ro$//' | sort -u > "$tfile"
  # Match against a temp file rather than a case pattern: the targets list
  # contains newlines, and case globs don't match across them.
  for r in $roots; do
    if grep -qxF "$r" "$tfile"; then
      continue
    fi
    missing+=("$r")
  done
  rm -f "$tfile"
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "webcam healthcheck: nginx alias root(s) ${missing[*]} have no matching bind mount in docker-compose.yml" >&2
    return 1
  fi
  return 0
}

check_alias_roots || {
      # A config mismatch is not something a container restart can fix, so
      # this is a warning, not a failure — but it must be impossible to miss,
      # because a silent drift means the second camera's gallery 404s every
      # asset as HTML while every endpoint still returns 200.
      echo "==================== webcam healthcheck: CONFIG DRIFT ====================" >&2
      echo "nginx.conf has an alias root with no matching bind mount in" >&2
      echo "docker-compose.yml. A renamed camera in settings.json breaks the" >&2
      echo "second camera's gallery (lucide.min.js, thumbs/, images.json all" >&2
      echo "404 as the SPA fallback). Edit nginx.conf to match the compose" >&2
      echo "mounts, or remove the location for that camera." >&2
      echo "=======================================================================" >&2
    }

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