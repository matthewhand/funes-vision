#!/usr/bin/env bash
# Run every Node test suite in tests/*.js. `node tests/*.js` only ever runs the
# first file (the shell passes the rest as argv), so this loops explicitly and
# stops at the first failure. Usage: bash tests/run.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# Inline-script syntax gate. The SPA's one inline <script> is pulled out with
# the same tool ESLint consumes (tools/lint/extract-inline.mjs) and parsed with
# `node --check`, so a syntax error in index.html fails here instead of only in
# a browser. This is not a tests/*.js suite, so it is not part of the counter.
inline_js_dir="$(mktemp -d)"
inline_js="$inline_js_dir/inline.js"
trap 'rm -rf "$inline_js_dir"' EXIT
node tools/lint/extract-inline.mjs > "$inline_js"
node --check "$inline_js"
printf 'PASS inline syntax (extract-inline.mjs -> node --check)\n'

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
