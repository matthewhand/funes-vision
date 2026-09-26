// Node assert test for the pure resolveDisplayTz() helper embedded in index.html.
// Mirrors the backend's resolve_timezone() fallback: validates the TZ the API
// hands the frontend and falls back to Australia/Sydney when absent/invalid.
// Run: node tests/test_resolve_display_tz.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const tm = html.match(/pure:taxonomy ===\n([\s\S]*?)\n\s*\/\/ === \/pure:taxonomy/);
assert(tm, 'taxonomy sentinel block not found in index.html');
eval(tm[1]); // defines haTaxonomy() — canonical default_tz
const m = html.match(/pure:resolveDisplayTz ===\n([\s\S]*?)\n\s*\/\/ === \/pure:resolveDisplayTz/);
assert(m, 'resolveDisplayTz sentinel block not found in index.html');
eval(m[1]); // defines resolveDisplayTz

assert.strictEqual(resolveDisplayTz('UTC'), 'UTC');
assert.strictEqual(resolveDisplayTz('Europe/London'), 'Europe/London');
assert.strictEqual(resolveDisplayTz('Australia/Sydney'), 'Australia/Sydney');
assert.strictEqual(resolveDisplayTz('  America/New_York  '), 'America/New_York'); // trimmed
assert.strictEqual(resolveDisplayTz(''), 'Australia/Sydney');          // blank -> fallback
assert.strictEqual(resolveDisplayTz('   '), 'Australia/Sydney');       // whitespace -> fallback
assert.strictEqual(resolveDisplayTz(null), 'Australia/Sydney');        // null -> fallback
assert.strictEqual(resolveDisplayTz(undefined), 'Australia/Sydney');   // missing -> fallback
assert.strictEqual(resolveDisplayTz(123), 'Australia/Sydney');         // non-string -> fallback
assert.strictEqual(resolveDisplayTz('Not/AZone'), 'Australia/Sydney'); // invalid IANA -> fallback

console.log('resolveDisplayTz: all assertions passed');
