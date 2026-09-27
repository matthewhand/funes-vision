#!/usr/bin/env bash
# funes-vision pipeline watchdog — cron safety net over systemd.
#
# The long-lived create-index / analyze services can look "active" while a
# sweep is stuck for hours (or analysis.json is corrupt and retention never
# runs). This script:
#   1. Restarts dead units
#   2. Detects a stale successful sweep and restarts stuck pipelines
#   3. Forces cleanup via `analyze_images.py --retention-only` using the app's
#      own settings.json (max_age_days, max_dir_gb, pins) — not a blind find/rm
#
# Usage (also installed to /etc/cron.d/webcam-watchdog):
#   tools/watchdog.sh check       # units + stale-sweep only
#   tools/watchdog.sh retention   # force retention-only pass
#   tools/watchdog.sh auto        # check; retention if needed or due hourly
#
# Env overrides:
#   WATCHDOG_API          default http://127.0.0.1:8190
#   WATCHDOG_STALE_S      restart pipelines if lastrun older than this (7200)
#   WATCHDOG_BUDGET_PCT   kick retention when any camera >= this (90)
#   WATCHDOG_LOG          log file path

set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="${WATCHDOG_API:-http://127.0.0.1:8190}"
STALE_RESTART_S="${WATCHDOG_STALE_S:-7200}"
BUDGET_KICK_PCT="${WATCHDOG_BUDGET_PCT:-90}"
LOCK="${WATCHDOG_LOCK:-/tmp/webcam_analysis.lock}"
MARKER="${WATCHDOG_MARKER:-/tmp/webcam_analysis.lastrun}"
RESTART_STATE="${WATCHDOG_RESTART_STATE:-/tmp/webcam_watchdog_last_restart}"
LOG="${WATCHDOG_LOG:-$BASE/watchdog.log}"
UNITS=(webcam-pipeline@Webcam21 webcam-pipeline@Webcam22 webcam-api)

log() {
  local line
  line="$(date -Is) $*"
  echo "$line" >>"$LOG" 2>/dev/null || true
  echo "$line"
  logger -t webcam-watchdog -- "$*" 2>/dev/null || true
}

need_sudo() {
  # Prefer passwordless sudo (install.sh runs as root; user has NOPASSWD here).
  if sudo -n true 2>/dev/null; then
    echo sudo -n
  else
    echo ""
  fi
}

SUDO="$(need_sudo)"

restart_unit() {
  local u="$1"
  if [[ -n "$SUDO" ]]; then
    $SUDO systemctl restart "$u" && log "restarted $u" || log "ERROR: failed to restart $u"
  else
    log "ERROR: cannot restart $u (no passwordless sudo)"
  fi
}

ensure_units() {
  local u state
  for u in "${UNITS[@]}"; do
    state="$(systemctl is-active "$u" 2>/dev/null || true)"
    if [[ "$state" != "active" ]]; then
      log "UNIT $u is '${state:-unknown}' — restarting"
      restart_unit "$u"
    fi
  done
}

sweep_age_s() {
  if [[ -f "$MARKER" ]]; then
    echo $(( $(date +%s) - $(stat -c %Y "$MARKER") ))
  else
    echo 999999
  fi
}

api_get() {
  # Do NOT use curl -f: /api/health returns 503 when degraded but the JSON
  # body is still the status we need to read.
  curl -sS --max-time 10 "${API}$1" 2>/dev/null || true
}

sync_web_roots() {
  python3 - <<'PY' 2>/dev/null || true
import json, os, shutil
base = os.environ.get("WEBCAM_BASE") or ""
if not base:
    raise SystemExit
settings = json.load(open(os.path.join(base, "settings.json")))
for d in settings.get("watch_dirs", []):
    if not os.path.isdir(d):
        continue
    # NOT pins.json. That file is owned by the API, per camera
    # (api_server._pins_path), and syncing the repo-root copy over it deleted
    # every pin the user made in the UI. Retention reads the per-camera file
    # (analyze_images.retention_pins) plus the legacy repo file read-only.
    for name in ("analysis.json", "bursts.json"):
        src = os.path.join(base, name)
        if os.path.isfile(src):
            try:
                shutil.copy2(src, os.path.join(d, name))
            except OSError:
                pass
PY
}

