"""
Vehicle Tracking Blueprint — Module 5

Endpoint
--------
GET /track/<plate_number>
    Returns all detections for a plate sorted by time ascending,
    with camera location attached — ready to plot as a route on a map.

Query params
------------
from     : ISO-8601 start datetime
to       : ISO-8601 end datetime
"""
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_login import login_required
from app.models import Detection, Camera
from app.models.models import _iso

tracking_bp = Blueprint("tracking", __name__, url_prefix="/track")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@tracking_bp.get("/<plate_number>")
@login_required
def track_vehicle(plate_number: str):
    """
    Reconstruct the route of a vehicle across all cameras.

    Each element in the returned route array represents one camera sighting:
      { camera_id, location_name, latitude, longitude, timestamp }

    The array is sorted by timestamp ascending so the frontend can draw
    a chronological path on a map.
    """
    from_dt = _parse_iso(request.args.get("from"))
    to_dt = _parse_iso(request.args.get("to"))

    query = (
        Detection.query
        .filter(Detection.plate_number.ilike(plate_number))
        .order_by(Detection.timestamp.asc())
    )
    if from_dt:
        query = query.filter(Detection.timestamp >= from_dt)
    if to_dt:
        query = query.filter(Detection.timestamp <= to_dt)

    detections = query.all()

    # Cache camera lookups to avoid N+1 queries
    cam_cache: dict[str, Camera] = {}
    route = []
    for d in detections:
        if d.camera_id not in cam_cache:
            cam_cache[d.camera_id] = Camera.query.get(d.camera_id)
        cam = cam_cache[d.camera_id]
        route.append({
            "detection_id": d.detection_id,
            "camera_id": d.camera_id,
            "location_name": cam.location_name if cam else None,
            "latitude": cam.latitude if cam else None,
            "longitude": cam.longitude if cam else None,
            "confidence": d.confidence,
            "timestamp": _iso(d.timestamp),
        })

    return jsonify({
        "plate_number": plate_number.upper(),
        "total_sightings": len(route),
        "route": route,
    })


# ---------------------------------------------------------------------------
# Demo route generator (admin) - for live demos without a real multi-camera trip
# ---------------------------------------------------------------------------
# Builds a believable 5-7 checkpoint trip for a plate across the REGISTERED cameras
# (online cameras first), stores the sightings as manual test entries
# (confidence = NULL, shown everywhere as "manual entry"), and sends the LAST
# sighting through watchlist matching so the alert + WhatsApp include the route.

import math as _math
import random as _random
from datetime import timedelta as _timedelta

from flask_login import current_user as _current_user

_GUJ_LAT = (20.0, 24.8)
_GUJ_LON = (68.0, 74.6)


def _km(a, b):
    r = 6371.0
    p1, p2 = _math.radians(a[0]), _math.radians(b[0])
    dp, dl = _math.radians(b[0] - a[0]), _math.radians(b[1] - a[1])
    h = _math.sin(dp / 2) ** 2 + _math.cos(p1) * _math.cos(p2) * _math.sin(dl / 2) ** 2
    return 2 * r * _math.asin(_math.sqrt(h))


def _xy(c, origin):
    """Flat km coordinates around origin - good enough inside one state."""
    import math
    return ((c.longitude - origin.longitude) * 111.32 * math.cos(math.radians(origin.latitude)),
            (c.latitude - origin.latitude) * 110.57)


def _corridor_trip(pool, n_stops, rng):
    """
    A believable journey: random start A, a destination B, and checkpoints that lie
    between A and B (close to the A-B line), visited in order - no back-and-forth.
    Returns [] when no destination has enough cameras on the way.
    """
    if n_stops < 2 or len(pool) < n_stops:
        return []
    starts = list(pool)
    rng.shuffle(starts)
    for start in starts[:12]:
        options = []
        for end in pool:
            if end is start:
                continue
            ex, ey = _xy(end, start)
            length = (ex * ex + ey * ey) ** 0.5
            if length < 5 or length > 250:
                continue
            width = max(6.0, 0.35 * length)
            mids = []
            for c in pool:
                if c is start or c is end:
                    continue
                cx, cy = _xy(c, start)
                t = (cx * ex + cy * ey) / (length * length)
                side = abs(cx * ey - cy * ex) / length
                if 0.03 < t < 0.97 and side <= width:
                    mids.append((t, side, c))
            if len(mids) >= n_stops - 2:
                options.append((end, mids))
        if not options:
            continue
        end, mids = rng.choice(options)
        mids.sort(key=lambda m: m[1])               # closest to the road line first
        pick = rng.sample(mids[: max(n_stops - 2, 2 * (n_stops - 2))], n_stops - 2)
        pick.sort(key=lambda m: m[0])               # then in travel order
        return [start] + [m[2] for m in pick] + [end]
    return []


