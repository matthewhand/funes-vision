// Every locally-referenced asset in index.html must be enumerated by EVERY
// deployment target, or it 404s in that one target and nowhere else.
//
// The index.html -> js/*.js + css/*.css split adds new assets, and there are four
// independent places that decide what ships:
//
//   create-index.sh             the nightly/FTP sweep that copies into /mnt/models
//   tools/deploy-webroot.sh     the explicit sync + HEAD-drift check
//   tools/demo/build_demo.py    the published static demo bundle
//   tools/screenshots/proxy.py  the fixture stub the screenshot/a11y gate serves
//
// Miss one and the page still works everywhere else, so nothing catches it. The
// asset list is DERIVED from index.html here, not hardcoded, which is the only
// way this class of bug gets caught at all -- the same approach as
// tests/test_stub_api_coverage.py derives the /api/* routes.
//
// Run: node tests/test_static_asset_manifest.js
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { src } = require('./helpers/load.cjs');

const ROOT = path.join(__dirname, '..');

const TARGETS = [
  { file: 'create-index.sh', label: 'create-index.sh (web-root sweep)' },
  { file: 'tools/deploy-webroot.sh', label: 'tools/deploy-webroot.sh (explicit sync)' },
  { file: 'tools/demo/build_demo.py', label: 'tools/demo/build_demo.py (published demo)' },
  { file: 'tools/screenshots/proxy.py', label: 'tools/screenshots/proxy.py (fixture stub)' },
];

// tools/demo/build_demo.py copies lucide.min.js / favicon.ico / icon.svg /
// manifest.json and nothing else, so the SPA's Help button
// (<a id="btn-help" href="USER-GUIDE.html">) 404s in the published demo -- the
// demo shim sets window.__IS_DEMO__ but the SPA never reads it, so the link is
// live. Pre-existing, and tools/ is not this phase's to change, so the
// deviation is recorded here instead of being dropped silently: the assertion
// below fails if the entry ever goes stale, and any asset NOT in this map has
// to be added to all four lists.
const KNOWN_GAPS = [
  { target: 'tools/demo/build_demo.py', asset: 'USER-GUIDE.html', why: 'the demo bundle ships no walkthrough' },
];

// ---- the assets index.html asks the browser for ---------------------------

// A src=/href= value is an asset only if it is a real, static, same-origin path:
// no ${} template interpolation, no URL, no data:, no fragment, no /api/ or
// runtime path. The gallery's `thumbs/${...}.jpg` and friends are filtered out
// because they are generated data, not shipped files.
const ASSET_ATTR = /\b(?:src|href)\s*=\s*["']([^"']*)["']/gi;
const RUNTIME_ONLY = /[${}]|^(?:[a-z]+:|\/\/|#|\/api\/|\/thumbs\/)/i;

function localAssets() {
  const out = new Set();
  for (const m of src.matchAll(ASSET_ATTR)) {
    const v = m[1].trim();
    if (!v || RUNTIME_ONLY.test(v)) continue;
    out.add(v.replace(/^\.\//, ''));
  }
  return [...out].sort();
}

const assets = localAssets();
assert.ok(assets.length >= 5,
  'expected the known static assets (lucide.min.js, manifest.json, favicon.ico, ' +
  'icon.svg, USER-GUIDE.html); the src/href scan found only ' + JSON.stringify(assets) +
  ' -- if index.html stopped referencing them, fix this test, do not let it pass on an empty set.');
for (const a of ['lucide.min.js', 'manifest.json', 'favicon.ico', 'icon.svg', 'USER-GUIDE.html']) {
  assert.ok(assets.includes(a), 'the src/href scan must find ' + a + ' (it found ' + JSON.stringify(assets) + ')');
}
// A referenced asset that is not in the repo is a 404 on every target, so it
// fails here first with a clearer message. USER-GUIDE.html is served from the
// web root but kept under docs/ in the repo; create-index.sh and
// deploy-webroot.sh both copy it up.
const IN_REPO = ['', 'docs/'];
for (const a of assets) {
  assert.ok(IN_REPO.some((prefix) => fs.existsSync(path.join(ROOT, prefix + a))),
    'index.html references ' + a + ' but there is no such file at the repo root (or under docs/)');
}

// ---- does each target enumerate it? ---------------------------------------

// Loose on purpose: a mention of the asset name anywhere in the target's code
// counts as coverage, so this test fires when an asset is genuinely ABSENT from
// a target, not when a list is spelled differently. Comments are stripped first
// (the deploy script's header prose names assets it does not copy), and the
// match is a whole word so `icon.svg` cannot be satisfied by `my-icon.svg.bak`.
function declaredAssets(file) {
  const abs = path.join(ROOT, file);
  assert.ok(fs.existsSync(abs), file + ' not found at the repo root');
  const code = fs.readFileSync(abs, 'utf8').replace(/^\s*#.*$/gm, '');
  assert.ok(code.trim().length > 0, file + ' is empty after comment stripping');
  const found = new Set();
  for (const m of code.matchAll(/[\w./-]+\.[a-z0-9]{2,5}/gi)) found.add(m[0].replace(/^\.\//, ''));
  return found;
}

const gaps = [];
for (const t of TARGETS) {
  const declared = declaredAssets(t.file);
  for (const a of assets) {
    const basename = path.basename(a);
    const covered = declared.has(a) || declared.has(basename) ||
      [...declared].some((d) => path.basename(d) === basename);
    const gap = KNOWN_GAPS.find((g) => g.target === t.file && g.asset === basename);
    if (gap) {
      // A recorded gap that has been fixed upstream: the entry is stale and
      // would hide a future omission, so fail rather than keep it.
      assert.ok(!covered, 'KNOWN_GAPS records ' + basename + ' as missing from ' + t.file +
        ' (' + gap.why + '), but ' + t.file + ' now declares it. Delete the entry so it ' +
        'is covered for real and the next asset is held to the same bar.');
      continue;
    }
    if (!covered) gaps.push(t.label + ' does not ship ' + a);
  }
}

assert.deepStrictEqual(gaps, [],
  'index.html references assets that a deployment target does not ship, so they 404 ' +
  'in that target only:\n  ' + gaps.join('\n  ') +
  '\nAdd each to the target(s) above (and re-check nginx.conf for the live roots).');

console.log(
  'static-asset-manifest: ' + assets.length + ' local assets (' + assets.join(', ') +
  ') covered by ' + TARGETS.length + ' deployment targets' +
  (KNOWN_GAPS.length ? '; ' + KNOWN_GAPS.length + ' recorded pre-existing gap(s)' : '')
);
