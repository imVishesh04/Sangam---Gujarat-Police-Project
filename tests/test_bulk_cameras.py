"""Bulk start/stop of camera ingestion (POST /cameras/bulk)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db  # noqa: E402
from app.models import Camera  # noqa: E402
from app.services import video_ingestion as vi  # noqa: E402

IDS = [f"TEST-BULK-{i}" for i in range(4)]


class TestBulkCameras(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("config.TestingConfig")
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        db.create_all()
        for i, cid in enumerate(IDS):
            if not db.session.get(Camera, cid):
                db.session.add(Camera(camera_id=cid, department="Test", location_name=f"Bulk {i}",
                                      latitude=23.0, longitude=72.5, vendor="t", format="HLS",
                                      stream_url="" if i == 3 else f"http://127.0.0.1:9/{cid}.m3u8",
                                      status="offline"))
        db.session.commit()
        cls.client = cls.app.test_client()
        r = cls.client.post("/auth/login", json={"username": "admin", "password": "admin123"})
        cls.logged_in = r.status_code == 200

    @classmethod
    def tearDownClass(cls):
        Camera.query.filter(Camera.camera_id.in_(IDS)).delete(synchronize_session=False)
        db.session.commit()
        db.session.remove()
        cls.ctx.pop()

    def setUp(self):
        if not self.logged_in:
            self.skipTest("admin/admin123 not seeded")

    def test_start_selected_skips_cameras_without_url(self):
        with mock.patch.object(vi, "start_cameras_staggered") as st, \
             mock.patch.object(vi, "running_camera_ids", return_value={IDS[1]}):
            r = self.client.post("/cameras/bulk", json={"action": "start", "camera_ids": IDS})
        self.assertEqual(r.status_code, 202)
        body = r.get_json()
        self.assertEqual(sorted(body["queued"]), [IDS[0], IDS[2]])
        reasons = {s["camera_id"]: s["reason"] for s in body["skipped"]}
        self.assertEqual(reasons, {IDS[1]: "already running", IDS[3]: "no stream URL"})
        self.assertEqual([c for c, _ in st.call_args[0][0]], body["queued"])

    def test_start_does_not_mark_online_before_frames(self):
        with mock.patch.object(vi, "start_cameras_staggered"):
            self.client.post("/cameras/bulk", json={"action": "start", "camera_ids": IDS[:1]})
        self.assertEqual(db.session.get(Camera, IDS[0]).status, "offline")

    def test_stop_marks_offline(self):
        db.session.get(Camera, IDS[0]).status = "online"
        db.session.commit()
        with mock.patch.object(vi, "stop_cameras", return_value=[]):
            r = self.client.post("/cameras/bulk", json={"action": "stop", "camera_ids": IDS[:2]})
        self.assertEqual(r.status_code, 200)
        db.session.expire_all()
        self.assertEqual(db.session.get(Camera, IDS[0]).status, "offline")

    def test_bad_action(self):
        self.assertEqual(self.client.post("/cameras/bulk", json={"action": "boom"}).status_code, 400)


class TestStaggered(unittest.TestCase):
    def test_starts_each_camera_in_order(self):
        started = []
        with mock.patch.object(vi, "start_camera", side_effect=lambda cid, url, app: started.append(cid)):
            vi.start_cameras_staggered([("A", "u"), ("B", "u"), ("C", "u")], app=None, gap_s=0).join(2)
        self.assertEqual(started, ["A", "B", "C"])

    def test_stop_cancels_queued_starts(self):
        import threading
        started, gate = [], threading.Event()

        def fake_start(cid, url, app):
            started.append(cid)
            if cid == "A":
                gate.wait(2)  # hold the queue after the first camera
        with mock.patch.object(vi, "start_camera", side_effect=fake_start):
            t = vi.start_cameras_staggered([("A", "u"), ("B", "u"), ("C", "u")], app=None, gap_s=0)
            vi.stop_cameras(["B", "C"])
            gate.set()
            t.join(2)
        self.assertEqual(started, ["A"])


if __name__ == "__main__":
    unittest.main()
