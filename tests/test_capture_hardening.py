"""Read-only, synthetic-only hardening checks for capture eligibility and containment.

Everything here runs in a temporary directory with hand-written JPEG-shaped
bytes. No real footage, no camera, no model and no network beyond the loopback
test server started by this file.
"""
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from funes import server as server_module
from funes.server import (Handler, SnapshotCatalog, JPEG_NAME, MAX_IMAGE_BYTES,
                          is_link_like, is_within, read_bounded_bytes, read_jpeg_digest,
                          supported_jpeg_name)

JPEG = b'\xff\xd8\xff\xd9' + b'fictional-body' * 4 + b'\xff\xd9'


def link_supported():
    if not hasattr(os, 'symlink'):
        return False
    probe = Path(tempfile.mkdtemp(prefix='funes-link-probe-')) / 'x'
    try:
        os.symlink(probe.parent, probe)
        return True
    except (OSError, NotImplementedError):
        return False


def make_junction(link: Path, target: Path) -> bool:
    """Windows directory junction; returns False when it cannot be created."""
    if os.name != 'nt':
        return False
    try:
        done = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                              capture_output=True, text=True, timeout=30)
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class EligibilityTests(unittest.TestCase):
    """The scanner, the capture endpoint and the browser rule stay identical."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='funes-eligibility-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'incoming'
        (self.root / 'DemoFront').mkdir(parents=True)
        self.catalog = SnapshotCatalog(self.root)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, catalog=self.catalog))
        self.addCleanup(self.http.server_close)
        worker = Thread(target=self.http.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.http.shutdown)
        self.origin = 'http://127.0.0.1:' + str(self.http.server_address[1])

    def activity(self):
        with urlopen(self.origin + '/api/activity', timeout=5) as response:
            return json.load(response)

    def put(self, camera, name, raw=JPEG, mtime=None):
        path = self.root / camera / name
        path.write_bytes(raw)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_unsupported_filenames_are_counted_and_never_reach_the_api(self):
        for name in ('front door 01.jpg', 'café-01.jpg', '.hidden.jpg',
                     'a..b.jpg', 'x' * 120 + '.jpg', 'frame.png', 'notes.txt'):
            self.put('DemoFront', name, mtime=1600000000)
        self.put('DemoFront', 'good.jpg', mtime=1600000000)
        payload = self.activity()
        names = [frame['filename'] for frame in payload['frames']]
        self.assertEqual(names, ['good.jpg'])
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['scan']['skipped']['unsupported-filename'], 7)
        self.assertEqual(payload['scan']['skipped'].get('oversize-or-too-small', 0), 0)
        # One unsupported name never invalidates the frames that are valid.
        with urlopen(self.origin + '/captures/DemoFront/good.jpg', timeout=5) as image:
            self.assertEqual(image.read(), JPEG)
            self.assertEqual(image.headers['Content-Type'], 'image/jpeg')

    def test_every_advertised_capture_url_is_actually_servable(self):
        from tools.seed_synthetic_watch import seed
        self.assertEqual(seed(self.root), 10)
        payload = self.activity()
        self.assertEqual(payload['count'], 10)
        for frame in payload['frames']:
            with self.subTest(frame=frame['filename']):
                with urlopen(self.origin + frame['image_url'], timeout=5) as image:
                    self.assertEqual(image.headers['Content-Type'], 'image/jpeg')
                    self.assertEqual(image.read()[:3], b'\xff\xd8\xff')

    def test_partial_oversized_and_empty_files_are_controlled_skips(self):
        self.put('DemoFront', 'partial.jpg', b'\xff\xd8\xff')
        self.put('DemoFront', 'empty.jpg', b'')
        self.put('DemoFront', 'too-small.jpg', b'\xff\xd8')
        self.put('DemoFront', 'trailing.jpg', JPEG + b'appended-after-eoi')
        self.put('DemoFront', 'oversize.jpg', b'\xff\xd8\xff' + b'0' * (MAX_IMAGE_BYTES + 1) + b'\xff\xd9')
        payload = self.activity()
        self.assertEqual(payload['count'], 0)
        self.assertEqual(payload['visit_count'], 0)
        self.assertEqual(payload['inference'], {'object_detector': 'not run',
                                                 'vision_decision_model': 'not run'})
        skipped = payload['scan']['skipped']
        self.assertEqual(skipped.get('incomplete-or-unstable-jpeg'), 2)
        self.assertEqual(skipped.get('oversize-or-too-small'), 3)

    def test_empty_or_absent_camera_folders_report_no_skips(self):
        # setUp creates DemoFront but never DemoBack, so this covers both an
        # empty folder and a folder that does not exist at all.
        payload = self.activity()
        self.assertEqual(payload['count'], 0)
        self.assertEqual(payload['scan']['skipped'], {},
                         'a missing camera folder is not a skipped file and not a link')
        # A second empty snapshot must stay clean and must reuse the cache safely.
        self.assertEqual(self.activity()['scan']['skipped'], {})

    def test_subdirectories_and_special_files_are_not_indexed(self):
        (self.root / 'DemoFront' / 'album.jpg').mkdir()
        self.put('DemoFront', 'real.jpg', mtime=1600000000)
        payload = self.activity()
        self.assertEqual([f['filename'] for f in payload['frames']], ['real.jpg'])
        self.assertEqual(payload['scan']['skipped']['not-a-regular-file'], 1)

    def test_deleted_file_disappears_and_its_capture_becomes_a_404(self):
        target = self.put('DemoFront', 'temporary.jpg', mtime=1600000000)
        self.assertEqual(self.activity()['count'], 1)
        with urlopen(self.origin + '/captures/DemoFront/temporary.jpg', timeout=5) as image:
            self.assertEqual(image.status, 200)
        target.unlink()
        self.assertEqual(self.activity()['count'], 0)
        with self.assertRaises(HTTPError) as missing:
            urlopen(self.origin + '/captures/DemoFront/temporary.jpg', timeout=5)
        self.assertEqual(missing.exception.code, 404)
        # The rest of the API keeps working.
        with urlopen(self.origin + '/api/status', timeout=5) as status:
            self.assertEqual(json.load(status)['watched_frames'], 0)

    def test_unsupported_filename_rule_is_published_for_the_browser(self):
        payload = self.activity()
        self.assertEqual(payload['scan']['unsupported_filename_rule'], JPEG_NAME.pattern)
        browser = (ROOT / 'frontend/static/activity-v1.js').read_text(encoding='utf-8')
        published = browser.split('const IMAGE_NAME = /')[1].split('/')[0]
        self.assertEqual(JPEG_NAME.pattern, published,
                         'scanner and browser adapter must publish one identical rule')
        self.assertTrue(supported_jpeg_name('demo-front-01.JPG'))
        self.assertTrue(supported_jpeg_name('_MG_1234.jpeg'))
        for bad in ('front door.jpg', 'café.jpg', '.dot.jpg', 'a..b.jpg', '../x.jpg',
                    'x' * 120 + '.jpg', 'frame.png'):
            with self.subTest(name=bad):
                self.assertFalse(supported_jpeg_name(bad))


class BoundedReadTests(unittest.TestCase):
    """Reads happen on the opened handle and refuse growth, not just stat."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='funes-bounded-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_growth_between_stat_and_read_is_refused(self):
        target = self.dir / 'growing.jpg'
        target.write_bytes(JPEG)
        real = os.stat(target)
        calls = {'n': 0}

        def fake_fstat(_fd):
            calls['n'] += 1
            fields = dict(st_mode=real.st_mode, st_mtime_ns=real.st_mtime_ns,
                          st_ctime_ns=real.st_ctime_ns, st_size=real.st_size)
            if calls['n'] > 1:
                fields['st_size'] = real.st_size + 4096  # an uploader appended bytes
            return types.SimpleNamespace(**fields)

        original = server_module.os.fstat
        server_module.os.fstat = fake_fstat
        self.addCleanup(lambda: setattr(server_module.os, 'fstat', original))
        self.assertIsNone(read_jpeg_digest(target))
        self.assertGreaterEqual(calls['n'], 2)

    def test_truncation_between_stat_and_read_is_refused(self):
        target = self.dir / 'shrinking.jpg'
        target.write_bytes(JPEG)
        real = os.stat(target)
        calls = {'n': 0}

        def fake_fstat(_fd):
            calls['n'] += 1
            size = real.st_size if calls['n'] == 1 else real.st_size // 2
            return types.SimpleNamespace(st_mode=real.st_mode, st_size=size,
                                         st_mtime_ns=real.st_mtime_ns, st_ctime_ns=real.st_ctime_ns)

        original = server_module.os.fstat
        server_module.os.fstat = fake_fstat
        self.addCleanup(lambda: setattr(server_module.os, 'fstat', original))
        self.assertIsNone(read_jpeg_digest(target))
        self.assertIsNone(read_bounded_bytes(target, MAX_IMAGE_BYTES))

    def test_bounded_read_caps_memory_and_rejects_oversize(self):
        target = self.dir / 'big.bin'
        target.write_bytes(b'0' * 4096)
        self.assertEqual(len(read_bounded_bytes(target, 4096)), 4096)
        self.assertIsNone(read_bounded_bytes(target, 4095))
        self.assertIsNone(read_bounded_bytes(self.dir, 4096))

    def test_complete_markers_are_required(self):
        for name, raw in (('a.jpg', JPEG), ('b.jpg', b'\xff\xd8\xff'), ('c.jpg', JPEG[:-1]),
                          ('d.jpg', b'not-a-jpeg-at-all'), ('e.jpg', JPEG + b'tail')):
            with self.subTest(name=name):
                path = self.dir / name
                path.write_bytes(raw)
                expected = len(read_jpeg_digest(path) or '') == 64
                self.assertEqual(expected, name == 'a.jpg')