def build_demo_route(cameras, n_stops, rng=_random):
    """
    cameras: list of objects with camera_id, latitude, longitude, status.
    Returns (ordered_camera_list, used_offline: bool). A random start camera, then
    repeatedly one of the 3 nearest unvisited cameras (at least 2 km away), so every
    run gives a different but geographically sensible trip.
    """
    located = [c for c in cameras
               if c.latitude is not None and c.longitude is not None
               and _GUJ_LAT[0] <= c.latitude <= _GUJ_LAT[1] and _GUJ_LON[0] <= c.longitude <= _GUJ_LON[1]]
    online = [c for c in located if (c.status or "").lower() == "online"]
    pool = online if len(online) >= n_stops else located
    used_offline = pool is located and len(online) < n_stops
    if len(pool) < 2:
        return [], used_offline
    n_stops = min(n_stops, len(pool))

    trip = _corridor_trip(pool, n_stops, rng)
    if trip:
        return trip, used_offline

    # fallback (cameras too scattered for a corridor): hop to one of the 3 nearest
    route = [rng.choice(pool)]
    remaining = [c for c in pool if c is not route[0]]
    while len(route) < n_stops and remaining:
        here = (route[-1].latitude, route[-1].longitude)
        ranked = sorted(remaining, key=lambda c: _km(here, (c.latitude, c.longitude)))
        far_enough = [c for c in ranked if _km(here, (c.latitude, c.longitude)) >= 2] or ranked
        nxt = rng.choice(far_enough[:3])
        route.append(nxt)
        remaining.remove(nxt)
    return route, used_offline


@tracking_bp.post("/demo/<plate_number>")
@login_required
def generate_demo_route(plate_number: str):
    if _current_user.role != "admin":
        return jsonify({"error": "Admin access required"}), 403
    from app import db
    from app.services.matching_engine import check_and_alert

    plate = plate_number.strip().upper()
    data = request.get_json(silent=True) or {}
    n = data.get("stops")
    n = int(n) if isinstance(n, (int, float, str)) and str(n).isdigit() else _random.randint(5, 7)
    n = max(2, min(n, 10))

    cams = Camera.query.all()
    # the ingestion workers know the live state; it wins over the stored column
    try:
        from types import SimpleNamespace
        from app.services.video_ingestion import get_all_statuses
        live = get_all_statuses()
    except Exception:  # noqa: BLE001
        live = {}
    if live:
        cams = [SimpleNamespace(camera_id=c.camera_id, latitude=c.latitude, longitude=c.longitude,
                                location_name=c.location_name, status=live.get(c.camera_id, c.status))
                for c in cams]
    route, used_offline = build_demo_route(cams, n)
    if len(route) < 2:
        return jsonify({"error": "Need at least 2 cameras with GPS coordinates"}), 400

    # replace earlier manual test sightings for this plate (real ANPR reads are kept)
    Detection.query.filter(Detection.plate_number.ilike(plate), Detection.confidence.is_(None)).delete(
        synchronize_session=False)

    # timestamps: last stop = now; earlier stops by road-ish travel time (~45 km/h, 1.35x
    # straight-line) plus a few minutes at each checkpoint -> never trips the cloned-plate check
    times = [datetime.now(timezone.utc)]
    for newer, older in zip(reversed(route[1:]), reversed(route[:-1])):
        km = _km((older.latitude, older.longitude), (newer.latitude, newer.longitude))
        minutes = km * 1.35 / 45 * 60 + _random.randint(2, 8)
        times.append(times[-1] - _timedelta(minutes=minutes))
    times.reverse()

    for cam, ts in zip(route[:-1], times[:-1]):
        db.session.add(Detection(camera_id=cam.camera_id, plate_number=plate, confidence=None, timestamp=ts))
    last = Detection(camera_id=route[-1].camera_id, plate_number=plate, confidence=None, timestamp=times[-1])
    db.session.add(last)
    db.session.commit()
    alert = check_and_alert(last)

    online_used = sum(1 for c in route if (c.status or "").lower() == "online")
    return jsonify({
        "plate_number": plate,
        "stops": [{"camera_id": c.camera_id, "location_name": c.location_name,
                   "status": c.status, "timestamp": _iso(t)} for c, t in zip(route, times)],
        "online_cameras_used": online_used,
        "online_cameras_available": sum(1 for c in cams if (c.status or "").lower() == "online"),
        "cameras_registered": len(cams),
        "used_offline_cameras": used_offline,
        "alert_id": alert.alert_id if alert else None,
        "note": "Demo route made of manual test sightings (not ANPR reads).",
    }), 201