run_retention() {
  log "retention-only: starting (settings max_age_days / max_dir_gb / persist_budget_pct / pins)"
  export WEBCAM_BASE="$BASE"

  _retention_body() {
    if [[ -f "$HOME/.litellm/.env" ]]; then
      set -a
      # shellcheck disable=SC1091
      source "$HOME/.litellm/.env"
      set +a
    fi
    if python3 "$BASE/analyze_images.py" --retention-only; then
      touch "$MARKER"
      WEBCAM_BASE="$BASE" sync_web_roots
      log "retention-only: complete"
      return 0
    fi
    rc=$?
    log "retention-only: analyze_images.py failed (exit $rc)"
    return "$rc"
  }

  # Retention is a safety net, not the job. A live sweep holds $LOCK for
  # hours (MAX_DEEP_PASSES passes at ~40s), and the cron runs this at :05 every
  # hour, so a busy lock is the *normal* case — not a fault. The old code
  # answered it with pkill/fuser -k, i.e. the watchdog SIGKILLed the in-flight
  # sweep once an hour, discarding a vision call and resetting the deep-pass
  # budget. Never interrupt a running sweep: skip this cycle and let the next
  # :05 try again; an over-budget dir stays over budget until then, which is
  # what the hourly cadence is for. A genuinely wedged pipeline is cmd_check's
  # job, and it restarts the unit under RESTART_STATE throttling.
  if ! flock -n "$LOCK" -c true 2>/dev/null; then
    log "retention-only: lock busy — sweep in flight, skipping this cycle (left running)"
    return 0
  fi

  (
    # Re-check non-blocking: the probe above already told us the lock was free,
    # so waiting here would only be racing ourselves against the next sweep.
    if ! flock -x -n 200; then
      log "retention-only: lost the race for $LOCK — skipping this cycle (left running)"
      exit 0
    fi
    _retention_body
    exit $?
  ) 200>"$LOCK"
}

decide_with_python() {
  # Args: path-to-health.json path-to-status.json — avoids shell-quoting JSON.
  local health_file="$1" status_file="$2"
  WATCHDOG_BUDGET_PCT="$BUDGET_KICK_PCT" \
  WATCHDOG_STALE_S="$STALE_RESTART_S" \
  WATCHDOG_SWEEP_AGE="$(sweep_age_s)" \
  python3 - "$health_file" "$status_file" <<'PY'
import json, os, sys, time

def load_path(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}

health = load_path(sys.argv[1]) if len(sys.argv) > 1 else {}
status = load_path(sys.argv[2]) if len(sys.argv) > 2 else {}
budget_kick = float(os.environ.get("WATCHDOG_BUDGET_PCT", "90"))
stale_s = int(os.environ.get("WATCHDOG_STALE_S", "7200"))
marker_age = int(os.environ.get("WATCHDOG_SWEEP_AGE", "999999"))

api_ok = bool(health) or bool(status)
h_status = health.get("status") or ("unknown" if not api_ok else "ok")
checks = health.get("checks") or {}
recent_ok = bool(checks.get("recent_sweep", False))
api_age = status.get("trigger", {}).get("last_sweep_age_s")
try:
    sweep_age = int(api_age) if api_age is not None else marker_age
except (TypeError, ValueError):
    sweep_age = marker_age

cams = status.get("cameras") or []
max_budget = 0.0
for c in cams:
    try:
        max_budget = max(max_budget, float(c.get("budget_pct") or 0))
    except (TypeError, ValueError):
        pass

fs = status.get("filesystem") or {}
try:
    free_gb = float(fs.get("free_gb") if fs.get("free_gb") is not None else 999)
except (TypeError, ValueError):
    free_gb = 999.0

ret = status.get("retention")
ret_age = 10**9
if isinstance(ret, dict) and ret.get("ts"):
    try:
        ret_age = max(0, time.time() - float(ret["ts"]))
    except (TypeError, ValueError):
        pass

need_restart = (not api_ok) or (sweep_age >= stale_s)
need_retention = (
    max_budget >= budget_kick
    or free_gb < 1.0
    or need_restart
    or (not recent_ok and sweep_age > 3600)
    or ret_age > 3600  # at least hourly cleanup like the old cron
)

# KEY=value lines consumed by cmd_check's whitelist parser (no eval).
print(f"API_OK={'1' if api_ok else '0'}")
print(f"HEALTH={h_status}")
print(f"SWEEP_AGE={sweep_age}")
print(f"MAX_BUDGET={max_budget:.1f}")
print(f"FREE_GB={free_gb:.1f}")
print(f"RET_AGE={int(ret_age)}")
print(f"NEED_RESTART={'1' if need_restart else '0'}")
print(f"NEED_RETENTION={'1' if need_retention else '0'}")
PY
}

