"""
SANGAM Alert Agent - pushes watchlist alerts to WhatsApp so officers who are
not watching the dashboard still get them.

For every new watchlist Alert the agent runs a small, deterministic loop:

  1. PERCEIVE  - receives the alert id from the matching engine (non-blocking queue,
                 so a slow network never delays the ANPR pipeline)
  2. ENRICH    - camera location + Google Maps link, recent sightings of the plate,
                 RTO decode (state / RTO office) and registry record
  3. REASON    - cloned-plate check (two sightings too far apart for the time
                 between them), auto priority from the watchlist reason, and
                 duplicate / flood control
  4. ACT       - sends the WhatsApp message (retries with back-off) and writes
                 an audit-log entry for every send / failure / skip

The reasoning is rule-based on purpose: every line of a police alert must be
traceable to stored data, so no free-text generation is used for facts.

Env: ALERT_AGENT_ENABLED (1), ALERT_AGENT_DEDUPE_S (300),
     ALERT_AGENT_MAX_PER_MIN (10), CLONE_SPEED_KMH (150), DASHBOARD_PUBLIC_URL
"""
from __future__ import annotations

import logging
import math
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from app.services import rto_lookup, whatsapp

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
HIGH_PRIORITY_WORDS = ("stolen", "robbery", "murder", "kidnap", "abduct", "terror", "ats",
                       "weapon", "arms", "narcotic", "drug", "hit-and-run", "hit and run",
                       "extortion", "smuggl", "wanted", "missing")

_queue: "queue.Queue[int]" = queue.Queue(maxsize=500)
_worker: threading.Thread | None = None
_app = None
_lock = threading.Lock()
_recent_keys: dict[tuple[str, str], float] = {}
_send_times: deque = deque()
RECENT: deque = deque(maxlen=20)          # last composed messages (for dashboard / demo)
STATS = {"sent": 0, "failed": 0, "skipped": 0}


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return os.environ.get("ALERT_AGENT_ENABLED", "1") == "1"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_ist(dt: datetime, with_date=True) -> str:
    d = _aware(dt).astimezone(IST)
    return d.strftime("%d %b %Y, %H:%M:%S IST") if with_date else d.strftime("%H:%M IST")


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def auto_priority(reason: str) -> tuple[str, str]:
    """Base level from the watchlist reason only (see services/severity.py)."""
    from app.services import severity
    level, why = severity.base_level(reason)
    return level.upper(), why


def clone_check(sightings: list[dict]) -> dict | None:
    """
    sightings: newest first, each {time, lat, lon, camera_id, location}.
    Flags consecutive sightings whose implied speed is physically implausible.
    """
    limit = _env_int("CLONE_SPEED_KMH", 150)
    for newer, older in zip(sightings, sightings[1:]):
        if newer["camera_id"] == older["camera_id"]:
            continue
        if None in (newer["lat"], newer["lon"], older["lat"], older["lon"]):
            continue
        km = haversine_km(older["lat"], older["lon"], newer["lat"], newer["lon"])
        if km < 2:
            continue
        hours = max((newer["time"] - older["time"]).total_seconds(), 1) / 3600
        speed = km / hours
        if speed > limit:
            return {"km": km, "minutes": hours * 60, "speed": speed,
                    "a": older["location"], "b": newer["location"]}
    return None


# ---------------------------------------------------------------------------
# enrich + compose
# ---------------------------------------------------------------------------

def build_context(alert_id: int) -> dict | None:
    from app import db
    from app.models import Alert, Camera, Detection, Watchlist

    alert = db.session.get(Alert, alert_id)
    if alert is None:
        return None
    wl = db.session.get(Watchlist, alert.watchlist_id)
    cam = db.session.get(Camera, alert.camera_id)

    dets = (Detection.query.filter(Detection.plate_number.ilike(alert.matched_value))
            .order_by(Detection.timestamp.desc()).limit(5).all())
    cams = {c.camera_id: c for c in Camera.query.filter(
        Camera.camera_id.in_({d.camera_id for d in dets} | {alert.camera_id})).all()}
    sightings = []
    for d in dets:
        c = cams.get(d.camera_id)
        sightings.append({
            "time": _aware(d.timestamp), "camera_id": d.camera_id,
            "location": (c.location_name if c else d.camera_id),
            "lat": c.latitude if c else None, "lon": c.longitude if c else None,
            "confidence": d.confidence,
        })

    reason = wl.reason if wl else ""
    from app.services import dispatch, severity
    clone = clone_check(sightings)
    sev = severity.classify(reason, sightings, clone)
    priority, why = sev["level"].upper(), sev["why"]
    lat = cam.latitude if cam else None
    lon = cam.longitude if cam else None
    try:
        dsp = dispatch.suggest(lat, lon, sev["level"], sightings)
    except Exception as exc:  # noqa: BLE001 - dispatch must never block the alert
        logger.error("Dispatch suggestion failed for alert %s: %s", alert_id, exc)
        dsp = None

    return {
        "alert_id": alert.alert_id,
        "plate": alert.matched_value.upper(),
        "reason": reason or "-",
        "time": _aware(alert.timestamp),
        "camera_id": alert.camera_id,
        "location": cam.location_name if cam else alert.camera_id,
        "city": getattr(cam, "city", None) if cam else None,
        "lat": cam.latitude if cam else None,
        "lon": cam.longitude if cam else None,
        "confidence": sightings[0]["confidence"] if sightings else None,
        "sightings": sightings,
        "priority": priority, "priority_why": why,
        "severity": priority.lower(),
        "dispatch": dsp,
        "clone": clone,
        "rto": rto_lookup.lookup(alert.matched_value),
    }


