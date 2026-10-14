"""Low-load, no-dependency camera-folder smoke & security checks."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen,Request
from http.server import ThreadingHTTPServer
from functools import partial
from threading import Thread
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from funes.server import SnapshotCatalog,Handler
from tools.seed_synthetic_watch import seed

class WatchTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='funes-e2e-')
        self.addCleanup(tmp.cleanup)
        self.incoming=Path(tmp.name)/'incoming'
        self.catalog=SnapshotCatalog(self.incoming)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,catalog=self.catalog))
        self.addCleanup(self.http.server_close)
        thread=Thread(target=self.http.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.http.shutdown)
        self.root='http://127.0.0.1:'+str(self.http.server_address[1])

    def get(self,path):
        return urlopen(self.root+path,timeout=3)

    def test_observes_new_frames_then_groups_visits(self):
        start=json.loads(self.get('/api/activity').read())
        self.assertEqual((start['count'],start['visit_count']),(0,0))
        self.assertEqual(seed(self.incoming),10)
        final=json.loads(self.get('/api/activity').read())
        self.assertEqual(final['count'],10)
        self.assertEqual(final['visit_count'],7)
        self.assertTrue(any(v['frame_count']==3 and v['objects']==['person'] for v in final['visits']))
        self.assertTrue(any(v['frame_count']==2 and v['objects']==['dog'] for v in final['visits']))
        self.assertEqual(final['inference']['object_detector'],'not run')
        self.assertTrue(all(f['synthetic_fixture'] and f['decision_model_status']=='not run' for f in final['frames']))
        self.assertIn('image/jpeg',self.get('/captures/DemoFront/demo-front-03.jpg').headers['Content-Type'])

    def test_same_port_serves_frontend_and_api(self):
        self.assertIn(b'SYNTHETIC DEMO',self.get('/frontend/index.html').read())
        self.assertIn(b'Every frame has a place',self.get('/activity/index.html').read())
        self.assertIn(b'Local activity, without invented analysis.',self.get('/frontend/activity-v1.html').read())
        self.assertIn(b'normalizeActivity',self.get('/frontend/static/activity-v1.js').read())
        self.assertIn(b'<svg',self.get('/docs/architecture-overview.svg').read())
        self.assertIn(b'fixture-only',self.get('/api/status').read())

    def test_writes_private_files_and_traversal_blocked(self):
        for p in ('/.git/config','/funes/server.py','/README.md','/runtime/incoming/foo.jpg',
                  '/captures/DemoFront/../demo-front-03.jpg','/api/unknown'):
            with self.subTest(path=p):
                with self.assertRaises(HTTPError) as err:self.get(p)
                self.assertEqual(err.exception.code,404)
        for method in ('POST','PUT','DELETE'):
            with self.subTest(method=method):
                with self.assertRaises(HTTPError) as err:urlopen(Request(self.root+'/api/activity',data=b'{}',method=method),timeout=3)
                self.assertEqual(err.exception.code,405)

    def test_unknown_jpeg_never_fabricates_detection(self):
        self.incoming.joinpath('DemoFront').mkdir(parents=True)
        new=self.incoming/'DemoFront'/'unknown.jpg'
        new.write_bytes((ROOT/'demo-frames'/'demo-front-03.jpg').read_bytes())
        frame=json.loads(self.get('/api/activity').read())['frames'][0]
        self.assertEqual(frame['objects'],[])
        self.assertEqual(frame['annotation_source'],'unclassified image')

if __name__=='__main__':unittest.main()
