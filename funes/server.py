"""Single-origin Python frontend + synthetic watched-folder proof.

No camera access, detector inference, model launch, outbound requests or writes.
Scans an explicitly configured local directory when the UI requests activity;
requires only the Python standard library. Not the production ingestion API.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import partial
from collections import deque, Counter
from itertools import islice
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
import threading
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CAMERAS = ('DemoFront', 'DemoBack')
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_STATIC_BYTES = 16 * 1024 * 1024
MAX_FRAMES = 500
MAX_CANDIDATES_PER_CAMERA = 2000
READ_CHUNK_BYTES = 256 * 1024
# One eligibility rule shared by the scanner, the capture endpoint and the
# activity-v1 contract. ASCII-only and percent-encoding-free so the advertised
# capture URL is always exactly servable; ordinary spaces or non-ASCII names are
# reported as skipped instead of reaching the API or breaking a whole response.
JPEG_NAME = re.compile(r'^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,93}\.jpe?g$', flags=re.IGNORECASE)
# On POSIX st_ctime is the metadata-change time, so an unchanged
# (device, inode, size, mtime, ctime) tuple really does mean unchanged bytes and
# the digest cache can be trusted. Windows reports creation time as st_ctime and
# never moves it, so there a cache hit would keep attributing stale synthetic
# fixture annotations to a rewritten same-name, same-size file.
TRUSTWORTHY_CHANGE_TIME = os.name == 'posix'
REPARSE_POINT = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
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


def is_link_like(path: Path) -> bool:
    """True for symbolic links and Windows reparse points such as junctions.

    A path that simply does not exist is not a link; a path that cannot be
    examined at all is treated as one, because containment must fail closed.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, 'st_file_attributes', 0) & REPARSE_POINT)


def is_within(base: Path, file: Path) -> bool:
    """True only when `file` stays inside `base` through no link of any kind.

    The directory selected by the request is the containment boundary, not the
    repository root: the boundary itself and every component below it are checked
    for symlinks, junctions and other reparse points, and the resolved target must
    remain under the resolved boundary.
    """
    if not file.is_relative_to(base) or file == base or is_link_like(base):
        return False
    current = file
    while current != base:
        if is_link_like(current):
            return False
        current = current.parent
    try:
        return file.resolve().is_relative_to(base.resolve())
    except OSError:
        return False


def supported_jpeg_name(name: str) -> bool:
    """The single eligibility rule shared with the browser adapter.

    ASCII and percent-encoding free, at most 99 characters, and without a `..`
    component so an admitted filename is always exactly what the advertised
    capture URL serves.
    """
    return bool(JPEG_NAME.fullmatch(name)) and '..' not in name


