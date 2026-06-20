// Regression guard: every id the SPA looks up via getElementById() must exist
// as an id="..." in the markup — except a small allowlist of elements created
// at runtime. Catches a broken/removed/renamed DOM reference (a silent runtime
// null) that the pure-helper tests can't see. Run: node tests/test_dom_refs.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const refs = new Set([...html.matchAll(/getElementById\(["']([^"']+)["']\)/g)].map(m => m[1]));
const defined = new Set([...html.matchAll(/\bid=["']([^"']+)["']/g)].map(m => m[1]));

// Elements built at runtime via document.createElement(...).id = '...' (no
// static markup). Keep this list tiny and justified.
const DYNAMIC = new Set([
  'activity-pill',  // lazy get-or-create live-activity pill (updateActivityPill)
]);

const missing = [...refs].filter(id => !defined.has(id) && !DYNAMIC.has(id)).sort();
assert.deepStrictEqual(missing, [],
  'getElementById() refs with no matching id="" in markup (broken DOM ref?): ' + missing.join(', '));

console.log(`dom-refs: all ${refs.size} getElementById refs resolve (${DYNAMIC.size} dynamic allowlisted)`);
