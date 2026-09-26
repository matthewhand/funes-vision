#!/bin/bash
# tools/htpasswd.sh — manage /etc/nginx/.htpasswd for the public webcam/dogcam
# vhosts.
#
# Why this exists: the public webcam/dogcam vhosts proxy /api/ to the write
# API on :8190 behind basic-auth. The auth file was a hand-maintained
# /etc/nginx/.htpasswd whose single user entry no longer authenticated (401 on
# every write-API call), so pin/delete/settings/clip were silently dead for
# anyone visiting the real URL. Nothing in the repo managed it, so it drifted
# and nobody noticed.
#
# Usage:
#   tools/htpasswd.sh                 # verify current file
#   tools/htpasswd.sh set USER PASS   # upsert a user, bcrypt hash
#   tools/htpasswd.sh rm USER         # remove a user
set -euo pipefail

HTPASSWD_FILE="/etc/nginx/.htpasswd"
# nginx (Debian/Ubuntu) workers run as www-data; the master (root) reads the
# file at config load, but 640 root:www-data lets workers read it too without
# making the hashes world-readable. Override with HTPASSWD_GROUP if needed.
NGINX_GROUP="${HTPASSWD_GROUP:-www-data}"

verify() {
  if [ ! -f "$HTPASSWD_FILE" ]; then echo "no $HTPASSWD_FILE"; exit 1; fi
  echo "users: $(cut -d: -f1 "$HTPASSWD_FILE" | tr '\n' ' ')"
  ls -l "$HTPASSWD_FILE"
}

case "${1:-}" in
  set)
    [ "$#" -eq 3 ] || { echo "usage: $0 set USER PASS" >&2; exit 2; }
    sudo htpasswd -cb "$HTPASSWD_FILE" "$2" "$3"
    sudo chown "root:$NGINX_GROUP" "$HTPASSWD_FILE"
    sudo chmod 640 "$HTPASSWD_FILE"
    echo "set $2"
    ;;
  rm)
    [ "$#" -eq 2 ] || { echo "usage: $0 rm USER" >&2; exit 2; }
    sudo htpasswd -b "$HTPASSWD_FILE" "$2" "x" >/dev/null 2>&1 || true
    sudo htpasswd -D "$HTPASSWD_FILE" "$2" 2>/dev/null || true
    sudo chown "root:$NGINX_GROUP" "$HTPASSWD_FILE"; sudo chmod 640 "$HTPASSWD_FILE"
    echo "removed $2"
    ;;
  *)
    verify
    ;;
esac