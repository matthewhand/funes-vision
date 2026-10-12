#!/usr/bin/env python3
"""Offline privacy and synthetic provenance checks for the public frontend PR.

This is a file allowlist and digest guard, not proof of image distribution rights.
No private repository or live camera directory is ever read by this test.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'frontend'
source = (APP / 'index.html').read_text()
bridge = (APP / 'static/fixture-api.js').read_text()
fixture = json.loads((APP / 'static/fixture-data.json').read_text())
manifest = json.loads((APP / 'static/fixture-image-manifest.json').read_text())
expected = {'frontend/index.html', 'frontend/manifest.json', 'frontend/icon.svg',
            'frontend/favicon.ico', 'frontend/lucide.min.js',
            'frontend/images.json', 'frontend/analysis.json',
            'frontend/bursts.json', 'frontend/pins.json',
            'frontend/static/fixture-api.js', 'frontend/static/fixture-data.json',
            'frontend/static/fixture-image-manifest.json'}
expected.update(entry['path'] for entry in manifest)
actual = {p.relative_to(ROOT).as_posix() for p in APP.rglob('*') if p.is_file()}
assert actual == expected, f'Unreviewed frontend files: {sorted(actual - expected)}'

# Private camera IPs/hostnames/storage paths or personal region descriptions
# must never be reintroduced by copying the development checkout again.
for forbidden in ('10.0.0.', '/mnt/models', 'Webcam21', 'Webcam22',
                  'Northside', 'orange SUV', 'grey tiled porch',
                  'teamstinky', 'ubuntu-max', 'funes-vision-private',
                  'dogcam', 'github.com/matthewhand/funes-vision-private'):
    for path in (APP / 'index.html', APP / 'static/fixture-api.js',
                 APP / 'static/fixture-data.json', APP / 'analysis.json'):
        assert forbidden.lower() not in path.read_text().lower(), f'Restricted pattern in {path.name}: {forbidden}'

assert 'SYNTHETIC DEMO' in source and 'No cameras, models, live analysis, or server writes' in source
assert 'window.__FUNES_FIXTURE_ONLY__ = true' in bridge
assert "method !== 'GET'" in bridge and ', 403' in bridge
assert 'new EventSource(' not in bridge
assert '/tools/needle-client.js' not in source
assert 'const API_BASE = \'\'' in source
assert not re.search(r'(?<!\d)\d{1,3}(?:\.\d{1,3}){3}(?!\d)', source)

# Only already-public PR #1 synthetic bytes are allowed: duplicate files are
# remapped under virtual demo-front/back camera IDs for the private SPA parser.
assert len(manifest) == 20
assert {p['public_demo_source'] for p in manifest} == {
    f'{prefix}/demo-{camera}-{index:02d}.jpg'
    for prefix in ('demo-frames', 'demo-thumbs')
    for camera, count in (('front', 6), ('back', 4))
    for index in range(1, count + 1)
}
for entry in manifest:
    path = ROOT / entry['path']
    trusted = ROOT / entry['public_demo_source']
    assert path.is_file() and trusted.is_file()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == entry['sha256'] == hashlib.sha256(trusted.read_bytes()).hexdigest(), path
    assert path.read_bytes()[:3] == b'\xff\xd8\xff'

images = json.loads((APP / 'images.json').read_text())
analysis = json.loads((APP / 'analysis.json').read_text())
assert len(images) == len(set(images)) == 10
assert set(images) == set(analysis)
assert all(x.startswith(('demo-front_', 'demo-back_')) for x in images)
assert not any('_llm' in x or 'confidence' in x or 'score' in x for x in analysis.values())
assert all('Not an inference result' in x['description'] for x in analysis.values())
assert len(fixture['cameras']) == 2
assert fixture['settings']['deep_passes_enabled'] is False
assert fixture['settings']['allow_cloud'] is False
assert fixture['status']['llm']['reachable'] is False
assert all(c['source_dir'] == 'synthetic-only' for c in fixture['cameras'])
assert {k: len(v['images']) for k, v in fixture['catalogs'].items()} == {'DemoBack': 4, 'DemoFront': 6}
assert not (ROOT / 'frontend/.git').exists()

print('PASS frontend file allowlist, 20 image hash matches, 10 synthetic events, denied writes and no private patterns')
