"""
Matching Engine — Module 4

Runs on every new Detection.  If the plate number exists in the Watchlist,
an Alert row is created and a real-time SocketIO event is pushed to all
clients subscribed to the /alerts namespace.

The SocketIO payload is intentionally rich so the frontend can render a
complete alert card without a follow-up API call:
  {
    alert_id, camera_id, location_name, latitude, longitude,
    matched_value, reason, watchlist_type, timestamp
  }
"""
import logging
from app import db, socketio
from app.models import Detection, Watchlist, Alert, Camera

logger = logging.getLogger(__name__)


def check_and_alert(detection: Detection) -> Alert | None:
    """
    Compare detection.plate_number against Watchlist entries of type 'vehicle'.

    Steps
    -----
    1. Case-insensitive exact match against watchlist.value
    2. If matched → create Alert row
    3. Emit 'new_alert' on /alerts namespace with enriched payload
    4. Return the Alert (or None if no match)
    """
    match = Watchlist.query.filter(
        Watchlist.type == "vehicle",
        Watchlist.value.ilike(detection.plate_number),
    ).first()

    if not match:
        return None

    alert = Alert(
        watchlist_id=match.watchlist_id,
        camera_id=detection.camera_id,
        matched_value=detection.plate_number,
    )
    db.session.add(alert)
    db.session.commit()

    logger.warning(
        "WATCHLIST MATCH | plate=%s camera=%s reason=%s",
        detection.plate_number, detection.camera_id, match.reason,
    )

    # Build enriched payload — fetch camera location for map pin on frontend
    cam = db.session.get(Camera, detection.camera_id)
    payload = {
        **alert.to_dict(),
        "location_name": cam.location_name if cam else None,
        "latitude": cam.latitude if cam else None,
        "longitude": cam.longitude if cam else None,
        "reason": match.reason,
        "watchlist_type": match.type,
    }
    try:
        from app.services.alert_agent import severity_for
        sev = severity_for(alert)
        payload["severity"], payload["severity_why"] = sev["level"], sev["why"]
    except Exception as exc:  # noqa: BLE001
        logger.error("Severity failed for alert %s: %s", alert.alert_id, exc)

    socketio.emit("new_alert", payload, namespace="/alerts")

    # Hand off to the WhatsApp alert agent (background queue - never blocks
    # or breaks the detection pipeline).
    try:
        from app.services.alert_agent import notify_alert
        notify_alert(alert.alert_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Alert agent hand-off failed for alert %s: %s", alert.alert_id, exc)

    return alert
