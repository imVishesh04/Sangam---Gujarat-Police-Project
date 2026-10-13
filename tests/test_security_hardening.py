import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db, bcrypt, limiter
from app.models import User, Camera, Watchlist, Alert, AuditLog


class SecurityHardeningTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("config.TestingConfig")
        cls.client = cls.app.test_client()
        with cls.app.app_context():
            db.create_all()
            # Admin user
            if not User.query.filter_by(username="sec_admin").first():
                admin = User(
                    username="sec_admin",
                    password_hash=bcrypt.generate_password_hash("admin123").decode("utf-8"),
                    role="admin",
                )
                db.session.add(admin)

            # Viewer user
            if not User.query.filter_by(username="sec_viewer").first():
                viewer = User(
                    username="sec_viewer",
                    password_hash=bcrypt.generate_password_hash("viewer123").decode("utf-8"),
                    role="viewer",
                )
                db.session.add(viewer)

            # Seed camera for security testing
            cam = db.session.get(Camera, "CAM-SEC-01")
            if not cam:
                cam = Camera(
                    camera_id="CAM-SEC-01",
                    department="Security Traffic",
                    location_name="Vastrapur Lake",
                    latitude=23.035,
                    longitude=72.525,
                    stream_url="https://live.corp8.cloud/live/stream/6/index.m3u8",
                    format="hls",
                    status="online",
                )
                db.session.add(cam)

            # Seed watchlist & alert
            wl = Watchlist.query.filter_by(value="GJ01SEC999").first()
            if not wl:
                wl = Watchlist(type="vehicle", value="GJ01SEC999", reason="Security Test Vehicle")
                db.session.add(wl)
                db.session.flush()
            cls.wl_id = wl.watchlist_id

            alert = Alert.query.filter_by(matched_value="GJ01SEC999").first()
            if not alert:
                alert = Alert(
                    watchlist_id=wl.watchlist_id,
                    camera_id="CAM-SEC-01",
                    matched_value="GJ01SEC999",
                    timestamp=datetime.now(timezone.utc),
                )
                db.session.add(alert)
                db.session.flush()
            cls.alert_id = alert.alert_id

            db.session.commit()

    def setUp(self):
        if getattr(limiter, "_storage", None):
            limiter.reset()
        self.client.post("/auth/login", json={"username": "sec_admin", "password": "admin123"})

    def tearDown(self):
        if getattr(limiter, "_storage", None):
            limiter.reset()
        limiter.enabled = False

    def test_security_headers_present(self):
        """Responses must include secure headers: X-Content-Type-Options, X-Frame-Options, CSP, etc."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(res.headers.get("X-XSS-Protection"), "1; mode=block")
        self.assertEqual(res.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertIn("default-src", res.headers.get("Content-Security-Policy", ""))

    def test_hsts_header_in_production(self):
        """When SESSION_COOKIE_SECURE is true, Strict-Transport-Security header must be present."""
        prod_app = create_app("config.TestingConfig")
        prod_app.config["SESSION_COOKIE_SECURE"] = True
        prod_client = prod_app.test_client()
        res = prod_client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertIn("max-age=31536000", res.headers.get("Strict-Transport-Security", ""))

    def test_session_cookie_flags(self):
        """Session cookie must be configured with HttpOnly and SameSite=Lax."""
        unauth_client = self.app.test_client()
        res = unauth_client.post("/auth/login", json={"username": "sec_admin", "password": "admin123"})
        self.assertEqual(res.status_code, 200)

        # Check Set-Cookie header
        cookie_header = res.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Lax", cookie_header)

    def test_audit_logging_auth_lifecycle(self):
        """Login success, login failure, and logout must write audit entries."""
        unauth_client = self.app.test_client()

        # Failed login
        res_fail = unauth_client.post("/auth/login", json={"username": "sec_admin", "password": "wrongpassword"})
        self.assertEqual(res_fail.status_code, 401)

        # Successful login
        res_ok = unauth_client.post("/auth/login", json={"username": "sec_admin", "password": "admin123"})
        self.assertEqual(res_ok.status_code, 200)

        # Logout
        res_out = unauth_client.post("/auth/logout")
        self.assertEqual(res_out.status_code, 200)

        with self.app.app_context():
            fail_log = AuditLog.query.filter_by(action="LOGIN_FAILURE").order_by(AuditLog.id.desc()).first()
            self.assertIsNotNone(fail_log)
            self.assertEqual(fail_log.target_id, "sec_admin")

            ok_log = AuditLog.query.filter_by(action="LOGIN_SUCCESS").order_by(AuditLog.id.desc()).first()
            self.assertIsNotNone(ok_log)
            self.assertEqual(ok_log.username, "sec_admin")

            logout_log = AuditLog.query.filter_by(action="LOGOUT").order_by(AuditLog.id.desc()).first()
            self.assertIsNotNone(logout_log)
            self.assertEqual(logout_log.username, "sec_admin")

    def test_audit_logging_watchlist_operations(self):
        """Adding, updating, and deleting watchlist items must write audit entries."""
        # Add
        res_add = self.client.post("/watchlist/", json={
            "type": "vehicle",
            "value": "GJ05SEC111",
            "reason": "Test Audit Add",
        })
        self.assertEqual(res_add.status_code, 201)
        wl_id = res_add.get_json()["watchlist_id"]

        # Update
        res_up = self.client.put(f"/watchlist/{wl_id}", json={
            "reason": "Updated Test Audit Reason",
        })
        self.assertEqual(res_up.status_code, 200)

        # Delete
        res_del = self.client.delete(f"/watchlist/{wl_id}")
        self.assertEqual(res_del.status_code, 200)

        with self.app.app_context():
            add_log = AuditLog.query.filter_by(action="WATCHLIST_ADD", target_id=str(wl_id)).first()
            self.assertIsNotNone(add_log)
            self.assertIn("GJ05SEC111", add_log.details)

            up_log = AuditLog.query.filter_by(action="WATCHLIST_UPDATE", target_id=str(wl_id)).first()
            self.assertIsNotNone(up_log)

            del_log = AuditLog.query.filter_by(action="WATCHLIST_DELETE", target_id=str(wl_id)).first()
            self.assertIsNotNone(del_log)

    def test_audit_logging_alert_acknowledge(self):
        """Acknowledging an alert must write an audit entry."""
        res = self.client.put(f"/alerts/{self.alert_id}/acknowledge", json={
            "action_taken": "Intercept unit dispatched",
            "acknowledged_by": "Inspector Patel",
        })
        self.assertEqual(res.status_code, 200)

        with self.app.app_context():
            ack_log = AuditLog.query.filter_by(action="ALERT_ACKNOWLEDGE", target_id=str(self.alert_id)).order_by(AuditLog.id.desc()).first()
            self.assertIsNotNone(ack_log)
            self.assertIn("Inspector Patel", ack_log.details)
            self.assertIn("Intercept unit dispatched", ack_log.details)

    def test_audit_logging_camera_operations(self):
        """Adding, updating, deleting, and importing cameras must write audit entries."""
        cam_id = "CAM-SEC-AUDIT"
        # Add
        self.client.delete(f"/cameras/{cam_id}")
        res_add = self.client.post("/cameras/", json={
            "camera_id": cam_id,
            "department": "Security Traffic",
            "location_name": "Audit Test Junction",
            "latitude": 23.01,
            "longitude": 72.50,
            "stream_url": "https://live.corp8.cloud/live/stream/6/index.m3u8",
            "format": "hls",
            "status": "online",
        })
        self.assertEqual(res_add.status_code, 201)

        # Update
        res_up = self.client.put(f"/cameras/{cam_id}", json={
            "location_name": "Updated Audit Junction",
        })
        self.assertEqual(res_up.status_code, 200)

        # Delete
        res_del = self.client.delete(f"/cameras/{cam_id}")
        self.assertEqual(res_del.status_code, 200)

        # Import
        csv_content = (
            "camera_id,department,location_name,latitude,longitude,vendor,stream_url,format,status\n"
            "CAM-SEC-BULK,Audit Police,Bulk Junction,23.05,72.55,Hikvision,https://live.corp8.cloud/live/stream/6/index.m3u8,hls,online\n"
        )
        res_imp = self.client.post("/cameras/import", json={"csv_data": csv_content})
        self.assertEqual(res_imp.status_code, 200)

        with self.app.app_context():
            add_log = AuditLog.query.filter_by(action="CAMERA_ADD", target_id=cam_id).first()
            self.assertIsNotNone(add_log)

            up_log = AuditLog.query.filter_by(action="CAMERA_UPDATE", target_id=cam_id).first()
            self.assertIsNotNone(up_log)

            del_log = AuditLog.query.filter_by(action="CAMERA_DELETE", target_id=cam_id).first()
            self.assertIsNotNone(del_log)

            imp_log = AuditLog.query.filter_by(action="CAMERA_IMPORT").order_by(AuditLog.id.desc()).first()
            self.assertIsNotNone(imp_log)
            self.assertIn("inserted=1", imp_log.details)

    def test_audit_logs_api_authorization_and_filter(self):
        """GET /audit-logs/ must require admin and support action filtering."""
        # Unauthenticated -> 401
        res_unauth = self.app.test_client().get("/audit-logs/")
        self.assertEqual(res_unauth.status_code, 401)

        # Viewer role -> 403
        viewer_client = self.app.test_client()
        viewer_client.post("/auth/login", json={"username": "sec_viewer", "password": "viewer123"})
        res_viewer = viewer_client.get("/audit-logs/")
        self.assertEqual(res_viewer.status_code, 403)

        # Admin role -> 200 with list of logs
        res_admin = self.client.get("/audit-logs/?limit=10")
        self.assertEqual(res_admin.status_code, 200)
        data = res_admin.get_json()
        self.assertIn("logs", data)
        self.assertIn("total", data)
        self.assertGreater(data["total"], 0)

    def test_rate_limiting_enforcement(self):
        """When running under production Config, excessive login requests must return 429."""
        rate_app = create_app("config.Config")
        rate_client = rate_app.test_client()

        status_codes = []
        for _ in range(20):
            r = rate_client.post("/auth/login", json={"username": "sec_admin", "password": "wrong"})
            status_codes.append(r.status_code)

        self.assertIn(429, status_codes)
        r_429 = rate_client.post("/auth/login", json={"username": "sec_admin", "password": "wrong"})
        self.assertEqual(r_429.status_code, 429)
        self.assertEqual(r_429.get_json()["error"], "Too Many Requests")

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            Alert.query.filter(Alert.camera_id.in_(["CAM-SEC-01", "CAM-SEC-BULK", "CAM-SEC-AUDIT"])).delete()
            Camera.query.filter(Camera.camera_id.in_(["CAM-SEC-01", "CAM-SEC-BULK", "CAM-SEC-AUDIT"])).delete()
            Watchlist.query.filter(Watchlist.value.in_(["GJ01SEC999", "GJ05SEC111"])).delete()
            User.query.filter(User.username.in_(["sec_admin", "sec_viewer"])).delete()
            db.session.commit()


if __name__ == "__main__":
    unittest.main()
