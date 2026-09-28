// Contract test: index.html must ship a global :focus-visible ring (so every
// keyboard-focusable control shows focus) and coarse-pointer (touch) targets of
// at least 44px. Run: node tests/test_a11y_css.js
const assert = require('assert');
const { src: html, mediaBodies } = require('./helpers/load.cjs');

// Global focus-visible outline (not just the one-off .image-card rule).
assert(/\bbutton[^{]*:focus-visible[\s\S]{0,200}?outline:/.test(html) ||
       /:focus-visible\s*\{[^}]*outline:/.test(html),
  'expected a global :focus-visible { outline: ... } rule');

// A coarse-pointer media block bumping touch targets to >= 44px.
const coarse = mediaBodies(/pointer:\s*coarse/);
assert(coarse.length, 'expected an @media (pointer: coarse) block');
assert(/min-width:\s*44px/.test(coarse[0]) && /min-height:\s*44px/.test(coarse[0]),
  'coarse-pointer block must set min-width/min-height: 44px on small controls');
assert(/action-icon-btn/.test(coarse[0]),
  'coarse-pointer block should enlarge the card action buttons (.action-icon-btn)');

console.log('a11y-css: all assertions passed');
