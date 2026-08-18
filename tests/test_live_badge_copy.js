// Header Live badge copy. Run: node tests/test_live_badge_copy.js
const fs = require('fs');
const assert = require('assert');
const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const a = html.match(/pure:streamStatusText ===\n([\s\S]*?)\n\s*\/\/ === \/pure:streamStatusText/);
const b = html.match(/pure:liveBadgeCopy ===\n([\s\S]*?)\n\s*\/\/ === \/pure:liveBadgeCopy/);
assert(a && b, 'sentinels missing');
eval(a[1]);
eval(b[1]);

assert.deepStrictEqual(liveBadgeCopy(false, true, 1, 2), { text: 'Auto-refresh: OFF', tone: 'off' });
assert.strictEqual(liveBadgeCopy(true, true, 1000, 1005).tone, 'live');
assert.strictEqual(liveBadgeCopy(true, true, 1000, 1005).text, 'Live');
assert.strictEqual(liveBadgeCopy(true, false).tone, 'poll');
assert.match(liveBadgeCopy(true, false).text, /Poll/i);
assert.strictEqual(liveBadgeCopy(true, true, 1000, 1040).tone, 'stale');
assert.match(liveBadgeCopy(true, true, 1000, 1040).text, /Stale/i);

// First paint: isLive defaults true, so markup must already say Live (not
// "Auto-refresh: ON") and name the control as a toggle.
const liveText = html.match(/id="live-text">([^<]*)/);
assert(liveText, '#live-text missing');
assert.strictEqual(liveText[1], 'Live', 'first-paint live text must match isLive default');
assert.ok(/aria-label="Live, toggle live updates"/.test(html),
  'first-paint aria-label must be Live, toggle live updates');
assert.ok(/setAttribute\('aria-label',\s*copy\.text \+ ', toggle live updates'\)/.test(html),
  'paintLiveBadge must keep "toggle live updates" on the accessible name');

console.log('liveBadgeCopy: all assertions passed');
