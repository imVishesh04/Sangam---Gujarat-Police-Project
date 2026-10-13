"""
Audit Log API Blueprint
-----------------------
Endpoints:
  GET /audit-logs/ — Retrieve administrative audit trail (Admin only)
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app.models import AuditLog

audit_bp = Blueprint("audit", __name__, url_prefix="/audit-logs")


def _admin_required():
    if not current_user.is_authenticated or current_user.role != "admin":
        return jsonify({"error": "Admin access required"}), 403
    return None


@audit_bp.get("/")
@login_required
def list_audit_logs():
    """Retrieve audit logs with optional filtering and pagination."""
    guard = _admin_required()
    if guard:
        return guard

    action = request.args.get("action")
    target_type = request.args.get("target_type")
    username = request.args.get("username")

    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        limit = 50
        offset = 0

    query = AuditLog.query
    if action:
        query = query.filter_by(action=action)
    if target_type:
        query = query.filter_by(target_type=target_type)
    if username:
        query = query.filter_by(username=username)

    total = query.count()
    logs = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit).all()

    return jsonify({
        "total": total,
        "offset": offset,
        "limit": limit,
        "logs": [l.to_dict() for l in logs],
    })
