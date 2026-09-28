// Contract: Auto-refresh OFF must close the EventSource, stop polling, and
// ignore inbound SSE. Run: node tests/test_live_mode.js
const assert = require('assert');
const { src: html, grabFn } = require('./helpers/load.cjs');

assert.match(html, /let liveEventSource = null/);
assert.match(html, /function stopEventStream\(\)/);
assert.match(html, /liveEventSource\.close\(\)/);
assert.match(html, /function toggleLiveMode\(on\)/);

const toggle = grabFn('toggleLiveMode');
assert.match(toggle, /stopEventStream\(\)/);
assert.match(toggle, /stopPolling\(\)/);
assert.match(toggle, /startEventStream\(\)/);

const startEs = grabFn('startEventStream');
assert.match(startEs, /if \(!state\.isLive\) return/);
assert.match(startEs, /es\.onerror = \(\) => \{[\s\S]*if \(!state\.isLive\) return/);
assert.match(startEs, /addEventListener\('open', \(\) => \{[\s\S]*if \(!state\.isLive\) return/);

console.log('live-mode: EventSource is closable and Live OFF is guarded');
