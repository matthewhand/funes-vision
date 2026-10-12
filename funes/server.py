"""Single-origin Python frontend + synthetic watched-folder proof.

No camera access, detector inference, model launch, outbound requests or writes.
Scans an explicitly configured local directory when the UI requests activity;
requires only the Python standard library. Not the production ingestion API.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import partial
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CAMERAS = ('DemoFront', 'DemoBack')
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_FRAMES = 500
ALLOWED = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
           '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
           '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
           '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.ico': 'image/x-icon'}
STATIC = {'/frontend/', '/activity/', '/demo/', '/demo-frames/', '/demo-thumbs/', '/docs/evidence/'}
STATIC_EXACT = {'/manifest.json', '/docs/architecture-overview.svg'}
# Explicit fixture-only metadata, never assertions of actual model execution.
FIXTURE = json.loads((ROOT / 'tools/synthetic_scenarios.json').read_text())


def safe_relative(path: str) -> str | None:
    try:
        raw = unquote(urlsplit(path).path)
    except ValueError:
        return None
    if '\\' in raw or '\x00' in raw or '//' in raw or not raw.startswith('/'):
        return None
    parts = raw.split('/')[1:]
    if any(part.startswith('.') or part in ('.', '..') for part in parts if part):
        return None
    return raw


class SnapshotCatalog:
    def __init__(self, incoming: Path):
        self.incoming = incoming.resolve()
        self._fingerprints: dict[tuple[str, int, int], str] = {}
        self._lock = threading.Lock()
        self._manifest = {}
        for item in FIXTURE:
            image = ROOT / 'demo-frames' / item['source']
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            self._manifest[(item['source'], digest)] = item

    def _scan(self):
        frames = []
        for camera in CAMERAS:
            folder = self.incoming / camera
            if not folder.is_dir() or folder.is_symlink():
                continue
            for file in sorted(folder.iterdir()):
                if len(frames) >= MAX_FRAMES:
                    break
                if file.is_symlink() or file.suffix.lower() not in ('.jpg', '.jpeg') or not file.is_file():
                    continue
                st = file.stat()
                if not 3 <= st.st_size <= MAX_IMAGE_BYTES:
                    continue
                key = (str(file), st.st_size, st.st_mtime_ns)
                digest = self._fingerprints.get(key)
                if digest is None:
                    with file.open('rb') as f:
                        header = f.read(3)
                        if header != b'\xff\xd8\xff':
                            continue
                        rest = f.read()
                        if not rest.endswith(b'\xff\xd9'):
                            continue  # Partially uploaded JPEG: wait for a complete frame.
                        digest = hashlib.sha256(header + rest).hexdigest()
                    self._fingerprints[key] = digest
                fixture = self._manifest.get((file.name, digest))
                captured = (fixture['timestamp'] if fixture else
                            datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat())
                frames.append({
                    'id': hashlib.sha256((camera+'/'+file.name).encode()).hexdigest()[:16],
                    'camera': camera,
                    'filename': file.name,
                    'image_url': f'/captures/{camera}/{file.name}',
                    'captured_at': captured,
                    'objects': fixture['objects'] if fixture else [],
                    'description': fixture['description'] if fixture else 'New image awaiting object detection',
                    'annotation_source': 'synthetic fixture annotation' if fixture else 'unclassified image',
                    'detector_status': 'not run',
                    'decision_model_status': 'not run',
                    'synthetic_fixture': bool(fixture),
                })
        frames.sort(key=lambda e: (e['camera'], e['captured_at']))
        visits = []
        for frame in frames:
            when = datetime.fromisoformat(frame['captured_at'].replace('Z', '+00:00'))
            last = visits[-1] if visits else None
            if (last and last['camera'] == frame['camera'] and
                    (when - datetime.fromisoformat(last['end'])).total_seconds() <= 120):
                last['frames'].append(frame['id'])
                last['end'] = frame['captured_at']
                last['objects'] = sorted(set(last['objects']) | set(frame['objects']))
            else:
                visits.append({'camera': frame['camera'], 'start': frame['captured_at'],
                               'end': frame['captured_at'], 'frames': [frame['id']],
                               'objects': list(frame['objects'])})
        for i, visit in enumerate(visits):
            visit['id'] = 'visit-'+str(i+1)
            visit['frame_count'] = len(visit['frames'])
        return {'frames': sorted(frames, key=lambda e: e['captured_at'], reverse=True),
                'visits': sorted(visits, key=lambda e: e['end'], reverse=True),
                'count': len(frames), 'visit_count': len(visits),
                'mode': 'watched-folder-demo',
                'inference': {'object_detector': 'not run', 'vision_decision_model': 'not run'},
                'capture_assumption': 'Camera performs motion detection and saves JPEG files'}

    def snapshot(self):
        with self._lock:
            return self._scan()


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, catalog: SnapshotCatalog, **kwargs):
        self.catalog = catalog
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        # No file paths, camera names or query strings in server logs.
        pass

    def do_POST(self):
        self.send_error(405, 'Read-only preview')

    def do_PUT(self):
        self.send_error(405, 'Read-only preview')

    def do_DELETE(self):
        self.send_error(405, 'Read-only preview')

    def do_HEAD(self):
        self._dispatch(head_only=True)

    def do_GET(self):
        self._dispatch(head_only=False)

    def _json(self, result, head_only):
        raw = json.dumps(result).encode()
        self._send(200, 'application/json; charset=utf-8', raw, head_only)

    def _send(self, status, content_type, content, head_only):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _dispatch(self, head_only):
        path = safe_relative(self.path)
        if path is None:
            self.send_error(404)
            return
        if path == '/':
            self.send_response(302)
            self.send_header('Location', '/activity/index.html')
            self.end_headers()
            return
        if path == '/api/activity':
            self._json(self.catalog.snapshot(), head_only)
            return
        if path == '/api/status':
            data = self.catalog.snapshot()
            self._json({'status': 'fixture-only', 'watched_frames': data['count'],
                        'visits': data['visit_count'], 'inference': data['inference']}, head_only)
            return
        if path.startswith('/api/'):
            self.send_error(404)
            return
        if path.startswith('/captures/'):
            parts = path.split('/')
            if len(parts) != 4 or parts[2] not in CAMERAS or not re.fullmatch(r'[A-Za-z0-9_.-]+\.jpe?g', parts[3], flags=re.I):
                self.send_error(404)
                return
            file = self.catalog.incoming / parts[2] / parts[3]
        elif path in STATIC_EXACT or any(path.startswith(p) for p in STATIC):
            file = ROOT / path.lstrip('/')
        else:
            self.send_error(404)
            return
        if file.is_symlink() or not file.is_file():
            self.send_error(404)
            return
        # Ensure all paths remain in the expected root after resolving.
        base = self.catalog.incoming if path.startswith('/captures/') else ROOT
        if not file.resolve().is_relative_to(base):
            self.send_error(404)
            return
        extension = file.suffix.lower()
        if extension not in ALLOWED:
            self.send_error(404)
            return
        if path.startswith('/captures/') and file.stat().st_size > MAX_IMAGE_BYTES:
            self.send_error(404)
            return
        self._send(200, ALLOWED[extension], file.read_bytes(), head_only)


def main():
    cli = argparse.ArgumentParser(description='Single-port Funes Vision synthetic watched-folder preview')
    cli.add_argument('--watch-dir', type=Path, default=ROOT/'runtime'/'incoming')
    cli.add_argument('--host', default='127.0.0.1')
    cli.add_argument('--port', type=int, default=8000)
    args = cli.parse_args()
    if args.host != '127.0.0.1' and not (args.host == '0.0.0.0' and os.getenv('FUNES_PREVIEW_CONTAINER') == '1'):
        cli.error('Only loopback is supported outside the isolated container')
    if not 1 <= args.port <= 65535:
        cli.error('Unsupported port')
    catalog = SnapshotCatalog(args.watch_dir)
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, catalog=catalog))
    server.daemon_threads = True
    print(f'Funes Vision single-origin preview at http://127.0.0.1:{args.port}/', flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
