// Contract: Timeline visits are a CSS-owned log, not JS card chrome.
// CSS owns density so media queries win. Run: node tests/test_visit_row_log.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const ev = html.match(/function renderEventsView\([^)]*\) \{([\s\S]*?)\n    function openEventPlayer/);
assert(ev, 'renderEventsView not found');
const body = ev[1];

assert.ok(/list\.className = 'visit-log'/.test(body), 'list must be class visit-log');
assert.ok(!/list\.style\.cssText/.test(body), 'list must not set inline layout');
assert.ok(!/row\.style\.cssText/.test(body), 'row must not set card chrome inline');
assert.ok(!/data-lucide/.test(body), 'lucide icons must be gone from visit rows');
assert.ok(!/lucide\.createIcons\(\)/.test(body), 'renderEventsView must not call lucide.createIcons');
assert.ok(!/v\.label === 'person' \? 'user'/.test(body), 'icon map must be gone');
assert.ok(!/play-circle/.test(body), 'play lucide icon must be gone');
assert.ok(!/width:80px/.test(body), 'thumb size must not be inline');

for (const cls of ['visit-body', 'visit-title', 'visit-label', 'visit-meta', 'visit-caption', 'visit-ongoing']) {
  assert.ok(body.includes(cls), 'row markup must include ' + cls);
}

assert.ok(/class="card-img unloaded"/.test(body), 'thumb stays lazy card-img');
assert.ok(/visitAltText\(/.test(body), 'thumb alt stays visitAltText');
assert.ok(/setAttribute\('aria-label'/.test(body), 'row keeps aria-label');
assert.ok(/bindActivatable\(row/.test(body), 'row stays keyboard-activatable');
assert.ok(/escapeHtml\(v\.label\)/.test(body), 'label stays escaped');
assert.ok(/escapeHtml\(v\.caption\)/.test(body), 'caption stays escaped');

const log = html.match(/\.visit-log\s*\{([^}]*)\}/);
assert(log, '.visit-log rule missing');
assert.ok(/gap:\s*0/.test(log[1]), '.visit-log gap must be 0');

const row = html.match(/\.visit-row\s*\{([^}]*)\}/);
assert(row, '.visit-row rule missing');
assert.ok(/border-bottom:/.test(row[1]), '.visit-row is a log line (border-bottom, not a card)');

assert.ok(/\.visit-row\s+\.card-img\s*\{[^}]*width:\s*48px/.test(html) &&
          /\.visit-row\s+\.card-img\s*\{[^}]*height:\s*48px/.test(html),
  'desktop thumbs are 48px');

const meta = html.match(/\.visit-meta,\s*\.visit-caption\s*\{([^}]*)\}/) ||
             html.match(/\.visit-meta\s*\{([^}]*)\}/);
assert(meta, '.visit-meta rule missing');
assert.ok(/white-space:\s*nowrap/.test(meta[1]), 'meta is one nowrap line');
assert.ok(/text-overflow:\s*ellipsis/.test(meta[1]), 'meta truncates with ellipsis');

assert.ok(/@media\s*\(max-width:\s*768px\)\s*\{\s*\.visit-row\s*\{[^}]*contain-intrinsic-size:\s*auto 48px;\s*\}\s*\.visit-row\s+\.card-img\s*\{[^}]*width:\s*40px/.test(html),
  '768px rows use 48px intrinsic size and 40px thumbs');

assert.ok(/@media\s*\(pointer:\s*coarse\)[\s\S]*?\.visit-row\s*\{[^}]*min-height:\s*44px/.test(html),
  'coarse pointer visit rows are at least 44px');

console.log('visit-row-log: CSS-owned log recipe verified');
