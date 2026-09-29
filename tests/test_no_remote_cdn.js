// Contract: the gallery must not phone Google / unpkg / other CDNs.
// Icons ship as a pinned local Lucide UMD next to index.html (copied into
// each nginx root by create-index.sh). Run: node tests/test_no_remote_cdn.js
//
// Extended to the diagram set (docs/diagrams/*.html), which used to carry a
// `<link>` to the Google Fonts /css2 endpoint -- the exact behaviour this test
// bans for the app, invisible to CI because the original scan only read
// index.html. See docs/diagrams/fonts/README.md and issue #44.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { src: html } = require('./helpers/load.cjs');

const root = __dirname + '/..';
const createIndex = fs.readFileSync(root + '/create-index.sh', 'utf8');

const remote = html.match(/https?:\/\/[^\s"'<>]+/gi) || [];
const cdn = remote.filter((u) =>
  /fonts\.googleapis\.com|fonts\.gstatic\.com|unpkg\.com|cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|ajax\.googleapis\.com/i.test(u)
);
assert.deepStrictEqual(cdn, [], 'index.html must not load third-party CDNs: ' + cdn.join(', '));

// Same denylist as the index.html scan above, reused for the diagram set below.
const CDN_HOST_RE =
  /fonts\.googleapis\.com|fonts\.gstatic\.com|unpkg\.com|cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|ajax\.googleapis\.com/i;

assert.ok(
  /<script[^>]+src="lucide\.min\.js"/.test(html),
  'index.html must load the local lucide.min.js (not a CDN)'
);
assert.ok(
  fs.existsSync(root + '/lucide.min.js'),
  'lucide.min.js must exist at the repo root'
);
const bytes = fs.statSync(root + '/lucide.min.js').size;
assert.ok(bytes > 10_000, 'lucide.min.js looks empty (' + bytes + ' bytes)');

// create-index.sh must sync the UMD into each camera web root (nginx).
assert.ok(
  /lucide\.min\.js/.test(createIndex),
  'create-index.sh must copy lucide.min.js into IMAGE_DIR'
);

// ---------------------------------------------------------------------------
// docs/diagrams: same rule, plus the fonts must be vendored and present.
// ---------------------------------------------------------------------------

// A bare XML namespace is an identifier, not something the browser fetches, so
// it is the one absolute URL allowed to survive in a diagram. Anything else
// with a host in it is a network dependency and is rejected.
const NON_FETCH_URLS = ['http://www.w3.org/2000/svg', 'http://www.w3.org/1999/xlink'];

const REF_ATTR_RE =
  /\b(?:href|src|xlink:href|poster|srcset|data|action|formaction)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi;
const CSS_URL_RE = /url\(\s*([^)]*?)\s*\)/gi;
const CSS_IMPORT_RE = /@import\b[^;]*/gi;
const HTML_COMMENT_RE = /<!--[\s\S]*?-->/g;
const CSS_COMMENT_RE = /\/\*[\s\S]*?\*\//g;

const unquote = (v) => v.replace(/^["']|["']$/g, '').trim();

// Classify a single reference value. Returns null when local/allowed, or a
// human-readable reason when it would cause a third-party request.
function remoteReason(value) {
  const v = unquote(value);
  if (!v || v.startsWith('#')) return null; // fragment
  if (NON_FETCH_URLS.indexOf(v) !== -1) return null; // XML namespace
  if (/^data:image\//i.test(v) || /^data:font\//i.test(v) || /^data:application\/(x-)?font/i.test(v)) {
    return null; // inlined payload
  }
  const lower = v.toLowerCase();
  const protocolRelative = lower.startsWith('//');
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(v);
  const schemeRelative = hasScheme && !/^(data|mailto|tel|javascript|about):/i.test(v);
  if (!protocolRelative && !schemeRelative) return null; // plain relative path
  if (/^javascript:/i.test(lower)) return 'executable javascript: URL in a diagram';
  return 'external reference: ' + v;
}

// Collect every reference a document can make, from the attributes above plus
// inline CSS url()/@import. Comments are stripped: they are never fetched, and
// leaving them in would make the test fail on prose about a CDN.
function referencesOf(source) {
  const body = source.replace(HTML_COMMENT_RE, '').replace(CSS_COMMENT_RE, '');
  const refs = [];
  let m;
  REF_ATTR_RE.lastIndex = 0;
  while ((m = REF_ATTR_RE.exec(body)) !== null) refs.push(m[1]);
  CSS_URL_RE.lastIndex = 0;
  while ((m = CSS_URL_RE.exec(body)) !== null) refs.push(m[1]);
  CSS_IMPORT_RE.lastIndex = 0;
  while ((m = CSS_IMPORT_RE.exec(body)) !== null) refs.push(m[0]);
  return refs;
}

// The whole-document net: no host-bearing URL anywhere outside the namespace
// allowlist. Catches a remote fetch smuggled in somewhere the attribute scan
// above does not model (an inline <script>, a meta refresh, ...).
function scanForHosts(label, source) {
  const body = source.replace(HTML_COMMENT_RE, '').replace(CSS_COMMENT_RE, '');
  const hits = (body.match(/(?:https?:)?\/\/[^\s"'<>),;]+/gi) || [])
    .map((u) => u.replace(/[),;]+$/, ''))
    .filter((u) => !NON_FETCH_URLS.some((ok) => u === ok || u.indexOf(ok + '/') === 0))
    // A protocol-relative //host/path is still a request, but a bare // inside
    // JS or CSS is a comment -- so only keep ones shaped like an authority.
    .filter((u) => /^https?:/i.test(u) || /^\/\/[a-z0-9-]+(\.[a-z0-9-]+)+/i.test(u));
  assert.deepStrictEqual(
    hits,
    [],
    label + ' must not reference an external host (offline-first): ' + hits.join(', ')
  );
}

const DIAGRAMS = path.join(root, 'docs', 'diagrams');
assert.ok(fs.existsSync(DIAGRAMS), 'docs/diagrams must exist');

const diagrams = fs
  .readdirSync(DIAGRAMS)
  .filter((f) => f.endsWith('.html'))
  .sort();

// Guard against a vacuous pass: if the glob ever stops matching (renamed
// directory, files moved to .htm), the loop below would trivially succeed.
// The floor is the real count -- 16 numbered diagrams + _template.html -- so
// deleting a diagram fails here instead of quietly shrinking the set. Raise it
// in the same commit that adds a diagram.
assert.ok(
  diagrams.length >= 17,
  'expected the 16 numbered diagrams + _template.html under docs/diagrams, found ' + diagrams.length
);

for (const name of diagrams) {
  const file = path.join(DIAGRAMS, name);
  const source = fs.readFileSync(file, 'utf8');
  const label = 'docs/diagrams/' + name;

  const offenders = referencesOf(source).map(remoteReason).filter(Boolean);
  assert.deepStrictEqual(
    offenders,
    [],
    label + ' must not load remote fonts/stylesheets/images: ' + offenders.join('; ')
  );
  scanForHosts(label, source);

  const cdnHits = (source.match(/https?:\/\/[^\s"'<>]+/gi) || []).filter((u) => CDN_HOST_RE.test(u));
  assert.deepStrictEqual(cdnHits, [], label + ' must not load third-party CDNs: ' + cdnHits.join(', '));

  // Every diagram must pull the same vendored stylesheet, so the 17 HTML
  // files cannot drift apart typographically.
  const sheets = (source.match(/<link[^>]+rel="stylesheet"[^>]*>/gi) || []).map((t) =>
    (/href="([^"]+)"/.exec(t) || [])[1]
  );
  assert.deepStrictEqual(
    sheets,
    ['fonts.css'],
    label + ' must link exactly the local fonts.css, got: ' + JSON.stringify(sheets)
  );

  // The families the diagram set is designed around.
  for (const family of ['Geist', 'Geist Mono', 'Instrument Serif']) {
    assert.ok(
      source.indexOf("'" + family + "'") !== -1,
      label + ' must still use the ' + family + ' stack declared in fonts.css'
    );
  }
}

// --- the vendored font plumbing itself -------------------------------------

const FONTS_CSS = path.join(DIAGRAMS, 'fonts.css');
assert.ok(fs.existsSync(FONTS_CSS), 'docs/diagrams/fonts.css must exist');
const fontsCss = fs.readFileSync(FONTS_CSS, 'utf8');
scanForHosts('docs/diagrams/fonts.css', fontsCss);
assert.ok(
  !/@import\b/i.test(fontsCss),
  'docs/diagrams/fonts.css must not @import anything (loaders are a bypass)'
);

// Every face the diagrams ask for must resolve to a real file, so a renamed or
// forgotten woff2 fails here instead of silently falling back to a system font.
const REQUIRED_FACES = [
  { file: 'Geist-Variable.woff2', family: 'Geist' },
  { file: 'GeistMono-Variable.woff2', family: 'Geist Mono' },
  { file: 'InstrumentSerif-Regular.woff2', family: 'Instrument Serif' },
  { file: 'InstrumentSerif-Italic.woff2', family: 'Instrument Serif' },
];

const declared = (fontsCss.match(/url\(\s*['"]?([^'")]+)['"]?\s*\)/gi) || []).map((u) =>
  unquote(/url\(\s*['"]?([^'")]+)['"]?\s*\)/i.exec(u)[1])
);
assert.ok(
  declared.length >= REQUIRED_FACES.length,
  'docs/diagrams/fonts.css must declare at least ' +
    REQUIRED_FACES.length +
    ' @font-face src urls, found ' +
    declared.length
);

for (const face of REQUIRED_FACES) {
  const rel = 'fonts/' + face.file;
  assert.ok(
    fontsCss.indexOf("url('" + rel + "')") !== -1,
    'docs/diagrams/fonts.css must declare url(\'' + rel + '\') for ' + face.family
  );
  assert.ok(
    fontsCss.indexOf("font-family: '" + face.family + "'") !== -1,
    'docs/diagrams/fonts.css must declare font-family \'' + face.family + '\''
  );
  const abs = path.join(DIAGRAMS, 'fonts', face.file);
  assert.ok(fs.existsSync(abs), 'vendored font missing on disk: docs/diagrams/fonts/' + face.file);
  const size = fs.statSync(abs).size;
  assert.ok(size > 5_000, 'vendored font looks empty: docs/diagrams/fonts/' + face.file + ' (' + size + ' bytes)');
}

// Anything fonts.css points at must exist, not just the four we know about.
for (const rel of declared) {
  const reason = remoteReason(rel);
  assert.strictEqual(reason, null, 'docs/diagrams/fonts.css must not reference remotely: ' + reason);
  assert.ok(
    fs.existsSync(path.join(DIAGRAMS, rel)),
    'docs/diagrams/fonts.css references a missing file: ' + rel
  );
}

// Redistribution is only lawful while the OFL text ships with the binaries.
for (const license of ['OFL-Geist.txt', 'OFL-InstrumentSerif.txt']) {
  const abs = path.join(DIAGRAMS, 'fonts', license);
  assert.ok(fs.existsSync(abs), 'missing font license docs/diagrams/fonts/' + license);
  const text = fs.readFileSync(abs, 'utf8');
  assert.ok(
    /SIL Open Font License, Version 1\.1/i.test(text),
    'docs/diagrams/fonts/' + license + ' must contain the OFL 1.1 text'
  );
  assert.ok(
    /^Copyright/m.test(text),
    'docs/diagrams/fonts/' + license + ' must retain its copyright notice'
  );
}
assert.ok(
  fs.existsSync(path.join(DIAGRAMS, 'fonts', 'README.md')),
  'docs/diagrams/fonts/README.md must record provenance and checksums'
);

console.log(
  'no-remote-cdn: all assertions passed (' + diagrams.length + ' diagrams, ' + declared.length + ' vendored faces)'
);
