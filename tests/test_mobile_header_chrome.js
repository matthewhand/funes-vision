// Contract: phone header is one compact bar (camera + view tabs). CSS-only
// at 768/960 — do not hide the time row or move Help/Refresh (#15/#18).
// Run: node tests/test_mobile_header_chrome.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

function braceBody(src, openIdx) {
  let depth = 0;
  for (let i = openIdx; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(openIdx + 1, i);
    }
  }
  return '';
}

function mediaContaining(query, needle) {
  const marker = '@media (max-width: ' + query + ')';
  let from = 0;
  while (true) {
    const idx = html.indexOf(marker, from);
    if (idx < 0) return null;
    const brace = html.indexOf('{', idx);
    if (brace < 0) return null;
    const body = braceBody(html, brace);
    if (body.includes(needle)) return body;
    from = idx + marker.length;
  }
}

function rule(block, selector) {
  const re = new RegExp(
    selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]+)}'
  );
  const m = block.match(re);
  return m ? m[1] : '';
}

// First paint: time slider folded so it does not own a tall band.
assert.ok(/class="time-slider-wrapper range-default range-collapsed"/.test(html),
  '.time-slider-wrapper must first-paint range-default range-collapsed');

// Desktop one-bar: base (non-media) .header-content stays nowrap.
const baseHeader = html.match(/\.header-content \{\s*max-width:\s*1600px;[\s\S]*?flex-wrap:\s*nowrap;/);
assert(baseHeader, 'base .header-content must set flex-wrap: nowrap');
assert.ok(
  html.indexOf('@media') === -1 || html.indexOf('@media') > baseHeader.index,
  'flex-wrap: nowrap must live on the base .header-content rule, not only in a media query'
);

const locked = ['btn-help', 'btn-refresh', 'filter-tabs', 'search-input', 'btn-switch-feed', 'live-toggle'];
for (const id of locked) {
  assert.ok(new RegExp('id="' + id + '"').test(html), 'must keep id="' + id + '"');
}

const header = (html.match(/<header>([\s\S]*?)<\/header>/) || [, ''])[1];
assert.ok(/id="btn-help"/.test(header), 'btn-help stays in <header> (#18)');
assert.ok(/id="btn-refresh"/.test(header), 'btn-refresh stays in <header> (#18)');

const m960 = mediaContaining('960px', '.tab-cluster');
assert(m960, '@media (max-width: 960px) with .tab-cluster not found');
const tabs960 = rule(m960, '.header-content .tab-cluster') || rule(m960, '.tab-cluster');
assert(/order:\s*0/.test(tabs960), '960 .tab-cluster must be order: 0, not 3');
assert(!/order:\s*3/.test(tabs960), '960 .tab-cluster must not use order: 3');
assert(/flex:\s*1 1 auto/.test(tabs960), '960 .tab-cluster must be flex: 1 1 auto');
assert(!/flex:\s*1 1 100%/.test(tabs960), '960 .tab-cluster must not be flex: 1 1 100%');
assert(/min-width:\s*0/.test(tabs960), '960 .tab-cluster must set min-width: 0');
const stats960 = rule(m960, '#stats-label');
assert(/display:\s*none/.test(stats960), '#stats-label must hide under 960px');

const m768 = mediaContaining('768px', '.header-content');
assert(m768, '@media (max-width: 768px) with .header-content not found');
const hc768 = rule(m768, '.header-content');
assert(/flex-direction:\s*row/.test(hc768), '768 .header-content must be flex-direction: row');
assert(!/flex-direction:\s*column/.test(hc768), '768 .header-content must not be column');
assert(/flex-wrap:\s*wrap/.test(hc768), '768 .header-content must wrap (controls on second row)');
assert(/flex:\s*0 0 auto/.test(rule(m768, '.logo-area')), '768 .logo-area must be flex: 0 0 auto');
const tabs768 = rule(m768, '.header-content .tab-cluster') || rule(m768, '.tab-cluster');
assert(/order:\s*0/.test(tabs768), '768 .tab-cluster must be order: 0');
assert(/overflow-x:\s*auto/.test(tabs768), '768 .tab-cluster must overflow-x: auto');
assert(/flex:\s*1 1 100%/.test(rule(m768, '.controls-wrapper')),
  '768 .controls-wrapper must be flex: 1 1 100% (second row)');

assert.ok(!/\.time-slider-wrapper\s*\{[^}]*display:\s*none/.test(m768),
  'must not hide the time row at 768 (#15)');
assert.ok(!/\.time-slider-wrapper\s*\{[^}]*display:\s*none/.test(m960),
  'must not hide the time row at 960 (#15)');

console.log('mobile-header-chrome: all assertions passed');