@unittest.skipUnless(link_supported(), 'symbolic links unavailable on this host')
class ContainmentTests(unittest.TestCase):
    """Selected directories, not the repository root, bound every served file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='funes-containment-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'incoming'
        self.outside = Path(self.tmp.name) / 'outside'
        (self.root / 'DemoFront').mkdir(parents=True)
        self.outside.mkdir()
        (self.outside / 'secret.jpg').write_bytes(JPEG)
        (self.root / 'DemoFront' / 'inside.jpg').write_bytes(JPEG)

    def test_symlinked_parent_directory_is_not_followed(self):
        base = self.root / 'DemoFront'
        linked = self.root / 'DemoBack'
        try:
            os.symlink(self.outside, linked, target_is_directory=True)
        except OSError as error:
            self.skipTest(f'symlink creation refused: {error}')
        self.assertTrue(is_link_like(linked))
        self.assertTrue(is_within(base, base / 'inside.jpg'))
        self.assertFalse(is_within(linked, linked / 'secret.jpg'))
        self.assertFalse(is_within(base, linked / 'secret.jpg'))
        self.assertFalse(is_within(base, self.outside / 'secret.jpg'))
        catalog = SnapshotCatalog(self.root)
        payload = catalog.snapshot()
        self.assertEqual([f['filename'] for f in payload['frames']], ['inside.jpg'])
        self.assertEqual(payload['scan']['skipped']['camera-directory-is-a-link'], 1)

    def test_symlinked_file_inside_the_camera_folder_is_rejected(self):
        (self.root / 'DemoFront' / 'linked.jpg').symlink_to(self.outside / 'secret.jpg')
        catalog = SnapshotCatalog(self.root)
        payload = catalog.snapshot()
        self.assertEqual([f['filename'] for f in payload['frames']], ['inside.jpg'])
        self.assertEqual(payload['scan']['skipped']['link-or-junction'], 1)

    def test_containment_boundary_is_the_selected_directory(self):
        base = self.root / 'DemoFront'
        self.assertTrue(is_within(base, base / 'inside.jpg'))
        self.assertFalse(is_within(base, self.outside / 'secret.jpg'))
        self.assertFalse(is_within(base, base))
        self.assertFalse(is_within(base, Path(self.tmp.name) / '..' / 'outside' / 'secret.jpg'))


@unittest.skipUnless(os.name == 'nt', 'Windows junction checks')
class JunctionTests(unittest.TestCase):
    """Windows junctions are reparse points, not symbolic links, and must be
    refused as well when a camera folder is one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='funes-junction-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'incoming'
        self.outside = Path(self.tmp.name) / 'outside'
        (self.root / 'DemoFront').mkdir(parents=True)
        self.outside.mkdir()
        (self.outside / 'secret.jpg').write_bytes(JPEG)
        (self.root / 'DemoFront' / 'inside.jpg').write_bytes(JPEG)

    def test_junction_camera_folder_is_not_followed(self):
        base = self.root / 'DemoFront'
        junction = self.root / 'DemoBack'
        if not make_junction(junction, self.outside):
            self.skipTest('junction creation refused on this host')
        self.assertTrue(is_link_like(junction))
        self.assertFalse(is_link_like(base))
        self.assertFalse(is_within(base, junction / 'secret.jpg'))
        catalog = SnapshotCatalog(self.root)
        payload = catalog.snapshot()
        self.assertEqual([f['filename'] for f in payload['frames']], ['inside.jpg'])
        self.assertEqual(payload['scan']['skipped']['camera-directory-is-a-link'], 1)

    def test_junction_camera_folder_is_not_served_over_http(self):
        base = self.root / 'DemoFront'
        junction = self.root / 'DemoBack'
        if not make_junction(junction, self.outside):
            self.skipTest('junction creation refused on this host')
        catalog = SnapshotCatalog(self.root)
        http = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, catalog=catalog))
        worker = Thread(target=http.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(http.shutdown)
        self.addCleanup(http.server_close)
        origin = 'http://127.0.0.1:' + str(http.server_address[1])
        with urlopen(origin + '/captures/DemoFront/inside.jpg', timeout=5) as image:
            self.assertEqual(image.read(), JPEG)
        with self.assertRaises(HTTPError) as error:
            urlopen(origin + '/captures/DemoBack/secret.jpg', timeout=5)
        self.assertEqual(error.exception.code, 404)


class HttpContainmentTests(unittest.TestCase):
    """Loopback read-only surface: no traversal, no private files, no writes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='funes-http-containment-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'incoming'
        (self.root / 'DemoFront').mkdir(parents=True)
        (self.root / 'DemoFront' / 'inside.jpg').write_bytes(JPEG)
        self.catalog = SnapshotCatalog(self.root)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, catalog=self.catalog))
        self.addCleanup(self.http.server_close)
        worker = Thread(target=self.http.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.http.shutdown)
        self.origin = 'http://127.0.0.1:' + str(self.http.server_address[1])

    def test_capture_paths_cannot_traverse_or_leave_the_camera_folder(self):
        for path in ('/captures/DemoFront/../../funes/server.py',
                     '/captures/DemoFront/%2e%2e%2fserver.py',
                     '/captures/DemoFront/inside.jpg%00.png',
                     '/captures/Other/inside.jpg',
                     '/captures/DemoFront/inside.jpg/extra.jpg',
                     '/captures/DemoFront/front%20door.jpg',
                     '/captures/DemoFront/a..b.jpg',
                     '/captures/DemoFront//inside.jpg'):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as error:
                    urlopen(self.origin + path, timeout=5)
                self.assertEqual(error.exception.code, 404)

    def test_private_repository_files_stay_unreachable(self):
        for path in ('/.git/config', '/funes/server.py', '/README.md', '/package.json',
                     '/server.mjs', '/tools/seed_synthetic_watch.py', '/api/activity/../status',
                     '/frontend\\index.html', '/frontend/static\\activity-v1.js',
                     '/frontend//index.html', '/frontend/%2e%2e/README.md'):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as error:
                    urlopen(self.origin + path, timeout=5)
                self.assertEqual(error.exception.code, 404)

    def test_write_methods_are_refused_on_the_api_and_static_surface(self):
        from urllib.request import Request
        for path in ('/api/activity', '/api/status', '/captures/DemoFront/inside.jpg',
                     '/frontend/index.html'):
            for method in ('POST', 'PUT', 'DELETE'):
                with self.subTest(path=path, method=method):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(self.origin + path, data=b'{}', method=method), timeout=5)
                    self.assertEqual(error.exception.code, 405)


if __name__ == '__main__':
    unittest.main()