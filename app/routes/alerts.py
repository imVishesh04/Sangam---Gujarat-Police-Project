"""
Alerts Blueprint — Module 6

Endpoints
---------
GET  /alerts/              — list alerts (filterable, paginated)
GET  /alerts/<alert_id>    — alert detail with enriched camera + watchlist info
"""
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import and_
from app import db
from app.models import Alert, Camera, Watchlist
from app.services.audit import log_audit_event

alerts_bp = Blueprint("alerts", __name__, url_prefix="/alerts")


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string to a UTC-aware datetime, or return None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


@alerts_bp.get("/")
@login_required
def list_alerts():
    """
    List alerts with optional filters.

    Query params
    ------------
    camera_id   : filter by source camera
    type        : watchlist type — "vehicle" or "person"
    from        : ISO-8601 start datetime (inclusive)
    to          : ISO-8601 end datetime (inclusive)
    page        : page number (default 1)
    per_page    : results per page (default 50, max 200)
    """
    camera_id = request.args.get("camera_id")
    wl_type = request.args.get("type")
    from_dt = _parse_iso(request.args.get("from"))
    to_dt = _parse_iso(request.args.get("to"))
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(200, max(1, int(request.args.get("per_page", 50))))

    filters = []
    if camera_id:
        filters.append(Alert.camera_id == camera_id)
    if from_dt:
        filters.append(Alert.timestamp >= from_dt)
    if to_dt:
        filters.append(Alert.timestamp <= to_dt)
    if wl_type:
        # Join to Watchlist to filter by type
        wl_ids = [w.watchlist_id for w in Watchlist.query.filter_by(type=wl_type).all()]
        filters.append(Alert.watchlist_id.in_(wl_ids))

    query = Alert.query.filter(and_(*filters)).order_by(Alert.timestamp.desc())
    total = query.count()
    alerts = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "alerts": [_enrich(a) for a in alerts],
    })


@alerts_bp.get("/<alert_id>")
@login_required
def get_alert(alert_id):
    """Return a single alert enriched with camera location and watchlist reason."""
    try:
        numeric_id = int(alert_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid alert_id format, expected integer"}), 400

    alert = Alert.query.get_or_404(numeric_id)
    return jsonify(_enrich(alert))


@alerts_bp.put("/<alert_id>/acknowledge")
@login_required
def acknowledge_alert(alert_id):
    """
    Acknowledge an alert and record officer notes / action taken.
    """
    try:
        numeric_id = int(alert_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid alert_id format, expected integer"}), 400

    alert = Alert.query.get_or_404(numeric_id)
    data = request.get_json(silent=True) or {}

    officer = data.get("acknowledged_by") or (
        current_user.username if getattr(current_user, "is_authenticated", False) else "Duty Officer"
    )
    notes = data.get("action_taken") or data.get("notes") or "Acknowledged by operator"

    alert.is_acknowledged = True
    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.acknowledged_by = officer
    alert.action_taken = notes
    db.session.commit()

    log_audit_event(
        action="ALERT_ACKNOWLEDGE",
        target_type="alert",
        target_id=alert.alert_id,
        details=f"officer={officer} action={notes} matched={alert.matched_value}",
    )

    return jsonify(_enrich(alert)), 200


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _enrich(alert: Alert) -> dict:
    """
    Attach camera location and watchlist reason to an alert dict.
    Avoids extra round-trips from the frontend.
    """
    data = alert.to_dict()
    cam = db.session.get(Camera, alert.camera_id)
    wl = db.session.get(Watchlist, alert.watchlist_id)
    data["location_name"] = cam.location_name if cam else None
    data["latitude"] = cam.latitude if cam else None
    data["longitude"] = cam.longitude if cam else None
    data["reason"] = wl.reason if wl else None
    data["watchlist_type"] = wl.type if wl else None
    try:
        from app.services.alert_agent import severity_for
        sev = severity_for(alert)
        data["severity"], data["severity_why"] = sev["level"], sev["why"]
    except Exception:  # noqa: BLE001 - never break the alert list over a label
        data["severity"], data["severity_why"] = "medium", "severity unavailable"
    return data
