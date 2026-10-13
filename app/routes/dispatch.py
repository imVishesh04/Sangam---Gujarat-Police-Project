"""
Dispatch blueprint - nearest responding unit for an alert.

GET  /dispatch/units                  all units (sample registry + live updates)
GET  /dispatch/suggest/<alert_id>     best 3 units for this alert, with reasons
POST /dispatch/assign/<alert_id>      {"unit_id"} - acknowledges the alert as
                                      "Assigned to <unit>" and WhatsApps ONLY that unit
                                      (unit has no number yet -> control-room number)
POST /dispatch/units/<id>/location    {"lat","lon","status"} - live position / status
                                      (future: PCR van phone app or AVL GPS feed)
"""
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app import db, limiter
from app.services import alert_agent, dispatch, whatsapp
from app.services.audit import log_audit_event

dispatch_bp = Blueprint("dispatch", __name__, url_prefix="/dispatch")


def _admin():
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"error": "Admin access required"}), 403
    return None


@dispatch_bp.get("/units")
@login_required
def list_units():
    return jsonify({"units": dispatch.units(), "sample": True})


@dispatch_bp.get("/suggest/<int:alert_id>")
@login_required
def suggest(alert_id):
    ctx = alert_agent.build_context(alert_id)
    if ctx is None:
        return jsonify({"error": "alert not found"}), 404
    dsp = ctx.get("dispatch") or {"ok": False, "units": [], "error": "dispatch unavailable"}
    return jsonify({"alert_id": alert_id, "plate": ctx["plate"], "severity": ctx["severity"],
                    "severity_why": ctx["priority_why"], "location": ctx["location"], **dsp})


@dispatch_bp.post("/assign/<int:alert_id>")
@login_required
@limiter.limit("20 per minute")
def assign(alert_id):
    guard = _admin()
    if guard:
        return guard
    from app.models import Alert
    unit_id = (request.get_json(silent=True) or {}).get("unit_id", "")
    alert = db.session.get(Alert, alert_id)
    if alert is None:
        return jsonify({"error": "alert not found"}), 404
    ctx = alert_agent.build_context(alert_id)
    dsp = ctx.get("dispatch") or {}
    unit = next((u for u in dsp.get("units", []) if u["unit_id"] == unit_id), None)
    if unit is None:  # officer picked a unit outside the top 3 - still allowed
        raw = next((u for u in dispatch.units() if u["unit_id"] == unit_id), None)
        if raw is None:
            return jsonify({"error": "unknown unit"}), 400
        d = dispatch.km((ctx["lat"], ctx["lon"]), (raw["lat"], raw["lon"])) if ctx["lat"] is not None else None
        unit = {**raw, "distance_km": round(d, 1) if d is not None else None,
                "eta_min": dispatch.eta_minutes(d) if d is not None else None}

    officer = current_user.username
    body = dispatch.compose_dispatch(ctx, unit, officer)
    phone = dispatch.phone_for(unit_id)
    targets = [phone] if phone else whatsapp.recipients()
    routed = "unit" if phone else "control room (unit has no number in the registry yet)"
    if not targets:
        if whatsapp.provider_name() == "log":
            targets = ["+000000000000"]  # written to demo_output/whatsapp_outbox.txt
            routed = "outbox file only (WHATSAPP_PROVIDER=log)"
        else:
            routed = "nobody - set WHATSAPP_TO in .env"
    results = []
    for to in targets:
        ok, info = whatsapp.send(to, body)
        results.append({"to": whatsapp.mask_number(to), "ok": ok, "info": info})

    alert.is_acknowledged = True
    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.acknowledged_by = officer
    alert.action_taken = f"Assigned to {unit['name']} ({unit_id})"
    db.session.commit()
    log_audit_event("ALERT_DISPATCH", "alert", alert_id,
                    details=f"unit={unit_id} routed_to={routed} sent={sum(r['ok'] for r in results)}/{len(results)}")
    return jsonify({"alert_id": alert_id, "unit": unit, "routed_to": routed, "results": results,
                    "action_taken": alert.action_taken})


@dispatch_bp.post("/units/<unit_id>/location")
@login_required
def update_location(unit_id):
    guard = _admin()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    lat, lon = data.get("lat"), data.get("lon")
    try:
        if lat is not None:
            lat, lon = float(lat), float(lon)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "lat/lon invalid"}), 400
    unit = dispatch.update_unit(unit_id, lat, lon, data.get("status"))
    if unit is None:
        return jsonify({"error": "unknown unit"}), 404
    return jsonify(unit)
