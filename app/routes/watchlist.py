"""
Watchlist Blueprint — Module 4

Endpoints
---------
GET    /watchlist/              — list all entries (filterable by ?type=)
POST   /watchlist/              — add entry (Admin)
GET    /watchlist/<id>          — single entry
PUT    /watchlist/<id>          — update entry (Admin)
DELETE /watchlist/<id>          — remove entry (Admin)
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models import Watchlist
from app.services.audit import log_audit_event

watchlist_bp = Blueprint("watchlist", __name__, url_prefix="/watchlist")


def _admin_required():
    if not current_user.is_authenticated or current_user.role != "admin":
        return jsonify({"error": "Admin access required"}), 403
    return None


@watchlist_bp.get("/")
@login_required
def list_watchlist():
    """List watchlist entries. Filter by ?type=vehicle or ?type=person."""
    wl_type = request.args.get("type")
    query = Watchlist.query
    if wl_type:
        query = query.filter_by(type=wl_type)
    return jsonify([w.to_dict() for w in query.order_by(Watchlist.added_date.desc()).all()])


@watchlist_bp.post("/")
@login_required
def add_entry():
    """Add a new watchlist entry. Requires Admin role."""
    guard = _admin_required()
    if guard:
        return guard

    data = request.get_json(force=True)
    if not data.get("value"):
        return jsonify({"error": "value is required"}), 400

    entry = Watchlist(
        type=data.get("type", "vehicle"),
        value=data["value"].strip().upper(),
        reason=data.get("reason"),
    )
    db.session.add(entry)
    db.session.commit()
    log_audit_event(
        action="WATCHLIST_ADD",
        target_type="watchlist",
        target_id=entry.watchlist_id,
        details=f"value={entry.value} type={entry.type} reason={entry.reason or ''}",
    )
    return jsonify(entry.to_dict()), 201


@watchlist_bp.get("/<int:watchlist_id>")
@login_required
def get_entry(watchlist_id):
    """Return a single watchlist entry."""
    return jsonify(Watchlist.query.get_or_404(watchlist_id).to_dict())


@watchlist_bp.put("/<int:watchlist_id>")
@login_required
def update_entry(watchlist_id):
    """Update reason or type on an existing watchlist entry. Requires Admin."""
    guard = _admin_required()
    if guard:
        return guard

    entry = Watchlist.query.get_or_404(watchlist_id)
    data = request.get_json(force=True)
    for field in ("type", "value", "reason"):
        if field in data:
            val = data[field]
            setattr(entry, field, val.strip().upper() if field == "value" else val)
    db.session.commit()
    log_audit_event(
        action="WATCHLIST_UPDATE",
        target_type="watchlist",
        target_id=entry.watchlist_id,
        details=f"value={entry.value} type={entry.type} reason={entry.reason or ''}",
    )
    return jsonify(entry.to_dict())


@watchlist_bp.delete("/<int:watchlist_id>")
@login_required
def remove_entry(watchlist_id):
    """Remove a watchlist entry. Requires Admin."""
    guard = _admin_required()
    if guard:
        return guard

    entry = Watchlist.query.get_or_404(watchlist_id)
    val = entry.value
    db.session.delete(entry)
    db.session.commit()
    log_audit_event(
        action="WATCHLIST_DELETE",
        target_type="watchlist",
        target_id=watchlist_id,
        details=f"deleted value={val}",
    )
    return jsonify({"message": f"Watchlist entry {watchlist_id} deleted"})