def severity_for(alert) -> dict:
    """Same LOW/MEDIUM/HIGH/CRITICAL the WhatsApp message uses, for the dashboard."""
    from app import db
    from app.models import Camera, Detection, Watchlist
    from app.services import severity
    wl = db.session.get(Watchlist, alert.watchlist_id)
    dets = (Detection.query.filter(Detection.plate_number.ilike(alert.matched_value))
            .order_by(Detection.timestamp.desc()).limit(5).all())
    cams = {c.camera_id: c for c in Camera.query.filter(
        Camera.camera_id.in_({d.camera_id for d in dets})).all()} if dets else {}
    sightings = [{"time": _aware(d.timestamp), "camera_id": d.camera_id,
                  "location": cams[d.camera_id].location_name if d.camera_id in cams else d.camera_id,
                  "lat": cams[d.camera_id].latitude if d.camera_id in cams else None,
                  "lon": cams[d.camera_id].longitude if d.camera_id in cams else None} for d in dets]
    return severity.classify(wl.reason if wl else "", sightings, clone_check(sightings))


def compose(ctx: dict) -> str:
    L = [f"🚨 *SANGAM Watchlist Alert* #{ctx['alert_id']}",
         f"*Priority:* {ctx['priority']} ({ctx['priority_why']})", "",
         f"*Vehicle:* {ctx['plate']}",
         f"*Reason:* {ctx['reason']}",
         f"*Last seen:* {ctx['location']} ({ctx['camera_id']})",
         f"*Time:* {fmt_ist(ctx['time'])}"]
    if ctx["lat"] is not None and ctx["lon"] is not None:
        L.append(f"*Map (camera registry GPS):* https://maps.google.com/?q={ctx['lat']:.5f},{ctx['lon']:.5f}")
    if ctx.get("confidence") is not None:
        conf = ctx["confidence"] * 100 if ctx["confidence"] <= 1 else ctx["confidence"]
        L.append(f"*OCR confidence:* {conf:.0f}%")
    else:
        L.append("*Sighting:* manual test entry (not an ANPR read)")

    if len(ctx["sightings"]) > 1:
        L += ["", "*Recent sightings (latest first):*"]
        for i, s in enumerate(ctx["sightings"][:4], 1):
            L.append(f"{i}. {fmt_ist(s['time'], with_date=False)} · {s['location']} ({s['camera_id']})")
    if ctx["clone"]:
        c = ctx["clone"]
        L.append(f"⚠️ *Possible cloned plate:* seen at {c['a']} and {c['b']}, "
                 f"{c['km']:.0f} km apart in {c['minutes']:.0f} min (~{c['speed']:.0f} km/h).")

    dsp = ctx.get("dispatch")
    if dsp and dsp.get("units"):
        top = dsp["units"][0]
        L += ["", f"*Nearest unit (suggested):* {top['name']} · {top['distance_km']} km · ~{top['eta_min']} min"]
        if dsp.get("station"):
            st = dsp["station"]
            L.append(f"*Local station:* {st['name']} · {st['distance_km']} km")
        if dsp.get("prediction"):
            p = dsp["prediction"]
            L.append(f"*Heading:* {p['heading']} at ~{p['speed_kmh']} km/h")
        if dsp.get("note"):
            L.append(f"_{dsp['note']}_")

    rto = ctx["rto"]
    d = rto.get("decode")
    L.append("")
    if d:
        office = f"{d['rto_code']} {d['rto_office']} RTO" if d.get("rto_office") else f"RTO code {d['rto_code']}"
        L.append(f"*Registered in:* {d['state']} · {office}")
    rec = rto.get("record")
    if rec:
        parts = [rec.get(k) for k in ("make", "model", "colour", "fuel") if rec.get(k)]
        L.append(f"*Vehicle record:* {' · '.join(str(p) for p in parts)}")
        extra = []
        if rec.get("vehicle_class"):
            extra.append(rec["vehicle_class"])
        if rec.get("registration_year"):
            extra.append(f"Reg. {rec['registration_year']}")
        if rec.get("rc_status"):
            extra.append(f"RC {rec['rc_status']}")
        if extra:
            L.append("   " + " · ".join(extra))
        if rec.get("insurance_valid_till"):
            L.append(f"   Insurance till {rec['insurance_valid_till']} · PUC till {rec.get('puc_valid_till', '-')}")
        if rec.get("owner_masked"):
            L.append(f"   Owner: {rec['owner_masked']} (masked)")
    L.append(f"_Source: {rto.get('source')}_")

    url = os.environ.get("DASHBOARD_PUBLIC_URL", "").strip()
    L += ["", f"Open in SANGAM: {url}" if url else "Act from the SANGAM dashboard. Reply is not monitored."]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# act
