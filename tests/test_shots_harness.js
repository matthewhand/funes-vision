// Screenshot harness contracts. Run: node tests/test_shots_harness.js
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.join(__dirname, '..');
const shots = fs.readFileSync(path.join(root, 'tools/screenshots/shots.js'), 'utf8');
const run = fs.readFileSync(path.join(root, 'tools/screenshots/run_shots.sh'), 'utf8');
const status = JSON.parse(fs.readFileSync(
  path.join(root, 'tools/screenshots/fixtures/api/status.json'), 'utf8'));
const analysis = JSON.parse(fs.readFileSync(
  path.join(root, 'tools/screenshots/fixtures/gallery/analysis.json'), 'utf8'));

assert.ok(!/waitForLoadState\('networkidle'\)/.test(shots),
  'shots.js must not wait for networkidle (SSE stub never idles)');
assert.ok(/waitForLoadState\('domcontentloaded'\)/.test(shots),
  'settle() should wait for domcontentloaded');
assert.ok(/20260618101522301/.test(shots),
  'lightbox shot must click the analyzed courier still, not the first All card');
assert.ok(/#lightbox\.active/.test(shots),
  'lightbox shot must wait until #lightbox.active');
assert.ok(/USER-GUIDE\.html/.test(shots), 'must capture Help');
assert.ok(/#btn-integrations/.test(shots), 'must capture Integrations');
assert.ok(/data-pill="night"/.test(shots), 'must capture Night pills');
assert.ok(!/\.visit-card/.test(shots), 'dead .visit-card selector must stay gone');

assert.ok(/PUBLISH_GUIDE_IMG/.test(run), 'run_shots.sh must offer a publish map');
assert.ok(/desktop-01-timeline-visits:timeline/.test(run),
  'publish map must rename desktop-01 → timeline.png');
assert.ok(/mobile-05-night-pills:night-pills/.test(run),
  'publish map must include night-pills');

assert.strictEqual(status.queue.deep_eta_s, 42, 'fixture status needs deep_eta_s');
assert.strictEqual(status.queue.deep_s_per_frame, 42.0, 'fixture status needs deep_s_per_frame');
assert.strictEqual(status.settings.max_scans_per_image, 2);
assert.ok(
  analysis['10.0.0.21_01_20260618170000000_MOTDEC.jpg']
    && analysis['10.0.0.21_01_20260618170000000_MOTDEC.jpg'].fast_pass === 'negative',
  'newest empty still must be CLEAR so All-grid lead is labelled'
);

console.log('shots-harness: all assertions passed');
