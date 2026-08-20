// Node assert tests for Gallery UX polish pure helpers in index.html.
// Run: node tests/test_ux_polish_helpers.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}

eval(block('formatGb'));
eval(block('countActiveFilters'));
eval(block('activeFilterBanner'));
eval(block('prefersReducedMotion'));
eval(block('spaceTogglesPlayback'));
eval(block('frameErrorFallbackSrc'));
eval(block('shouldRefreshSystemPanel'));
eval(block('emptyStateKind'));
eval(block('sparklineHasData'));
eval(block('labelCounts'));

// --- formatGb ---
assert.strictEqual(formatGb(512 * 1024 * 1024, 'B'), '512 MB');
assert.strictEqual(formatGb(0.5, 'GB'), '512 MB');
assert.strictEqual(formatGb(2.25, 'GB'), '2.3 GB');
assert.strictEqual(formatGb(3 * 1024 ** 3, 'B'), '3.0 GB');
assert.strictEqual(formatGb(null), '');
assert.strictEqual(formatGb(-1, 'GB'), '');

// --- countActiveFilters / activeFilterBanner ---
assert.strictEqual(countActiveFilters({}), 0);
assert.strictEqual(countActiveFilters({ searchQuery: 'person' }), 1);
assert.strictEqual(countActiveFilters({
  searchQuery: 'x', activeDateFilter: '2026-07-01', timeStart: 6, timeEnd: 18,
  activeHourChartFilter: 9, objectFilters: ['person', 'dog']
}), 6);
assert.strictEqual(countActiveFilters({ objectFilters: new Set(['a', 'b']) }), 2);
assert.strictEqual(countActiveFilters({ activeDateFilter: 'all', timeStart: 0, timeEnd: 23 }), 0);
assert.strictEqual(countActiveFilters({ activeDateFilter: '2026-08-21' }, '2026-08-21'), 0);
assert.strictEqual(countActiveFilters({ activeDateFilter: '2026-08-20' }, '2026-08-21'), 1);
assert.deepStrictEqual(activeFilterBanner({}), { count: 0, text: '' });
assert.deepStrictEqual(activeFilterBanner({ searchQuery: 'hi' }), { count: 1, text: '1 filter active' });
assert.deepStrictEqual(activeFilterBanner({ objectFilters: ['a', 'b'] }), { count: 2, text: '2 filters active' });

// --- prefersReducedMotion ---
assert.strictEqual(prefersReducedMotion(true), true);
assert.strictEqual(prefersReducedMotion(false), false);
assert.strictEqual(prefersReducedMotion(null), false);

// --- spaceTogglesPlayback ---
// Synthetic element tree: button inside div
function el(tag, parent) {
  const n = { tagName: tag.toUpperCase(), parent, closest(sel) {
    const tags = sel.split(',').map(s => s.trim().replace(/\[role="(\w+)"\]/, (_, r) => `ROLE:${r}`));
    let cur = this;
    while (cur) {
      for (const t of tags) {
        if (t.startsWith('ROLE:')) {
          if (cur.getAttribute && cur.getAttribute('role') === t.slice(5)) return cur;
        } else if (cur.tagName === t.toUpperCase()) return cur;
      }
      cur = cur.parent;
    }
    return null;
  }, getAttribute(n) { return this._attrs && this._attrs[n]; } };
  if (parent) parent.child = n;
  return n;
}
const root = el('div');
const btn = el('button', root);
assert.strictEqual(spaceTogglesPlayback(btn), false); // focus on button → don't double-toggle
const plain = el('img', root);
assert.strictEqual(spaceTogglesPlayback(plain), true);
assert.strictEqual(spaceTogglesPlayback(null), true);
const sw = el('div', root);
sw._attrs = { role: 'switch' };
assert.strictEqual(spaceTogglesPlayback(sw), false);

// --- frameErrorFallbackSrc ---
assert.strictEqual(frameErrorFallbackSrc(0, ['a.jpg', 'b.jpg']), 'a.jpg');
assert.strictEqual(frameErrorFallbackSrc(1, ['a.jpg', 'b.jpg']), 'b.jpg');
assert.strictEqual(frameErrorFallbackSrc(99, ['a.jpg', 'b.jpg']), 'b.jpg'); // clamp
assert.strictEqual(frameErrorFallbackSrc(0, []), '');
assert.strictEqual(frameErrorFallbackSrc(0, null), '');

// --- shouldRefreshSystemPanel ---
assert.strictEqual(shouldRefreshSystemPanel({}), true);
assert.strictEqual(shouldRefreshSystemPanel({ hovered: true }), false);
assert.strictEqual(shouldRefreshSystemPanel({ focused: true }), false);
assert.strictEqual(shouldRefreshSystemPanel({ scrollTop: 12 }), false);
assert.strictEqual(shouldRefreshSystemPanel({ scrollTop: 0 }), true);

// --- emptyStateKind ---
assert.strictEqual(emptyStateKind({ error: 'boom' }).kind, 'error');
assert.strictEqual(emptyStateKind({ analyzing: true }).kind, 'analyzing');
assert.strictEqual(emptyStateKind({ filtersActive: true }).kind, 'filtered');
assert.strictEqual(emptyStateKind({}).kind, 'empty');
assert.ok(/filter/i.test(emptyStateKind({ filtersActive: true }).message));

// --- sparklineHasData ---
assert.strictEqual(sparklineHasData([0, 0, 0]), false);
assert.strictEqual(sparklineHasData([0, 3, 0]), true);
assert.strictEqual(sparklineHasData(null), false);

// --- labelCounts (chip frequency helper — shipped + used by renderObjectFilters) ---
const lc = labelCounts([
  new Set(['person', 'dog']),
  new Set(['person']),
  ['cat', 'person'],
]);
assert.strictEqual(lc[0].label, 'person');
assert.strictEqual(lc[0].count, 3);
assert.deepStrictEqual(
  lc.find(x => x.label === 'dog'),
  { label: 'dog', count: 1 }
);
assert.deepStrictEqual(labelCounts([]), []);
assert.deepStrictEqual(labelCounts(null), []);

console.log('ux_polish_helpers: all assertions passed');