cmd_check() {
  ensure_units
  local health status hf sf evals
  health="$(api_get /api/health)"
  status="$(api_get /api/status)"
  if [[ -z "$health" && -z "$status" ]]; then
    log "API unreachable at $API — restarting webcam-api"
    restart_unit webcam-api
    sleep 2
    health="$(api_get /api/health)"
    status="$(api_get /api/status)"
  fi

  hf="$(mktemp)"
  sf="$(mktemp)"
  # Note: do not use ${var:-{}} — bash treats the first } as closing the
  # expansion, so a set var is emitted with a trailing extra brace and JSON
  # parse fails silently in decide_with_python.
  if [[ -n "$health" ]]; then printf '%s' "$health" >"$hf"; else printf '%s' '{}' >"$hf"; fi
  if [[ -n "$status" ]]; then printf '%s' "$status" >"$sf"; else printf '%s' '{}' >"$sf"; fi
  evals="$(decide_with_python "$hf" "$sf")"
  rm -f "$hf" "$sf"
  # Parse the KEY=value output without eval: only whitelisted keys are
  # assigned, and printf -v writes the value verbatim (no word-splitting,
  # globbing or command substitution from untrusted API-derived text).
  API_OK= HEALTH=unknown SWEEP_AGE=999999 MAX_BUDGET=0.0 FREE_GB=999.0
  RET_AGE=999999 NEED_RESTART=0 NEED_RETENTION=0
  local k v
  while IFS='=' read -r k v; do
    if [[ -z "$k" ]]; then continue; fi
    case "$k" in
      API_OK|HEALTH|SWEEP_AGE|MAX_BUDGET|FREE_GB|RET_AGE|NEED_RESTART|NEED_RETENTION)
        printf -v "$k" '%s' "$v"
        ;;
    esac
  done <<<"$evals"
  log "check: health=$HEALTH sweep_age=${SWEEP_AGE}s max_budget=${MAX_BUDGET}% free_gb=$FREE_GB ret_age=${RET_AGE}s restart=$NEED_RESTART retention=$NEED_RETENTION"

  if [[ "${NEED_RESTART:-0}" == "1" ]]; then
    now="$(date +%s)"
    last=0
    [[ -f "$RESTART_STATE" ]] && last="$(cat "$RESTART_STATE" 2>/dev/null || echo 0)"
    if [[ $((now - last)) -lt "$STALE_RESTART_S" ]]; then
      log "stale sweep (age ${SWEEP_AGE}s) but restarted $((now - last))s ago — not thrashing"
    else
      log "stale/unhealthy sweep (age ${SWEEP_AGE}s) — restarting pipeline units"
      restart_unit "webcam-pipeline@Webcam21"
      restart_unit "webcam-pipeline@Webcam22"
      echo "$now" >"$RESTART_STATE"
      # Give create-index a moment to come back before we grab the lock.
      sleep 3
    fi
  fi

  export NEED_RETENTION NEED_RESTART SWEEP_AGE MAX_BUDGET
}

cmd_retention() {
  ensure_units
  run_retention
}

cmd_auto() {
  cmd_check
  if [[ "${NEED_RETENTION:-0}" == "1" ]]; then
    run_retention || true
  else
    log "auto: healthy enough; retention not required this cycle"
  fi
}

mode="${1:-auto}"
case "$mode" in
  check)     cmd_check ;;
  retention) cmd_retention ;;
  auto)      cmd_auto ;;
  -h|--help|help)
    sed -n '2,20p' "$0"
    exit 0
    ;;
  *)
    echo "Unknown mode: $mode (use check|retention|auto)" >&2
    exit 2
    ;;
esac
