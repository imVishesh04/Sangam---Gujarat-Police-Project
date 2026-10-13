import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db, bcrypt
from app.models import User, Camera

class StreamProxyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('config.TestingConfig')
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()
            if not User.query.filter_by(username='proxy_admin').first():
                admin = User(
                    username='proxy_admin',
                    password_hash=bcrypt.generate_password_hash('admin123').decode('utf-8'),
                    role='admin'
                )
                db.session.add(admin)
            
            cam = Camera.query.get('CAM-TEST-PROXY')
            if not cam:
                cam = Camera(
                    camera_id='CAM-TEST-PROXY',
                    department='Traffic Police',
                    location_name='Proxy Test Gate',
                    vendor='Sentinel',
                    stream_url='https://live.corp8.cloud/live/stream/6/index.m3u8',
                    format='hls',
                    status='offline'
                )
                db.session.add(cam)
            # Self-contained: this camera is used by the "camera 6" test below
            if not Camera.query.get('CAM-AHM-001'):
                db.session.add(Camera(
                    camera_id='CAM-AHM-001',
                    department='Traffic Police',
                    location_name='Timbavadi Gate',
                    vendor='Sentinel',
                    stream_url='https://live.corp8.cloud/live/stream/6/index.m3u8',
                    format='hls',
                    status='offline'
                ))
            db.session.commit()

    def setUp(self):
        self.client.post('/auth/login', json={'username': 'proxy_admin', 'password': 'admin123'})

    def test_unauthenticated_stream_proxy_returns_401(self):
        unauth_client = self.app.test_client()
        r = unauth_client.get('/cameras/CAM-TEST-PROXY/stream-proxy')
        self.assertEqual(r.status_code, 401)

    def test_nonexistent_camera_returns_404(self):
        r = self.client.get('/cameras/DOES-NOT-EXIST/stream-proxy')
        self.assertEqual(r.status_code, 404)

    def test_unsupported_external_host_returns_400(self):
        r = self.client.get('/cameras/CAM-TEST-PROXY/stream-proxy?path=https://malicious.evilcorp.com/stream.m3u8')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Unsupported stream source host', r.get_json().get('error', ''))

    @patch('app.routes.cameras._fetch_hls_upstream')
    def test_m3u8_playlist_rewriting_and_content_type(self, mock_fetch):
        mock_fetch.return_value = (
            200,
            'https://live.corp8.cloud/live/stream/6/index.m3u8',
            'application/vnd.apple.mpegurl',
            b"""#EXTM3U
#EXT-X-VERSION:3
#EXTINF:6.000,
segment0.ts
#EXTINF:6.000,
segment1.ts
"""
        )
        r = self.client.get('/cameras/CAM-TEST-PROXY/stream-proxy')
        self.assertEqual(r.status_code, 200)
        self.assertIn('application/vnd.apple.mpegurl', r.headers.get('Content-Type', ''))
        data = r.data.decode('utf-8')
        self.assertIn('/cameras/CAM-TEST-PROXY/stream-proxy?path=', data)
        self.assertIn('segment0.ts', data)

    @patch('app.routes.cameras._fetch_hls_upstream')
    def test_ts_segment_proxy_content_type(self, mock_fetch):
        mock_fetch.return_value = (
            200,
            'https://live.corp8.cloud/live/stream/6/segment0.ts',
            'video/mp2t',
            b'MPEG-TS-MOCK-PAYLOAD'
        )
        r = self.client.get('/cameras/CAM-TEST-PROXY/stream-proxy?path=https://live.corp8.cloud/live/stream/6/segment0.ts')
        self.assertEqual(r.status_code, 200)
        self.assertIn('video/mp2t', r.headers.get('Content-Type', ''))
        self.assertEqual(r.data, b'MPEG-TS-MOCK-PAYLOAD')

    @patch('app.routes.cameras._fetch_hls_upstream')
    def test_upstream_timeout_returns_504(self, mock_fetch):
        mock_fetch.return_value = (
            'timeout',
            'Upstream gateway timed out: Read timed out.',
            504,
            b''
        )
        r = self.client.get('/cameras/CAM-TEST-PROXY/stream-proxy')
        self.assertEqual(r.status_code, 504)
        json_data = r.get_json()
        self.assertIn('Gateway timeout', json_data.get('error', ''))
        self.assertEqual(json_data.get('camera_id'), 'CAM-TEST-PROXY')

    @patch('app.routes.cameras._fetch_hls_upstream')
    def test_upstream_502_returns_502_with_json(self, mock_fetch):
        mock_fetch.return_value = (
            'error',
            'Upstream returned HTTP 502',
            502,
            b''
        )
        r = self.client.get('/cameras/CAM-TEST-PROXY/stream-proxy')
        self.assertEqual(r.status_code, 502)
        json_data = r.get_json()
        self.assertIn('Live stream unavailable', json_data.get('error', ''))

    @patch('app.routes.cameras._fetch_hls_upstream')
    def test_upstream_html_error_returns_502(self, mock_fetch):
        mock_fetch.return_value = (
            'error',
            'Upstream returned non-HLS HTML/error page',
            502,
            b''
        )
        r = self.client.get('/cameras/CAM-TEST-PROXY/stream-proxy')
        self.assertEqual(r.status_code, 502)
        self.assertIn('non-HLS HTML', r.get_json().get('detail', ''))

    def test_live_camera_6_upstream_failure_handled_cleanly(self):
        r = self.client.get('/cameras/CAM-AHM-001/stream-proxy')
        self.assertIn(r.status_code, (200, 502, 504))
        if r.status_code != 200:
            self.assertEqual(r.headers.get('Content-Type'), 'application/json')
            self.assertIn('error', r.get_json())

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            Camera.query.filter_by(camera_id='CAM-TEST-PROXY').delete()
            User.query.filter_by(username='proxy_admin').delete()
            db.session.commit()

if __name__ == '__main__':
    unittest.main()
