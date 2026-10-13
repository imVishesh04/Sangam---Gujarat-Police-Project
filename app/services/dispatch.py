"""
Dispatch agent - picks the responding unit for an alert.

Input : the alert's last camera position, its severity and the recent sightings.
Output: the best 3 units (PCR van / QRT / traffic point / police station) with
        distance, ETA and a plain-language reason, plus the local police station.

How it decides (explainable, no black box):
1. Which unit TYPES fit the severity (PLAN below) - a parking-fine plate does not
   pull a QRT; a kidnapping suspect does.
2. Only units with status "available" (a PCR van that is busy is skipped).
3. Where the vehicle is GOING: if the last two sightings are at different cameras
   within an hour, the heading and speed give a point ~10 minutes ahead (max 5 km).
   Units are ranked by distance to that point, so the van ahead of the vehicle wins
   over the one behind it.
4. A small penalty per step down the type list keeps the preferred type first
   when distances are close.

Only ONE unit is messaged (plus the station for HIGH/CRITICAL), instead of the
whole district WhatsApp group - that is the point.

Units come from data/response_units.json (sample). Positions can be updated live
via POST /dispatch/units/<id>/location (future: PCR van phone app / AVL GPS).
"""
from __future__ import annotations

import copy
import json
import math
import os
import threading
from datetime import timedelta

_DATA = os.path.join(os.path.dirname(__file__), "data", "response_units.json")
_lock = threading.Lock()
_units: list[dict] | None = None

PLAN = {
    "critical": ["pcr", "qrt", "station"],
    "high": ["pcr", "station"],
    "medium": ["pcr", "traffic", "station"],
    "low": ["traffic", "station"],
}
TYPE_LABEL = {"pcr": "PCR van", "qrt": "Quick Response Team", "traffic": "Traffic police", "station": "Police station"}
TYPE_PENALTY_KM = 1.5   # used when intercepting a moving vehicle
TIE_PENALTY_KM = 0.3    # otherwise: nearest wins, type only breaks near-ties
FAR_KM = 50


def km(a, b) -> float:
    r = 6371.0
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def eta_minutes(distance_km: float) -> int:
    """Road-ish ETA: 1.4x straight line, ~35 km/h in a city, ~55 km/h outside."""
    road = distance_km * 1.4
    speed = 35 if distance_km < 15 else 55
    return max(1, round(road / speed * 60))


def units() -> list[dict]:
    global _units
    with _lock:
        if _units is None:
            with open(_DATA, encoding="utf-8") as fh:
                _units = json.load(fh)["units"]
        return copy.deepcopy(_units)


def update_unit(unit_id: str, lat=None, lon=None, status=None) -> dict | None:
    units()  # make sure loaded
    with _lock:
        for u in _units:
            if u["unit_id"] == unit_id:
                if lat is not None and lon is not None:
                    u["lat"], u["lon"] = float(lat), float(lon)
                if status in ("available", "busy", "off-duty"):
                    u["status"] = status
                return copy.deepcopy(u)
    return None


def _compass(dlat: float, dlon: float) -> str:
    deg = (math.degrees(math.atan2(dlon, dlat)) + 360) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][round(deg / 45) % 8]


def predict(sightings: list[dict]) -> dict | None:
    """sightings newest first: {time, lat, lon, camera_id}. Point ~10 min ahead."""
    pts = [s for s in sightings if s.get("lat") is not None and s.get("lon") is not None]
    if len(pts) < 2:
        return None
    new, old = pts[0], next((p for p in pts[1:] if p["camera_id"] != pts[0]["camera_id"]), None)
    if old is None:
        return None
    gap = new["time"] - old["time"]
    if gap <= timedelta(0) or gap > timedelta(hours=1):
        return None
    dist = km((old["lat"], old["lon"]), (new["lat"], new["lon"]))
    if dist < 0.3:
        return None
    speed = dist / (gap.total_seconds() / 3600)
    if speed > float(os.environ.get("CLONE_SPEED_KMH", "150")):
        return None  # impossible jump (likely a cloned plate) - no trustworthy heading
    ahead = min(speed * (10 / 60), 5.0)
    f = ahead / dist
    return {
        "lat": new["lat"] + (new["lat"] - old["lat"]) * f,
        "lon": new["lon"] + (new["lon"] - old["lon"]) * f,
        "heading": _compass(new["lat"] - old["lat"], new["lon"] - old["lon"]),
        "speed_kmh": round(speed),
        "ahead_km": round(ahead, 1),
    }


