"""
Notifications blueprint - status and test endpoints for the WhatsApp alert agent.

GET  /notifications/status   provider, masked recipients, sent/failed counters
GET  /notifications/recent   last composed WhatsApp messages (what was sent)
POST /notifications/test-alert  admin only. Creates a REAL alert from a manual test
                             sighting (watchlist plate at a camera) - same path as an ANPR hit,
                             so it shows on the dashboard AND goes to WhatsApp.
POST /notifications/test     admin only. {"mode": "ping"} sends a short test message,
                             {"mode": "latest_alert"} re-sends the newest alert in full.
"""
import os
import random
import re

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app import limiter
from app.services import alert_agent, whatsapp

notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@notifications_bp.get("/status")
@login_required
def notification_status():
    return jsonify(alert_agent.status())


@notifications_bp.get("/recent")
@login_required
def notification_recent():
    return jsonify(list(alert_agent.RECENT))


@notifications_bp.post("/test")
@login_required
@limiter.limit("5 per minute")
def notification_test():
    if current_user.role != "admin":
        return jsonify({"error": "Admin access required"}), 403
    mode = (request.get_json(silent=True) or {}).get("mode", "ping")

    if mode == "latest_alert":
        from app.models import Alert
        latest = Alert.query.order_by(Alert.alert_id.desc()).first()
        if latest is None:
            return jsonify({"error": "No alerts yet - trigger a watchlist match first"}), 404
        return jsonify(alert_agent.process_alert(latest.alert_id, force=True))

    to_list = whatsapp.recipients()
    if not to_list and not whatsapp.telegram_configured():
        return jsonify({"error": "WHATSAPP_TO is not set in .env"}), 400
    body = ("✅ *SANGAM test message*\n"
            "WhatsApp alerts are connected. Watchlist matches will arrive here "
            "with location, recent sightings and RTO details.")
    results = []
    for to in to_list:
        ok, info = whatsapp.send(to, body)
        results.append({"to": whatsapp.mask_number(to), "ok": ok, "info": info})
    if whatsapp.telegram_configured():
        ok, info = whatsapp.send_telegram(body)
        results.append({"to": "telegram", "ok": ok, "info": info})
    status = 200 if all(r["ok"] for r in results) else 502
    return jsonify({"provider": whatsapp.provider_name(), "results": results}), status


_PLATE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$")


@notifications_bp.post("/test-alert")
@login_required
@limiter.limit("10 per minute")
def notification_test_alert():
    if current_user.role != "admin":
        return jsonify({"error": "Admin access required"}), 403
    from datetime import datetime, timezone
    from app import db
    from app.models import Camera, Detection, Watchlist
    from app.services.matching_engine import check_and_alert

    data = request.get_json(silent=True) or {}
    cam_id = data.get("camera_id")
    if cam_id:
        camera = db.session.get(Camera, cam_id)
    else:
        located = Camera.query.filter(Camera.latitude.isnot(None)).all()
        camera = random.choice(located) if located else Camera.query.first()
    if camera is None:
        return jsonify({"error": "No cameras registered"}), 404

    plate = (data.get("plate") or os.environ.get("DEMO_ALERT_PLATE", "")).strip().upper()
    q = Watchlist.query.filter(Watchlist.type == "vehicle")
    if plate:
        wl = q.filter(Watchlist.value.ilike(plate)).first()
    else:
        wl = next((w for w in q.order_by(Watchlist.watchlist_id).all() if _PLATE_RE.match(w.value.upper())), None)
    if wl is None:
        return jsonify({"error": "No matching vehicle on the watchlist - add one first"}), 404

    # confidence=None marks a manual sighting (no OCR) - the WhatsApp message says so
    det = Detection(camera_id=camera.camera_id, plate_number=wl.value.upper(), confidence=None,
                    timestamp=datetime.now(timezone.utc))
    db.session.add(det)
    db.session.commit()
    alert = check_and_alert(det)
    if alert is None:
        return jsonify({"error": "Watchlist matching did not create an alert"}), 500
    return jsonify({"alert_id": alert.alert_id, "plate": wl.value.upper(),
                    "camera_id": camera.camera_id, "location_name": camera.location_name}), 201
