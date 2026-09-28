// Regression guard: every id the SPA looks up via getElementById() must exist
// as an id="..." in the markup — except a small allowlist of elements created
// at runtime. Catches a broken/removed/renamed DOM reference (a silent runtime
// null) that the pure-helper tests can't see. Run: node tests/test_dom_refs.js
const assert = require('assert');
const { src, ids } = require('./helpers/load.cjs');

const refs = new Set([...src.matchAll(/getElementById\(["']([^"']+)["']\)/g)].map(m => m[1]));
const defined = new Set(ids());

// A scan that finds nothing asserts nothing, so an empty reference set is a
// silent pass, not a clean bill of health. This is the tripwire for the
// index.html -> js/*.js split: the moment the getElementById() calls move out
// of index.html, this fails here and says so, instead of the loop below
// iterating zero times and reporting success. When that happens the scan has to
// be repointed at the new module files, not deleted.
assert.ok(refs.size > 0,
  'found 0 getElementById() references in ' + __dirname + '/../index.html: the SPA moved ' +
  'out of this file (or the scan broke). Repoint the scan, do not let it pass empty.');
assert.ok(defined.size > 0,
  'found 0 id="" attributes in index.html: the markup moved, so every ref would look unresolved.');

// Elements built at runtime via document.createElement(...).id = '...' (no
// static markup). Keep this list tiny and justified.
const DYNAMIC = new Set([
  'activity-pill',  // lazy get-or-create live-activity pill (updateActivityPill)
]);

const missing = [...refs].filter(id => !defined.has(id) && !DYNAMIC.has(id)).sort();
assert.deepStrictEqual(missing, [],
  'getElementById() refs with no matching id="" in markup (broken DOM ref?): ' + missing.join(', '));

console.log(`dom-refs: all ${refs.size} getElementById refs resolve (${DYNAMIC.size} dynamic allowlisted)`);
