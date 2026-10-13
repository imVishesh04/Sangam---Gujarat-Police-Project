import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import get_database_uri
from app import create_app, db, bcrypt
from app.models import User, Camera
import migrate_db


class PostgresPostGISTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("config.TestingConfig")
        cls.client = cls.app.test_client()
        with cls.app.app_context():
            db.create_all()
            if not User.query.filter_by(username="gis_admin").first():
                admin = User(
                    username="gis_admin",
                    password_hash=bcrypt.generate_password_hash("admin123").decode("utf-8"),
                    role="admin",
                )
                db.session.add(admin)

            # Seed cameras with known coordinates:
            # Point A: Gandhinagar (23.2156, 72.6369)
            # Point B: Ahmedabad (23.0225, 72.5714) ~22 km from Point A
            # Point C: Surat (21.1702, 72.8311) ~230 km from Point A
            test_cams = [
                ("CAM-GIS-GANDHI", "Gandhinagar Traffic", "Gandhinagar Sector 1", 23.2156, 72.6369, "online"),
                ("CAM-GIS-AHM", "Ahmedabad Traffic", "Ahmedabad Ashram Rd", 23.0225, 72.5714, "online"),
                ("CAM-GIS-SURAT", "Surat Traffic", "Surat Ring Rd", 21.1702, 72.8311, "online"),
            ]
            for cid, dept, loc, lat, lon, st in test_cams:
                cam = db.session.get(Camera, cid)
                if not cam:
                    cam = Camera(
                        camera_id=cid,
                        department=dept,
                        location_name=loc,
                        latitude=lat,
                        longitude=lon,
                        stream_url="http://127.0.0.1:5000/stream.m3u8",
                        format="hls",
                        status=st,
                    )
                    db.session.add(cam)
            db.session.commit()

    def setUp(self):
        self.client.post("/auth/login", json={"username": "gis_admin", "password": "admin123"})

    def test_database_uri_normalization(self):
        """Verify DATABASE_URL normalizes postgres:// to postgresql:// and defaults to SQLite."""
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            uri = get_database_uri()
            self.assertTrue(uri.startswith("sqlite:///"))
            self.assertTrue(uri.endswith("cctv.db"))

        with patch.dict(os.environ, {"DATABASE_URL": "postgres://user:pass@localhost:5432/sangam"}):
            uri = get_database_uri()
            self.assertEqual(uri, "postgresql://user:pass@localhost:5432/sangam")

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost:5432/sangam"}):
            uri = get_database_uri()
            self.assertEqual(uri, "postgresql://user:pass@localhost:5432/sangam")

    def test_camera_geom_sync(self):
        """Verify Camera model automatically populates geom from coordinates."""
        with self.app.app_context():
            cam = db.session.get(Camera, "CAM-GIS-GANDHI")
            self.assertIsNotNone(cam)
            self.assertEqual(cam.geom, "POINT(72.6369 23.2156)")
            data = cam.to_dict()
            self.assertEqual(data["geom"], "POINT(72.6369 23.2156)")

    def test_nearby_unauthenticated(self):
        """Unauthenticated requests must return 401."""
        unauth_client = self.app.test_client()
        res = unauth_client.get("/cameras/nearby?lat=23.2156&lng=72.6369")
        self.assertEqual(res.status_code, 401)

    def test_nearby_validation_errors(self):
        """Invalid lat/lng/radius must return 400 with descriptive error."""
        # Missing coordinates
        res = self.client.get("/cameras/nearby")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Missing", res.get_json()["error"])

        # Non-numeric
        res = self.client.get("/cameras/nearby?lat=invalid&lng=72.6")
        self.assertEqual(res.status_code, 400)
        self.assertIn("numeric", res.get_json()["error"])

        # Out of bounds
        res = self.client.get("/cameras/nearby?lat=95.0&lng=72.6")
        self.assertEqual(res.status_code, 400)
        self.assertIn("out of bounds", res.get_json()["error"])

        # Non-positive radius
        res = self.client.get("/cameras/nearby?lat=23.0&lng=72.6&radius_km=-5")
        self.assertEqual(res.status_code, 400)
        self.assertIn("greater than 0", res.get_json()["error"])

    def test_nearby_spatial_query_sqlite_haversine(self):
        """Verify spatial lookup finds cameras within radius and sorts by distance."""
        # Query center Gandhinagar with 5 km radius: should only match Gandhinagar
        res = self.client.get("/cameras/nearby?lat=23.2156&lng=72.6369&radius_km=5")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["engine"], "sqlite_haversine")
        self.assertEqual(data["center"]["latitude"], 23.2156)
        self.assertEqual(data["center"]["longitude"], 72.6369)
        cams = [c["camera_id"] for c in data["cameras"]]
        self.assertIn("CAM-GIS-GANDHI", cams)
        self.assertNotIn("CAM-GIS-SURAT", cams)

        # Gandhinagar camera distance should be ~0.0 km
        gandhi_cam = next(c for c in data["cameras"] if c["camera_id"] == "CAM-GIS-GANDHI")
        self.assertAlmostEqual(gandhi_cam["distance_km"], 0.0, delta=0.01)

        # Expand radius to 30 km: should now include Ahmedabad (~22 km) but NOT Surat (~230 km)
        res30 = self.client.get("/cameras/nearby?lat=23.2156&lng=72.6369&radius_km=30")
        self.assertEqual(res30.status_code, 200)
        cams30 = [c["camera_id"] for c in res30.get_json()["cameras"]]
        self.assertIn("CAM-GIS-GANDHI", cams30)
        self.assertIn("CAM-GIS-AHM", cams30)
        self.assertNotIn("CAM-GIS-SURAT", cams30)

        # Verify ascending distance ordering
        distances = [c["distance_km"] for c in res30.get_json()["cameras"]]
        self.assertEqual(distances, sorted(distances))

    def test_nearby_department_filter(self):
        """Department filter should refine spatial results."""
        res = self.client.get(
            "/cameras/nearby?lat=23.2156&lng=72.6369&radius_km=30&department=Gandhinagar%20Traffic"
        )
        self.assertEqual(res.status_code, 200)
        cams = [c["camera_id"] for c in res.get_json()["cameras"]]
        self.assertIn("CAM-GIS-GANDHI", cams)
        self.assertNotIn("CAM-GIS-AHM", cams)

    def test_postgis_sql_branch(self):
        """Verify the PostGIS ST_DWithin SQL execution path when dialect is postgresql."""
        with self.app.app_context():
            mock_engine = MagicMock()
            mock_engine.dialect.name = "postgresql"
            mock_conn = MagicMock()
            mock_engine.connect.return_value.__enter__.return_value = mock_conn

            # Mock returned row from ST_DWithin
            mock_row = MagicMock()
            mock_row._mapping = {
                "camera_id": "CAM-PG-01",
                "department": "Traffic",
                "location_name": "Ring Road",
                "latitude": 23.2,
                "longitude": 72.6,
                "geom": "0101000020E6100000",
                "vendor": "Axis",
                "stream_url": "rtsp://camera1",
                "format": "rtsp",
                "status": "online",
                "last_heartbeat": None,
                "distance_km": 1.25,
            }
            mock_conn.execute.return_value.fetchall.return_value = [mock_row]

            with patch.object(type(db), "engine", new_callable=PropertyMock) as mock_prop:
                mock_prop.return_value = mock_engine
                res = self.client.get("/cameras/nearby?lat=23.2&lng=72.6&radius_km=5")
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertEqual(data["engine"], "postgis")
                self.assertEqual(len(data["cameras"]), 1)
                self.assertEqual(data["cameras"][0]["camera_id"], "CAM-PG-01")
                self.assertEqual(data["cameras"][0]["distance_km"], 1.25)
                # Verify that ST_DWithin and ST_Distance SQL was executed
                call_args = mock_conn.execute.call_args[0]
                sql_executed = str(call_args[0])
                self.assertIn("ST_DWithin", sql_executed)
                self.assertIn("ST_Distance", sql_executed)

    def test_migrate_db_script(self):
        """Verify migrate_db.py check and execution work without error."""
        check_ok = migrate_db.run_migration(check_only=True)
        self.assertTrue(check_ok)

        run_ok = migrate_db.run_migration()
        self.assertTrue(run_ok)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            Camera.query.filter(Camera.camera_id.in_(["CAM-GIS-GANDHI", "CAM-GIS-AHM", "CAM-GIS-SURAT"])).delete()
            User.query.filter_by(username="gis_admin").delete()
            db.session.commit()


if __name__ == "__main__":
    unittest.main()
