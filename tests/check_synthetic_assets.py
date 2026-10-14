#!/usr/bin/env python3
"""Offline and dependency-free allowlist check for public synthetic sample assets."""
import hashlib
import json
from pathlib import Path
import sys

# Text guards must not depend on the host locale encoding.
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
assert manifest.get('status') == 'review_candidate_synthetic_ai_generated'
assets = manifest['assets']
expected = {f'{folder}/demo-{camera}-{i:02d}.jpg'
            for folder in ('demo-frames', 'demo-thumbs')
            for camera, limit in (('front', 6), ('back', 4))
            for i in range(1, limit + 1)}
assert len(assets) == 20
assert {entry['file'] for entry in assets} == expected
for asset in assets:
    path = ROOT / asset['file']
    raw = path.read_bytes()
    assert raw.startswith(b'\xff\xd8\xff'), asset['file']
    assert b'Exif\x00\x00' not in raw, asset['file']
    assert len(raw) == asset['bytes'], asset['file']
    assert hashlib.sha256(raw).hexdigest() == asset['sha256'], asset['file']
    assert 0 < len(raw) < 1_000_000, asset['file']
    assert not any(s in asset['file'] for s in ('10.0.0.', 'Webcam', 'MOTDEC', '..', '\\'))
actual = {p.relative_to(ROOT).as_posix() for folder in ('demo-frames', 'demo-thumbs')
          for p in (ROOT / folder).glob('*') if p.is_file()}
assert actual == expected, f'unlisted files: {actual - expected}'
print(f'PASS: {len(assets)} synthetic JPEG assets match audited manifest')
