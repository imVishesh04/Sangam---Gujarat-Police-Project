"""Severity levels and the nearest-unit dispatch agent."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services import dispatch, severity  # noqa: E402

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)


class TestSeverity(unittest.TestCase):
    def test_base_levels(self):
        self.assertEqual(severity.base_level("Kidnapping suspect car")[0], "critical")
        self.assertEqual(severity.base_level("Stolen vehicle - FIR 12")[0], "high")
        self.assertEqual(severity.base_level("Suspicious vehicle")[0], "medium")
        self.assertEqual(severity.base_level("Expired registration")[0], "low")

    def test_short_words_match_whole_words_only(self):
        self.assertEqual(severity.base_level("Seen near the hats market")[0], "medium")  # 'ats' inside a word
        self.assertEqual(severity.base_level("ATS lookout")[0], "critical")

    def test_clone_raises_to_high(self):
        self.assertEqual(severity.classify("Expired registration", clone={"km": 200})["level"], "high")

    def test_moving_vehicle_escalates(self):
        s = [{"time": NOW - timedelta(minutes=m), "camera_id": f"C{m}"} for m in (1, 8, 15)]
        r = severity.classify("Stolen vehicle", s, now=NOW)
        self.assertEqual(r["level"], "critical")
        self.assertIn("moving", r["why"])

    def test_old_sightings_do_not_escalate(self):
        s = [{"time": NOW - timedelta(hours=h), "camera_id": f"C{h}"} for h in (1, 2, 3)]
        self.assertEqual(severity.classify("Stolen vehicle", s, now=NOW)["level"], "high")


UNITS = [
    {"unit_id": "P-WEST", "name": "PCR west", "type": "pcr", "area": "A", "lat": 23.00, "lon": 72.50, "status": "available"},
    {"unit_id": "P-EAST", "name": "PCR east", "type": "pcr", "area": "A", "lat": 23.00, "lon": 72.62, "status": "available"},
    {"unit_id": "P-BUSY", "name": "PCR busy", "type": "pcr", "area": "A", "lat": 23.00, "lon": 72.56, "status": "busy"},
    {"unit_id": "T-1", "name": "Traffic", "type": "traffic", "area": "A", "lat": 23.00, "lon": 72.561, "status": "available"},
    {"unit_id": "S-1", "name": "Station", "type": "station", "area": "A", "lat": 23.01, "lon": 72.56, "status": "available"},
    {"unit_id": "Q-1", "name": "QRT", "type": "qrt", "area": "A", "lat": 23.10, "lon": 72.56, "status": "available"},
]


class TestDispatch(unittest.TestCase):
    def test_busy_units_skipped(self):
        r = dispatch.suggest(23.0, 72.56, "high", unit_list=UNITS)
        self.assertNotIn("P-BUSY", [u["unit_id"] for u in r["units"]])

    def test_low_severity_never_sends_pcr_or_qrt(self):
        r = dispatch.suggest(23.0, 72.56, "low", unit_list=UNITS)
        self.assertTrue(all(u["type"] in ("traffic", "station") for u in r["units"]))
        self.assertEqual(r["units"][0]["unit_id"], "T-1")

    def test_unit_ahead_of_vehicle_wins(self):
        # vehicle moving east: 72.54 -> 72.56 in 3 minutes
        s = [{"time": NOW, "camera_id": "B", "lat": 23.0, "lon": 72.56},
             {"time": NOW - timedelta(minutes=3), "camera_id": "A", "lat": 23.0, "lon": 72.54}]
        r = dispatch.suggest(23.0, 72.56, "high", s, unit_list=UNITS)
        self.assertEqual(r["prediction"]["heading"], "E")
        self.assertEqual(r["units"][0]["unit_id"], "P-EAST")
        self.assertIn("ahead", r["units"][0]["why"])

    def test_best_is_nearest_when_not_moving(self):
        units = [
            {"unit_id": "P-FAR", "name": "PCR", "type": "pcr", "area": "A", "lat": 23.04, "lon": 72.56, "status": "available"},
            {"unit_id": "S-NEAR", "name": "Station", "type": "station", "area": "A", "lat": 23.03, "lon": 72.56, "status": "available"},
        ]
        r = dispatch.suggest(23.0, 72.56, "high", unit_list=units)
        self.assertEqual(r["units"][0]["unit_id"], "S-NEAR")
        self.assertIn("nearest", r["units"][0]["why"])

    def test_station_added_for_high(self):
        r = dispatch.suggest(23.0, 72.56, "critical", unit_list=UNITS)
        self.assertEqual(r["station"]["unit_id"], "S-1")
        self.assertIsNone(dispatch.suggest(23.0, 72.56, "medium", unit_list=UNITS)["station"])

    def test_no_gps(self):
        self.assertFalse(dispatch.suggest(None, None, "high", unit_list=UNITS)["ok"])

    def test_sample_registry_loads(self):
        self.assertGreater(len(dispatch.units()), 10)
        self.assertTrue(all(u["phone"] == "" for u in dispatch.units()))  # no real numbers shipped


class TestEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app, db
        from app.models import Camera, Detection, Watchlist
        from app.services.matching_engine import check_and_alert
        cls.app = create_app("config.TestingConfig")
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        db.create_all()
        cls.client = cls.app.test_client()
        cls.logged_in = cls.client.post("/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200
        if not db.session.get(Camera, "TEST-DSP-CAM"):
            db.session.add(Camera(camera_id="TEST-DSP-CAM", department="T", location_name="Dispatch test cam",
                                  latitude=23.0156, longitude=72.5311, vendor="t", format="HLS", stream_url="", status="offline"))
        wl = Watchlist.query.filter_by(value="GJ99DS0001").first()
        if not wl:
            wl = Watchlist(type="vehicle", value="GJ99DS0001", reason="Stolen vehicle (test)")
            db.session.add(wl)
        db.session.commit()
        det = Detection(camera_id="TEST-DSP-CAM", plate_number="GJ99DS0001", confidence=None)
        db.session.add(det)
        db.session.commit()
        cls.alert = check_and_alert(det)

    @classmethod
    def tearDownClass(cls):
        from app import db
        from app.models import Alert, Camera, Detection, Watchlist
        Alert.query.filter_by(matched_value="GJ99DS0001").delete()
        Detection.query.filter_by(plate_number="GJ99DS0001").delete()
        Watchlist.query.filter_by(value="GJ99DS0001").delete()
        Camera.query.filter_by(camera_id="TEST-DSP-CAM").delete()
        db.session.commit()
        cls.ctx.pop()

    def setUp(self):
        if not self.logged_in:
            self.skipTest("admin not seeded")

    def test_alert_list_has_severity(self):
        alerts = self.client.get("/alerts/").get_json()["alerts"]
        mine = next(a for a in alerts if a["alert_id"] == self.alert.alert_id)
        self.assertEqual(mine["severity"], "high")

    def test_suggest_and_assign(self):
        r = self.client.get(f"/dispatch/suggest/{self.alert.alert_id}").get_json()
        self.assertTrue(r["ok"])
        self.assertIn(r["units"][0]["type"], ("pcr", "station"))
        if not r.get("prediction"):  # not moving: BEST must be the nearest unit
            d = [u["distance_km"] for u in r["units"]]
            self.assertLessEqual(d[0], min(d) + 0.3)
        a = self.client.post(f"/dispatch/assign/{self.alert.alert_id}", json={"unit_id": r["units"][0]["unit_id"]})
        self.assertEqual(a.status_code, 200)
        self.assertIn("Assigned to", a.get_json()["action_taken"])
        self.assertRegex(a.get_json()["routed_to"], "control room|outbox")

    def test_impossible_speed_gives_no_heading(self):
        s = [{"time": NOW, "camera_id": "B", "lat": 22.3, "lon": 70.8},
             {"time": NOW - timedelta(minutes=5), "camera_id": "A", "lat": 23.0, "lon": 72.5}]
        self.assertIsNone(dispatch.predict(s))


if __name__ == "__main__":
    unittest.main()
