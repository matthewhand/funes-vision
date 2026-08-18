// Contract: the gallery must not phone Google / unpkg / other CDNs.
// Icons ship as a pinned local Lucide UMD next to index.html (copied into
// each nginx root by create-index.sh). Run: node tests/test_no_remote_cdn.js
const fs = require('fs');
const assert = require('assert');

const root = __dirname + '/..';
const html = fs.readFileSync(root + '/index.html', 'utf8');
const createIndex = fs.readFileSync(root + '/create-index.sh', 'utf8');

const remote = html.match(/https?:\/\/[^\s"'<>]+/gi) || [];
const cdn = remote.filter((u) =>
  /fonts\.googleapis\.com|fonts\.gstatic\.com|unpkg\.com|cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|ajax\.googleapis\.com/i.test(u)
);
assert.deepStrictEqual(cdn, [], 'index.html must not load third-party CDNs: ' + cdn.join(', '));

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

console.log('no-remote-cdn: all assertions passed');
