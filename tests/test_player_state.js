// Node assert tests for the pure player-state helpers in index.html:
// playbackToggleState() (play/pause button) and clipButtonState() (GIF export
// converting state). Run: node tests/test_player_state.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const grab = (name) => {
  const m = html.match(
    new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name)
  );
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
};
eval(grab('playbackToggleState'));
eval(grab('clipButtonState'));

// Play/pause toggle: text + pressed state; label stays "Play or pause".
assert.deepStrictEqual(playbackToggleState(true), { playing: true, text: 'Pause' });
assert.deepStrictEqual(playbackToggleState(false), { playing: false, text: 'Play' });
assert.strictEqual(playbackToggleState(1).text, 'Pause');
assert.strictEqual(playbackToggleState(0).text, 'Play');
assert.strictEqual(playbackToggleState(undefined).playing, false);

// GIF export: converting disables the button and shows a spinner.
assert.deepStrictEqual(clipButtonState('building'), {
  text: 'Building…', disabled: true, busy: true, spinner: true,
});

// Idle / completion / error all restore the resting button.
const idle = { text: 'Download GIF', disabled: false, busy: false, spinner: false };
assert.deepStrictEqual(clipButtonState('idle'), idle);
assert.deepStrictEqual(clipButtonState('done'), idle);
assert.deepStrictEqual(clipButtonState('error'), idle);
assert.deepStrictEqual(clipButtonState(undefined), idle);

console.log('playbackToggleState/clipButtonState: all assertions passed');
