"""Run exactly the documented Python launch and seed commands, on a fresh folder."""
from pathlib import Path
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

# Text output must not depend on the host locale encoding.
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT/'README.md').read_text(encoding='utf-8')
LAUNCH = 'python3 -m funes.server'
SEED = 'python3 tools/seed_synthetic_watch.py'
assert re.search(r'(?m)^'+re.escape(LAUNCH)+r'$', README), 'README missing canonical Python run command'
assert re.search(r'(?m)^'+re.escape(SEED)+r'$', README), 'README missing seed command'


def free_port():
    """Ask the OS for an unused loopback port instead of hardcoding one."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]

# A fresh, empty temporary watched folder proves the server discovers new JPEGs
# after launch; no cached JSON or running model is reused.
with tempfile.TemporaryDirectory(prefix='funes-readme-acceptance-') as directory:
    target = Path(directory)/'incoming'
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
    port = free_port()
    process = subprocess.Popen([sys.executable, '-m', 'funes.server', '--watch-dir', str(target),
                                '--port', str(port)],cwd=ROOT,env=env,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    origin='http://127.0.0.1:'+str(port)
    try:
        started=False
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if process.poll() is not None:
                raise AssertionError('README server exited before accepting connections')
            try:
                initial=json.load(urlopen(origin+'/api/activity',timeout=1))
                started=True
                break
            except OSError:
                time.sleep(.05)
        assert started and initial['count']==0, 'README server did not start empty'
        seed=subprocess.run([sys.executable,'tools/seed_synthetic_watch.py','--dest',str(target)],
                            cwd=ROOT,capture_output=True,encoding='utf-8',timeout=30)
        assert seed.returncode==0, 'README synthetic seed command failed'
        result=json.load(urlopen(origin+'/api/activity',timeout=2))
        assert result['count']==10 and result['visit_count']==7, result
        assert any(v['frame_count']==3 for v in result['visits'])
        assert result['inference']=={'object_detector':'not run','vision_decision_model':'not run'}
        assert result['scan']['skipped']=={}, result['scan']
        assert urlopen(origin+'/activity/index.html',timeout=2).status==200
        assert urlopen(origin+'/frontend/activity-v1.html',timeout=2).status==200
        assert urlopen(origin+'/frontend/static/activity-v1.js',timeout=2).status==200
        assert urlopen(origin+'/frontend/index.html',timeout=2).status==200
        print('PASS fresh watched folder: README Python start → 10 JPEGs → 7 visits on one port, no inference')
    finally:
        process.terminate()
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:process.kill()