def read_bounded_bytes(file: Path, limit: int) -> bytes | None:
    """Read a regular file through one handle, refusing oversize, growth and races.

    Deletion, permission loss and mid-read rewrites surface as `None` instead of
    propagating an unexpected filesystem error to the client.
    """
    try:
        with file.open('rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
                return None
            data = handle.read(limit + 1)
            if len(data) != before.st_size:
                return None
            after = os.fstat(handle.fileno())
            if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
                return None
            return data
    except OSError:
        return None


def read_jpeg_digest(file: Path) -> str | None:
    """SHA-256 of a complete, stable JPEG, or None for partial/growing/oversize.

    The bounded read happens on the already-opened handle, so the bytes hashed
    are exactly the bytes checked for the SOI/EOI markers; a file still being
    written by an uploading peer is skipped rather than half-indexed.
    """
    try:
        with file.open('rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or not 3 <= before.st_size <= MAX_IMAGE_BYTES:
                return None
            digest = hashlib.sha256()
            head = handle.read(3)
            if head != b'\xff\xd8\xff':
                return None
            digest.update(head)
            tail = b''
            remaining = before.st_size - 3
            while remaining > 0:
                chunk = handle.read(min(READ_CHUNK_BYTES, remaining))
                if not chunk:
                    return None
                digest.update(chunk)
                tail = (tail + chunk)[-2:]
                remaining -= len(chunk)
            after = os.fstat(handle.fileno())
            if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
                return None
            if handle.read(1) or tail != b'\xff\xd9':
                return None
            return digest.hexdigest()
    except OSError:
        return None


class SnapshotCatalog:
    def __init__(self, incoming: Path):
        self.incoming = incoming.resolve()
        self._fingerprints: dict[tuple[str, int, int, int, int, int], str] = {}
        self._lock = threading.Lock()
        self._manifest = {}
        for item in FIXTURE:
            image = ROOT / 'demo-frames' / item['source']
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            self._manifest[(item['source'], digest)] = item

    def _scan(self):
        """Return a bounded read-only snapshot of candidate JPEGs.

        A limited filesystem enumeration is sorted per camera, then accepted
        round-robin. A capped subset is not necessarily the same after a large
        directory is reordered; this is not a complete archival index. Every
        directory entry is classified, so an unsupported filename, an incomplete
        upload or an unreadable file is a counted skip rather than a failure of
        the whole snapshot.
        """
        frames = []
        candidate_lists = []
        skipped = Counter()
        for camera in CAMERAS:
            folder = self.incoming / camera
            if is_link_like(folder):
                skipped['camera-directory-is-a-link'] += 1
                continue
            if not folder.is_dir():
                continue
            try:
                files = sorted(islice(folder.iterdir(), MAX_CANDIDATES_PER_CAMERA))
            except OSError:
                skipped['camera-directory-unreadable'] += 1
                continue
            candidate_lists.append((camera, files))
        potentially_truncated = any(
            len(files) == MAX_CANDIDATES_PER_CAMERA for _, files in candidate_lists
        )
        pending = deque((camera, iter(files)) for camera, files in candidate_lists)
        active_fingerprints = {}
        while pending and len(frames) < MAX_FRAMES:
            camera, files = pending.popleft()
            try:
                file = next(files)
            except StopIteration:
                continue
            pending.append((camera, files))
            if is_link_like(file):
                skipped['link-or-junction'] += 1
                continue
            if not supported_jpeg_name(file.name):
                # Same rule the capture endpoint and the browser adapter apply,
                # so an admitted frame is always servable and always renderable.
                skipped['unsupported-filename'] += 1
                continue
            try:
                st = file.stat()
            except OSError:
                skipped['unreadable-or-deleted'] += 1
                continue
            if not stat.S_ISREG(st.st_mode):
                skipped['not-a-regular-file'] += 1
                continue
            if not 3 <= st.st_size <= MAX_IMAGE_BYTES:
                skipped['oversize-or-too-small'] += 1
                continue
            # Device/inode/ctime distinguish a rewritten same-name, same-size
            # and restored-mtime file from the previously hashed content.
            key = (str(file), st.st_size, st.st_mtime_ns,
                   st.st_ctime_ns, st.st_dev, st.st_ino)
            digest = self._fingerprints.get(key) if TRUSTWORTHY_CHANGE_TIME else None
            if digest is None:
                # A file can disappear or change while its uploading peer writes.
                # Skip this snapshot; do not fabricate detector evidence.
                digest = read_jpeg_digest(file)
                if digest is None:
                    skipped['incomplete-or-unstable-jpeg'] += 1
                    continue
            active_fingerprints[key] = digest
            fixture = self._manifest.get((file.name, digest))
            captured = (fixture['timestamp'] if fixture else
                        datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat())
            frames.append({
                'id': hashlib.sha256((camera+'/'+file.name).encode()).hexdigest()[:16],
                'camera': camera,
                'filename': file.name,
                'image_url': f'/captures/{camera}/{file.name}',
                'captured_at': captured,
                'capture_time_source': 'synthetic-fixture' if fixture else 'filesystem-mtime',
                'objects': fixture['objects'] if fixture else [],
                'description': fixture['description'] if fixture else 'New image awaiting object detection',
                'annotation_source': 'synthetic fixture annotation' if fixture else 'unclassified image',
                'detector_status': 'not run',
                'decision_model_status': 'not run',
                'synthetic_fixture': bool(fixture),
            })
        # Retain fingerprints only for files in the most recent bounded snapshot.
        self._fingerprints = active_fingerprints
        potentially_truncated = potentially_truncated or len(frames) >= MAX_FRAMES
        frames.sort(key=lambda e: (e['camera'], e['captured_at'], e['filename']))
        visits = []
        for frame in frames:
            when = datetime.fromisoformat(frame['captured_at'].replace('Z', '+00:00'))
            last = visits[-1] if visits else None
            if last and last['camera'] == frame['camera']:
                gap = (when - datetime.fromisoformat(last['end'].replace('Z', '+00:00'))).total_seconds()
            else:
                gap = None
            if gap is not None and 0 <= gap <= 120:
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
        return {'schema_version': 1, 'data_source': 'watched-folder-preview',
                'read_only': True,
                'scan': {'max_frames': MAX_FRAMES,
                         'max_candidates_per_camera': MAX_CANDIDATES_PER_CAMERA,
                         'possibly_truncated': potentially_truncated,
                         'unsupported_filename_rule': JPEG_NAME.pattern,
                         'skipped': dict(sorted(skipped.items()))},
                'frames': sorted(frames, key=lambda e: e['captured_at'], reverse=True),
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
            if len(parts) != 4 or parts[2] not in CAMERAS or not supported_jpeg_name(parts[3]):
                self.send_error(404)
                return
            # The selected camera directory is the containment boundary.
            base = self.catalog.incoming / parts[2]
            file = base / parts[3]
            limit = MAX_IMAGE_BYTES
        elif path in STATIC_EXACT:
            base = ROOT
            file = ROOT / path.lstrip('/')
            limit = MAX_STATIC_BYTES
        elif any(path.startswith(p) for p in STATIC):
            # Static assets are contained in the directory the URL selected, so
            # a link or junction anywhere below it cannot escape that directory.
            base = ROOT / path.split('/')[1]
            file = ROOT / path.lstrip('/')
            limit = MAX_STATIC_BYTES
        else:
            self.send_error(404)
            return
        if file.suffix.lower() not in ALLOWED or not is_within(base, file):
            self.send_error(404)
            return
        content = read_bounded_bytes(file, limit)
        if content is None:
            self.send_error(404)
            return
        self._send(200, ALLOWED[file.suffix.lower()], content, head_only)


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
