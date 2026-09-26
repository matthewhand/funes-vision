#!/usr/bin/env bash
# Run every Node test suite in tests/*.js. `node tests/*.js` only ever runs the
# first file (the shell passes the rest as argv), so this loops explicitly and
# stops at the first failure. Usage: bash tests/run.sh
set -euo pipefail

cd "$(dirname "$0")/.."

total=0
passed=0
for f in $(ls tests/*.js | sort); do
  total=$((total + 1))
  if node "$f"; then
    printf 'PASS %s\n' "$f"
    passed=$((passed + 1))
  else
    printf 'FAIL %s\n' "$f" >&2
    printf 'JS tests: %d/%d passed (stopped at first failure)\n' "$passed" "$total" >&2
    exit 1
  fi
done

printf 'JS tests: %d/%d passed\n' "$passed" "$total"
