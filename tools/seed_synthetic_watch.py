#!/usr/bin/env python3
"""Copy ONLY public hash-verified demo JPEGs into a configured test folder."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def seed(out: Path):
    manifest=json.loads((ROOT/'manifest.json').read_text())
    allowed={x['file']:x['sha256'] for x in manifest['assets']}
    scenarios=json.loads((ROOT/'tools/synthetic_scenarios.json').read_text())
    count=0
    for s in scenarios:
        filename=s['source']
        camera='DemoFront' if filename.startswith('demo-front-') else 'DemoBack'
        entry='demo-frames/'+filename
        raw=(ROOT/entry).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=allowed[entry]:
            raise ValueError('Fixture integrity check failed; no data copied')
        target=out/camera/filename
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=raw:
            raise ValueError('Refusing to overwrite different existing frame')
        target.write_bytes(raw)
        count+=1
    print(f'Seeded {count} verified synthetic JPEGs into local watched folder (path withheld)')
    return count

if __name__=='__main__':
    p=ArgumentParser()
    p.add_argument('--dest',type=Path,default=ROOT/'runtime'/'incoming')
    seed(p.parse_args().dest)