# ---------------------------------------------------------------------------
# Road route through checkpoints (server-side, so a browser/network quirk cannot
# break it). Tries two free OpenStreetMap routers; good answers are cached on disk
# so a route that worked once still works if the venue internet drops later.
# ---------------------------------------------------------------------------
import json as _json
import os as _os
import threading as _threading

_ROUTERS = [
    ("osrm-demo", "https://router.project-osrm.org/route/v1/driving/"),
    ("osm-de", "https://routing.openstreetmap.de/routed-car/route/v1/driving/"),
]
_ROUTE_CACHE_FILE = _os.path.join(_os.path.dirname(__file__), "..", "..", "demo_output", "road_route_cache.json")
_route_cache_lock = _threading.Lock()
_route_cache = None


def _load_route_cache():
    global _route_cache
    if _route_cache is None:
        try:
            with open(_ROUTE_CACHE_FILE, encoding="utf-8") as fh:
                _route_cache = _json.load(fh)
        except Exception:  # noqa: BLE001
            _route_cache = {}
    return _route_cache


def _save_route_cache():
    try:
        _os.makedirs(_os.path.dirname(_ROUTE_CACHE_FILE), exist_ok=True)
        items = list(_route_cache.items())[-200:]  # keep the file small
        with open(_ROUTE_CACHE_FILE, "w", encoding="utf-8") as fh:
            _json.dump(dict(items), fh)
    except Exception:  # noqa: BLE001
        pass


def fetch_road_route(points, http_get=None):
    """points: [(lat, lon), ...] -> dict(ok, source, distance, duration, legs, geometry) or ok=False."""
    key = ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in points)
    with _route_cache_lock:
        cached = _load_route_cache().get(key)
    if cached:
        return {**cached, "cached": True}

    if http_get is None:
        import requests
        http_get = requests.get
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    errors = []
    for name, base in _ROUTERS:
        try:
            resp = http_get(f"{base}{coords}", params={"overview": "full", "geometries": "geojson", "steps": "false"},
                            timeout=15, headers={"User-Agent": "SANGAM-hackathon-demo"})
            data = resp.json() if resp.status_code == 200 else {}
            route = (data.get("routes") or [None])[0]
            if not route or not route.get("geometry", {}).get("coordinates"):
                errors.append(f"{name}: HTTP {resp.status_code} {data.get('code', '')}".strip())
                continue
            result = {
                "ok": True,
                "source": name,
                "distance": route["distance"],
                "duration": route["duration"],
                "legs": [{"distance": leg["distance"], "duration": leg["duration"]} for leg in route.get("legs", [])],
                "geometry": [[c[1], c[0]] for c in route["geometry"]["coordinates"]],
            }
            with _route_cache_lock:
                _load_route_cache()[key] = result
                _save_route_cache()
            return result
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}")
    return {"ok": False, "error": "; ".join(errors) or "no router answered"}


@tracking_bp.post("/road-route")
@login_required
def road_route():
    pts = (request.get_json(silent=True) or {}).get("points") or []
    try:
        points = [(float(p[0]), float(p[1])) for p in pts][:25]
    except (TypeError, ValueError, IndexError):
        return jsonify({"ok": False, "error": "points must be [[lat, lon], ...]"}), 400
    if len(points) < 2 or not all(-90 <= a <= 90 and -180 <= b <= 180 for a, b in points):
        return jsonify({"ok": False, "error": "need at least 2 valid points"}), 400
    result = fetch_road_route(points)
    return jsonify(result), (200 if result.get("ok") else 502)
