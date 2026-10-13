"""Tests for the demo route generator (POST /track/demo/<plate>)."""
import os
import random
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db  # noqa: E402
from app.models import Detection  # noqa: E402
from app.routes.tracking import _km, build_demo_route  # noqa: E402

PLATE = "GJ99TS0001"  # test-only plate, not on the watchlist


def cam(i, lat, lon, status="offline"):
    return NS(camera_id=f"C{i}", latitude=lat, longitude=lon, status=status, location_name=f"Cam {i}")


GRID = [cam(i, 21.0 + (i % 6) * 0.4, 70.5 + (i // 6) * 0.4) for i in range(30)]


class TestBuildRoute(unittest.TestCase):
    def test_length_and_unique(self):
        route, _ = build_demo_route(GRID, 7, random.Random(1))
        self.assertEqual(len(route), 7)
        self.assertEqual(len({c.camera_id for c in route}), 7)

    def test_different_runs_give_different_routes(self):
        routes = {tuple(c.camera_id for c in build_demo_route(GRID, 6, random.Random(s))[0]) for s in range(8)}
        self.assertGreater(len(routes), 4)

    def test_online_cameras_preferred(self):
        cams = [cam(i, c.latitude, c.longitude, "online" if i < 8 else "offline") for i, c in enumerate(GRID)]
        route, used_offline = build_demo_route(cams, 6, random.Random(2))
        self.assertFalse(used_offline)
        self.assertTrue(all(c.status == "online" for c in route))

    def test_falls_back_when_too_few_online(self):
        cams = [cam(i, c.latitude, c.longitude, "online" if i < 2 else "offline") for i, c in enumerate(GRID)]
        route, used_offline = build_demo_route(cams, 6, random.Random(3))
        self.assertTrue(used_offline)
        self.assertEqual(len(route), 6)

    def test_ignores_cameras_without_gps_or_outside_gujarat(self):
        cams = GRID[:3] + [cam(90, None, None), cam(91, 28.6, 77.2)]
        route, _ = build_demo_route(cams, 5, random.Random(4))
        self.assertTrue(all(c.camera_id.startswith("C") and int(c.camera_id[1:]) < 3 for c in route))

    def test_steps_are_local(self):
        route, _ = build_demo_route(GRID, 7, random.Random(5))
        for a, b in zip(route, route[1:]):
            self.assertLess(_km((a.latitude, a.longitude), (b.latitude, b.longitude)), 120)


class TestTripShape(unittest.TestCase):
    def test_no_back_and_forth(self):
        from app.routes.tracking import _xy
        for seed in range(10):
            route, _ = build_demo_route(GRID, 6, random.Random(seed))
            a, b = route[0], route[-1]
            ex, ey = _xy(b, a)
            ts = [(_xy(c, a)[0] * ex + _xy(c, a)[1] * ey) for c in route]
            self.assertEqual(ts, sorted(ts), f"seed {seed}")


class FakeResp:
    def __init__(self, code, data):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


class TestRoadRoute(unittest.TestCase):
    def setUp(self):
        import app.routes.tracking as t
        self.t = t
        t._route_cache = {}
        t._save_route_cache = lambda: None

    def test_second_router_used_when_first_fails(self):
        calls = []

        def get(url, **kw):
            calls.append(url)
            if "project-osrm" in url:
                raise TimeoutError()
            return FakeResp(200, {"code": "Ok", "routes": [{"distance": 12000, "duration": 900,
                              "legs": [{"distance": 12000, "duration": 900}],
                              "geometry": {"coordinates": [[72.5, 23.0], [72.6, 23.1]]}}]})
        r = self.t.fetch_road_route([(23.0, 72.5), (23.1, 72.6)], http_get=get)
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "osm-de")
        self.assertEqual(r["geometry"][0], [23.0, 72.5])  # lat, lon order
        self.assertEqual(len(calls), 2)
        again = self.t.fetch_road_route([(23.0, 72.5), (23.1, 72.6)], http_get=lambda *a, **k: 1 / 0)
        self.assertTrue(again["cached"])

    def test_all_routers_down(self):
        r = self.t.fetch_road_route([(21.0, 70.0), (21.2, 70.3)], http_get=lambda *a, **k: FakeResp(503, {}))
        self.assertFalse(r["ok"])
        self.assertIn("HTTP 503", r["error"])


class TestEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("config.TestingConfig")
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        db.create_all()
        cls.client = cls.app.test_client()
        r = cls.client.post("/auth/login", json={"username": "admin", "password": "admin123"})
        cls.logged_in = r.status_code == 200

    @classmethod
    def tearDownClass(cls):
        Detection.query.filter(Detection.plate_number == PLATE).delete()
        db.session.commit()
        db.session.remove()
        cls.ctx.pop()

    def test_generates_route_with_increasing_times(self):
        if not self.logged_in:
            self.skipTest("admin/admin123 not seeded in this database")
        r = self.client.post(f"/track/demo/{PLATE}", json={"stops": 6})
        self.assertEqual(r.status_code, 201, r.get_json())
        body = r.get_json()
        self.assertEqual(len(body["stops"]), 6)
        times = [s["timestamp"] for s in body["stops"]]
        self.assertEqual(times, sorted(times))
        track = self.client.get(f"/track/{PLATE}").get_json()
        self.assertEqual(track["total_sightings"], 6)
        self.assertTrue(all(p["confidence"] is None for p in track["route"]))

    def test_regenerating_replaces_previous_demo_route(self):
        if not self.logged_in:
            self.skipTest("admin/admin123 not seeded in this database")
        self.client.post(f"/track/demo/{PLATE}", json={"stops": 5})
        self.client.post(f"/track/demo/{PLATE}", json={"stops": 7})
        track = self.client.get(f"/track/{PLATE}").get_json()
        self.assertEqual(track["total_sightings"], 7)


if __name__ == "__main__":
    unittest.main()
