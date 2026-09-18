// Contract: lightbox chrome overlays the JPEG instead of shrinking it.
// Header/footer are absolute gradient overlays; the image fills the body;
// close is always-on (not inside the header); Escape still closes.
// Run: node tests/test_lightbox_chrome.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

function rules(selector) {
  const re = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}', 'g');
  const found = [...html.matchAll(re)].map(m => m[1]);
  assert(found.length, selector + ' rule not found');
  return found;
}
function rule(selector) {
  return rules(selector)[0];
}

const headerBlocks = rules('.lightbox-header');
assert(headerBlocks.some(b => /position:\s*absolute/.test(b)),
  '.lightbox-header must be position:absolute overlay');
assert(headerBlocks.some(b => /linear-gradient/.test(b)),
  '.lightbox-header must use a gradient overlay');

const footerBlocks = rules('.lightbox-footer');
assert(footerBlocks.some(b => /position:\s*absolute/.test(b)),
  '.lightbox-footer must be position:absolute overlay');
assert(footerBlocks.some(b => /linear-gradient/.test(b)),
  '.lightbox-footer must use a gradient overlay');

const img = rule('.lightbox-image');
assert(!/max-height:\s*80vh/.test(img), '.lightbox-image must not cap at 80vh');
assert(/object-fit:\s*contain/.test(img), '.lightbox-image must object-fit:contain');

const wrap = rule('.lightbox-image-wrapper');
assert(!/max-width:\s*85%/.test(wrap), '.lightbox-image-wrapper must not cap at 85%');
assert.ok(!/\.lightbox-image-wrapper\s*\{[^}]*max-width:\s*95%/.test(html),
  'mobile must not re-cap the wrapper at 95%');

const closeBtn = rule('.lightbox-btn.btn-close');
assert(/position:\s*absolute/.test(closeBtn), '.lightbox-btn.btn-close must be absolute');
assert(/z-index:\s*6/.test(closeBtn), '.lightbox-btn.btn-close z-index must be 6');

const nav = rule('.nav-arrow');
assert(/position:\s*absolute/.test(nav), '.nav-arrow must be position:absolute');

function divInner(className) {
  const open = `<div class="${className}">`;
  const start = html.indexOf(open);
  assert(start !== -1, open + ' missing');
  let i = start + open.length, depth = 1;
  while (i < html.length && depth > 0) {
    const openAt = html.indexOf('<div', i);
    const closeAt = html.indexOf('</div>', i);
    assert(closeAt !== -1, 'unclosed ' + className);
    if (openAt !== -1 && openAt < closeAt) { depth++; i = openAt + 4; }
    else { depth--; if (depth === 0) return html.slice(start + open.length, closeAt); i = closeAt + 6; }
  }
  assert.fail('unclosed ' + className);
}
assert.ok(!/id="lightbox-close"/.test(divInner('lightbox-header')),
  '#lightbox-close must not live inside .lightbox-header');

const lbStart = html.indexOf('id="lightbox"');
const lbEnd = html.indexOf('id="zone-editor"');
assert(lbStart !== -1 && lbEnd > lbStart, '#lightbox markup missing');
const lbMarkup = html.slice(lbStart, lbEnd);
assert.ok(/id="lightbox-close"/.test(lbMarkup),
  '#lightbox-close must remain a child of #lightbox');

assert.ok(/if \(e\.key === 'Escape'\) closeLightbox\(\)/.test(html),
  'Escape must still close the lightbox');

const coarse = html.match(/@media[^{]*pointer:\s*coarse[^{]*\{([\s\S]*?)\n\s{0,6}\}/);
assert(coarse, 'expected an @media (pointer: coarse) block');
assert(/lightbox-btn/.test(coarse[1]) && /nav-arrow/.test(coarse[1]),
  'coarse-pointer block must enlarge .lightbox-btn and .nav-arrow');
assert(/min-width:\s*44px/.test(coarse[1]) && /min-height:\s*44px/.test(coarse[1]),
  'coarse-pointer lightbox controls must be 44px');

assert.ok(/\.lightbox\s*\{[\s\S]{0,240}?z-index:\s*2000/.test(html),
  'lightbox z-index 2000 must be kept');
assert.ok(/\.lightbox\s*\{[\s\S]{0,280}?background-color:\s*#04060a/.test(html),
  'lightbox background #04060a must be kept');

console.log('lightbox-chrome: all assertions passed');
