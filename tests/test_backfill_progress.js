// Node assert test for the pure backfillProgress() helper in index.html — the
// deep-analysis backlog summary shown in the status panel. Run:
//   node tests/test_backfill_progress.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:backfillProgress ===\n([\s\S]*?)\n\s*\/\/ === \/pure:backfillProgress/);
assert(m, 'backfillProgress sentinel block not found in index.html');
eval(m[1]); // defines backfillProgress

// Typical mid-backfill state: analyzed = verified; pending = partials + queue.
let p = backfillProgress({ llm_verified: 1720, awaiting_backfill: 7000, unverified_partials: 48 });
assert.strictEqual(p.analyzed, 1720);
assert.strictEqual(p.pending, 7048);
assert.strictEqual(p.total, 8768);
assert.strictEqual(p.pct, 20);   // round(1720/8768*100)

// Nothing pending => 100% (and no divide-by-zero on an empty queue).
assert.deepStrictEqual(backfillProgress({ llm_verified: 50, awaiting_backfill: 0, unverified_partials: 0 }),
  { analyzed: 50, pending: 0, total: 50, pct: 100 });
assert.deepStrictEqual(backfillProgress({}), { analyzed: 0, pending: 0, total: 0, pct: 100 });
assert.deepStrictEqual(backfillProgress(undefined), { analyzed: 0, pending: 0, total: 0, pct: 100 });

// All pending, none analyzed => 0%.
assert.strictEqual(backfillProgress({ llm_verified: 0, awaiting_backfill: 10 }).pct, 0);

// Missing fields are treated as zero.
assert.strictEqual(backfillProgress({ llm_verified: 30 }).pct, 100);

console.log('backfillProgress: all assertions passed');