# ---------------------------------------------------------------------------

def _should_skip(plate: str, camera_id: str) -> str | None:
    now = time.monotonic()
    dedupe_s = _env_int("ALERT_AGENT_DEDUPE_S", 300)
    max_per_min = _env_int("ALERT_AGENT_MAX_PER_MIN", 10)
    with _lock:
        last = _recent_keys.get((plate, camera_id))
        if last is not None and now - last < dedupe_s:
            return f"duplicate (same plate + camera within {dedupe_s}s)"
        while _send_times and now - _send_times[0] > 60:
            _send_times.popleft()
        if len(_send_times) >= max_per_min:
            return f"flood control ({max_per_min}/min)"
        _recent_keys[(plate, camera_id)] = now
        _send_times.append(now)
    return None


def process_alert(alert_id: int, force: bool = False) -> dict:
    """Synchronous enrich -> compose -> send. Needs an app context."""
    from app.services.audit import log_audit_event

    ctx = build_context(alert_id)
    if ctx is None:
        return {"status": "missing", "alert_id": alert_id}
    if not force:
        skip = _should_skip(ctx["plate"], ctx["camera_id"])
        if skip:
            STATS["skipped"] += 1
            log_audit_event("WHATSAPP_SKIPPED", "alert", alert_id, details=skip, username="alert-agent")
            return {"status": "skipped", "reason": skip, "alert_id": alert_id}

    body = compose(ctx)
    to_list = whatsapp.recipients()
    if not to_list:
        if whatsapp.provider_name() == "log" or whatsapp.telegram_configured():
            to_list = ["+000000000000"] if whatsapp.provider_name() == "log" else []
        else:
            STATS["failed"] += 1
            log_audit_event("WHATSAPP_FAILED", "alert", alert_id, details="WHATSAPP_TO not set", username="alert-agent")
            return {"status": "failed", "reason": "WHATSAPP_TO not set", "alert_id": alert_id}

    results = []
    for to in to_list:
        ok, info = False, ""
        for attempt in range(3):
            ok, info = whatsapp.send(to, body)
            if ok or whatsapp.is_permanent_error(info):
                break
            time.sleep(2 ** attempt)
        results.append({"to": whatsapp.mask_number(to), "ok": ok, "info": info})
        STATS["sent" if ok else "failed"] += 1
        log_audit_event("WHATSAPP_SENT" if ok else "WHATSAPP_FAILED", "alert", alert_id,
                        details=f"{whatsapp.provider_name()} -> {whatsapp.mask_number(to)}: {info}",
                        username="alert-agent")

    if whatsapp.telegram_configured():
        ok, info = whatsapp.send_telegram(body)
        results.append({"to": "telegram", "ok": ok, "info": info})
        STATS["sent" if ok else "failed"] += 1
        log_audit_event("TELEGRAM_SENT" if ok else "TELEGRAM_FAILED", "alert", alert_id,
                        details=info, username="alert-agent")

    entry = {"alert_id": alert_id, "plate": ctx["plate"], "priority": ctx["priority"],
             "time": datetime.now(IST).strftime("%H:%M:%S"), "provider": whatsapp.provider_name(),
             "results": results, "body": body}
    RECENT.appendleft(entry)
    return {"status": "sent" if all(r["ok"] for r in results) else "partial", **entry}


def _run():
    while True:
        alert_id = _queue.get()
        try:
            with _app.app_context():
                res = process_alert(alert_id)
                logger.info("Alert agent | alert=%s status=%s", alert_id, res.get("status"))
        except Exception as exc:  # noqa: BLE001 - the worker must survive anything
            logger.error("Alert agent error | alert=%s | %s", alert_id, exc)
        finally:
            _queue.task_done()


def notify_alert(alert_id: int) -> None:
    """Called by the matching engine. Returns immediately."""
    global _worker, _app
    if not enabled():
        return
    from flask import current_app
    with _lock:
        if _app is None:
            _app = current_app._get_current_object()
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="alert-agent", daemon=True)
            _worker.start()
    try:
        _queue.put_nowait(alert_id)
    except queue.Full:
        logger.error("Alert agent queue full - dropping alert %s", alert_id)


def status() -> dict:
    return {
        "enabled": enabled(),
        "provider": whatsapp.provider_name(),
        "recipients": [whatsapp.mask_number(r) for r in whatsapp.recipients()],
        "rto_provider": os.environ.get("RTO_PROVIDER", "sample"),
        "telegram": whatsapp.telegram_configured(),
        "queue": _queue.qsize(),
        **STATS,
    }
