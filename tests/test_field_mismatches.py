import os
import sys
import io
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db, bcrypt
from app.models import User, Camera, Watchlist, Alert

class FieldMismatchesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("config.TestingConfig")
        cls.client = cls.app.test_client()
        with cls.app.app_context():
            db.create_all()
            if not User.query.filter_by(username="mismatch_admin").first():
                admin = User(
                    username="mismatch_admin",
                    password_hash=bcrypt.generate_password_hash("admin123").decode("utf-8"),
                    role="admin"
                )
                db.session.add(admin)
            cam = db.session.get(Camera, "CAM-MISMATCH-1")
            if not cam:
                cam = Camera(
                    camera_id="CAM-MISMATCH-1",
                    department="Ahmedabad Traffic",
                    location_name="Iskcon Crossroad",
                    latitude=23.029,
                    longitude=72.507,
                    vendor="Hikvision",
                    stream_url="https://live.corp8.cloud/live/stream/6/index.m3u8",
                    format="hls",
                    status="online"
                )
                db.session.add(cam)
            wl = Watchlist.query.filter_by(value="GJ01XX9999").first()
            if not wl:
                wl = Watchlist(type="vehicle", value="GJ01XX9999", reason="Stolen Vehicle - FIR 88/2026")
                db.session.add(wl)
                db.session.flush()
            cls.wl_id = wl.watchlist_id
            alert = Alert.query.filter_by(matched_value="GJ01XX9999").first()
            if not alert:
                alert = Alert(
                    watchlist_id=wl.watchlist_id,
                    camera_id="CAM-MISMATCH-1",
                    matched_value="GJ01XX9999",
                    timestamp=datetime.now(timezone.utc)
                )
                db.session.add(alert)
                db.session.flush()
            cls.alert_id = alert.alert_id
            db.session.commit()

    def setUp(self):
        self.client.post("/auth/login", json={"username": "mismatch_admin", "password": "admin123"})
        # Clean up any test cameras from prior runs
        with self.app.app_context():
            Camera.query.filter(Camera.camera_id.like("CAM-CSV-%")).delete()
            db.session.commit()

    def test_unauthenticated_acknowledge_returns_401(self):
        unauth_client = self.app.test_client()
        r = unauth_client.put(f"/alerts/{self.alert_id}/acknowledge", json={"notes": "test"})
        self.assertEqual(r.status_code, 401)

    def test_acknowledge_with_integer_id(self):
        r = self.client.put(f"/alerts/{self.alert_id}/acknowledge", json={
            "notes": "PCR Unit 4 dispatched to Iskcon Crossroad",
            "acknowledged_by": "Inspector R. Sharma"
        })
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["is_acknowledged"])
        self.assertEqual(data["acknowledged_by"], "Inspector R. Sharma")
        self.assertEqual(data["action_taken"], "PCR Unit 4 dispatched to Iskcon Crossroad")
        self.assertIsNotNone(data["acknowledged_at"])
        self.assertEqual(data["location_name"], "Iskcon Crossroad")
        self.assertEqual(data["reason"], "Stolen Vehicle - FIR 88/2026")

    def test_acknowledge_with_string_id_in_url(self):
        r = self.client.put(f"/alerts/{str(self.alert_id)}/acknowledge", json={
            "action_taken": "Intercept verified by Flying Squad"
        })
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["is_acknowledged"])
        self.assertEqual(data["action_taken"], "Intercept verified by Flying Squad")

    def test_acknowledge_invalid_id_returns_400(self):
        r = self.client.put("/alerts/not-a-number/acknowledge", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Invalid alert_id", r.get_json().get("error", ""))

    def test_acknowledge_nonexistent_alert_returns_404(self):
        r = self.client.put("/alerts/9999999/acknowledge", json={})
        self.assertEqual(r.status_code, 404)

    def test_csv_import_json_payload_with_headers(self):
        csv_text = "camera_id,department,location_name,latitude,longitude,vendor,stream_url,format,status\nCAM-CSV-001,Gandhinagar Police,Ch-0 Circle,23.2156,72.6369,Dahua,rtsp://10.0.1.5/live,rtsp,offline\nCAM-CSV-002,Gandhinagar Police,Infocity Gate,23.1923,72.6289,Hikvision,rtsp://10.0.1.6/live,rtsp,online\n"
        r = self.client.post("/cameras/import", json={"csv_data": csv_text})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertGreaterEqual(data["inserted"], 2)
        self.assertEqual(len(data["errors"]), 0)
        with self.app.app_context():
            c1 = db.session.get(Camera, "CAM-CSV-001")
            self.assertIsNotNone(c1)
            self.assertEqual(c1.location_name, "Ch-0 Circle")
            self.assertEqual(c1.department, "Gandhinagar Police")

    def test_csv_import_json_headerless_positional(self):
        csv_text = "CAM-CSV-003,Surat Traffic,Majura Gate,21.1702,72.8311,CP Plus,rtsp://10.0.2.1/stream,rtsp,offline"
        r = self.client.post("/cameras/import", json={"csv_data": csv_text})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertGreaterEqual(data["inserted"], 1)
        with self.app.app_context():
            c3 = db.session.get(Camera, "CAM-CSV-003")
            self.assertIsNotNone(c3)
            self.assertEqual(c3.location_name, "Majura Gate")

    def test_csv_import_multipart_file_upload(self):
        csv_bytes = b"camera_id,department,location_name,latitude,longitude,vendor,stream_url,format,status\nCAM-CSV-004,Vadodara City Police,Alkapuri Underpass,22.3106,73.1812,Bosch,rtsp://10.0.3.1/stream,rtsp,online\n"
        data = {"file": (io.BytesIO(csv_bytes), "cameras_export.csv")}
        r = self.client.post("/cameras/import", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        data_json = r.get_json()
        self.assertGreaterEqual(data_json["inserted"], 1)
        with self.app.app_context():
            c4 = db.session.get(Camera, "CAM-CSV-004")
            self.assertIsNotNone(c4)
            self.assertEqual(c4.location_name, "Alkapuri Underpass")

    def test_csv_import_duplicate_skipped(self):
        # Insert a pre-existing camera first
        with self.app.app_context():
            existing = Camera(
                camera_id="CAM-CSV-DUP",
                department="Rajkot City Police",
                location_name="Kotecha Chowk",
                stream_url="rtsp://10.0.9.1/stream"
            )
            db.session.add(existing)
            db.session.commit()

        csv_text = "camera_id,department,location_name,latitude,longitude,vendor,stream_url,format,status\nCAM-CSV-DUP,Rajkot City Police,Kotecha Chowk,22.29,70.78,Dahua,rtsp://10.0.9.1/stream,rtsp,offline\n"
        r = self.client.post("/cameras/import", json={"csv_data": csv_text})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["inserted"], 0)
        self.assertEqual(data["skipped"], 1)

    def test_health_stats_endpoint_shape(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("database_engine", data)
        self.assertIn("cameras", data)
        self.assertIn("db_total", data["cameras"])
        self.assertIn("db_online", data["cameras"])
        self.assertIn("db_offline", data["cameras"])
        self.assertIn("detections_today", data)
        self.assertIn("alerts_today", data)
        self.assertIn("active_watchlists", data)
        self.assertIn("timestamp", data)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            Camera.query.filter(Camera.camera_id.in_(["CAM-MISMATCH-1", "CAM-CSV-DUP"])).delete()
            Watchlist.query.filter_by(value="GJ01XX9999").delete()
            User.query.filter_by(username="mismatch_admin").delete()
            db.session.commit()

if __name__ == "__main__":
    unittest.main()
