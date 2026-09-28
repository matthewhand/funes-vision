// Node assert test for the pure zonedTimeToUtc() helper in index.html.
// Camera filenames carry the source zone's WALL-CLOCK time (Sydney). The old
// parser hardcoded +10:00, which drifts +1h during AEDT (summer DST). This
// helper resolves the correct UTC instant DST-aware, so the displayed time
// matches the filename digits in every season. Run: node tests/test_zoned_time.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:zonedTimeToUtc ===\n([\s\S]*?)\n\s*\/\/ === \/pure:zonedTimeToUtc/);
assert(m, 'zonedTimeToUtc sentinel block not found in index.html');
eval(m[1]); // defines zonedTimeToUtc

// Winter (AEST, UTC+10): 14:00 Sydney == 04:00 UTC
assert.strictEqual(zonedTimeToUtc(2026, 6, 17, 14, 0, 0, 'Australia/Sydney'),
                   Date.UTC(2026, 5, 17, 4, 0, 0));
// Summer (AEDT, UTC+11): 14:00 Sydney == 03:00 UTC  (the case the old code got wrong)
assert.strictEqual(zonedTimeToUtc(2026, 1, 15, 14, 0, 0, 'Australia/Sydney'),
                   Date.UTC(2026, 0, 15, 3, 0, 0));

// Round-trip: formatting the instant back in Sydney shows the SAME wall clock
// (no DST drift) in both seasons — this is the actual user-visible guarantee.
const hhmm = (ms) => new Date(ms).toLocaleTimeString('en-GB',
  { timeZone: 'Australia/Sydney', hour: '2-digit', minute: '2-digit' });
assert.strictEqual(hhmm(zonedTimeToUtc(2026, 1, 15, 14, 0, 0, 'Australia/Sydney')), '14:00'); // summer
assert.strictEqual(hhmm(zonedTimeToUtc(2026, 6, 17, 14, 0, 0, 'Australia/Sydney')), '14:00'); // winter

// UTC source zone is the identity (no offset).
assert.strictEqual(zonedTimeToUtc(2026, 6, 17, 4, 0, 0, 'UTC'), Date.UTC(2026, 5, 17, 4, 0, 0));

// London summer (BST, UTC+1): 12:00 London == 11:00 UTC
assert.strictEqual(zonedTimeToUtc(2026, 7, 1, 12, 0, 0, 'Europe/London'),
                   Date.UTC(2026, 6, 1, 11, 0, 0));

// Millisecond field (issue #74). Captures inside the same second differ ONLY by
// this group, so dropping it made real burst cadence unplayable: every frame
// landed on a whole second and the minimum gap became 1000ms.
assert.strictEqual(zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'Australia/Sydney', 60),
                   Date.UTC(2026, 5, 18, 0, 14, 18, 60));
assert.strictEqual(zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'UTC', 750),
                   Date.UTC(2026, 5, 18, 10, 14, 18, 750));
// Omitted / non-numeric ms stays second-exact (the old signature's contract).
assert.strictEqual(zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'UTC'),
                   zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'UTC', 0));
assert.strictEqual(zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'UTC', undefined),
                   zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'UTC', 0));
assert.strictEqual(zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'UTC', NaN),
                   zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'UTC', 0));
// Consecutive 60ms burst frames land 60ms apart, not 1000ms apart.
assert.strictEqual(
  zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'UTC', 120) -
  zonedTimeToUtc(2026, 6, 18, 10, 14, 18, 'UTC', 60), 60);

console.log('zonedTimeToUtc: all assertions passed');
