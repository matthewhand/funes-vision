// Contract: Auto-refresh OFF must close the EventSource, stop polling, and
// ignore inbound SSE. Run: node tests/test_live_mode.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

assert.match(html, /let liveEventSource = null/);
assert.match(html, /function stopEventStream\(\)/);
assert.match(html, /liveEventSource\.close\(\)/);
assert.match(html, /function toggleLiveMode\(on\)/);

const toggle = html.match(/function toggleLiveMode\(on\) \{([\s\S]*?)\n    \}/);
assert(toggle, 'toggleLiveMode body not found');
assert.match(toggle[1], /stopEventStream\(\)/);
assert.match(toggle[1], /stopPolling\(\)/);
assert.match(toggle[1], /startEventStream\(\)/);

const startEs = html.match(/function startEventStream\(\) \{([\s\S]*?)\n    \}/);
assert(startEs, 'startEventStream body not found');
assert.match(startEs[1], /if \(!state\.isLive\) return/);
assert.match(startEs[1], /es\.onerror = \(\) => \{[\s\S]*if \(!state\.isLive\) return/);
assert.match(startEs[1], /addEventListener\('open', \(\) => \{[\s\S]*if \(!state\.isLive\) return/);

console.log('live-mode: EventSource is closable and Live OFF is guarded');