def suggest(lat, lon, level: str = "medium", sightings: list[dict] | None = None,
            unit_list: list[dict] | None = None, k: int = 3) -> dict:
    if lat is None or lon is None:
        return {"ok": False, "error": "last camera has no GPS position", "units": []}
    level = level if level in PLAN else "medium"
    plan = PLAN[level]
    pool = unit_list if unit_list is not None else units()
    pred = predict(sightings or [])
    target = (pred["lat"], pred["lon"]) if pred else (lat, lon)

    ranked = []
    for u in pool:
        if u.get("status") != "available" or u.get("type") not in plan:
            continue
        d_now = km((lat, lon), (u["lat"], u["lon"]))
        d_target = km(target, (u["lat"], u["lon"]))
        # Vehicle moving (prediction available): intercept it - rank by distance
        # to where it is heading, preferring mobile units (PCR) over a station.
        # Vehicle not moving / one sighting: the NEAREST unit is best; unit type
        # only breaks near-ties, so "BEST" is never visibly farther than others.
        if pred:
            score = d_target + plan.index(u["type"]) * TYPE_PENALTY_KM
        else:
            score = d_now + plan.index(u["type"]) * TIE_PENALTY_KM
        ranked.append((score, d_now, d_target, u))
    ranked.sort(key=lambda r: r[0])
    # Do not list units from another district (e.g. 247 km away) when a
    # nearby one exists; only fall back to far units if nothing is close.
    near = [r for r in ranked if r[1] <= FAR_KM]
    if near:
        ranked = near

    out = []
    for i, (score, d_now, d_target, u) in enumerate(ranked[:k]):
        why = [f"{TYPE_LABEL[u['type']]}, {d_now:.1f} km from last camera"]
        if pred:
            why.append("ahead of the vehicle" if d_target < d_now else "behind the vehicle")
        if i == 0:
            why.insert(0, "best to intercept the moving vehicle" if pred else "nearest available unit")
        out.append({**{key: u[key] for key in ("unit_id", "name", "type", "area", "lat", "lon")},
                    "type_label": TYPE_LABEL[u["type"]], "has_phone": bool(u.get("phone")),
                    "distance_km": round(d_now, 1), "eta_min": eta_minutes(d_now),
                    "far": d_now > FAR_KM, "why": "; ".join(why)})

    station = None
    if level in ("high", "critical"):
        stations = sorted((km((lat, lon), (u["lat"], u["lon"])), u) for u in pool
                          if u.get("type") == "station" and u.get("status") != "off-duty")
        if stations:
            d, u = stations[0]
            station = {"unit_id": u["unit_id"], "name": u["name"], "distance_km": round(d, 1),
                       "has_phone": bool(u.get("phone"))}

    return {"ok": bool(out), "level": level, "plan": plan, "prediction": pred, "units": out,
            "station": station,
            "error": None if out else "no available unit of a fitting type",
            "note": ("No unit within 50 km - inform the district control room."
                     if out and out[0]["far"] else None)}


def phone_for(unit_id: str) -> str:
    for u in units():
        if u["unit_id"] == unit_id:
            return (u.get("phone") or "").strip()
    return ""


def compose_dispatch(ctx: dict, unit: dict, assigned_by: str) -> str:
    L = [f"🚓 *SANGAM dispatch* - Alert #{ctx['alert_id']}",
         f"*Unit:* {unit['name']} ({unit['unit_id']})",
         f"*Priority:* {ctx['priority']}", "",
         f"*Vehicle:* {ctx['plate']}",
         f"*Reason:* {ctx['reason']}",
         f"*Last seen:* {ctx['location']} ({ctx['camera_id']})"]
    if ctx.get("lat") is not None:
        L.append(f"*Go to:* https://maps.google.com/?q={ctx['lat']:.5f},{ctx['lon']:.5f}")
    if unit.get("distance_km") is not None:
        L.append(f"*Distance:* {unit['distance_km']} km · ~{unit.get('eta_min', '?')} min")
    pred = ctx.get("dispatch", {}).get("prediction") if ctx.get("dispatch") else None
    if pred:
        L.append(f"*Heading:* {pred['heading']} at ~{pred['speed_kmh']} km/h")
    L += ["", f"Assigned by {assigned_by} from SANGAM. Confirm on arrival."]
    return "\n".join(L)
