"""
Audit Logging Service
---------------------
Records tamper-evident operational and administrative audit logs.
"""
import logging
from flask import request, has_request_context
from flask_login import current_user
from app import db
from app.models import AuditLog

logger = logging.getLogger("sangam.audit")


def log_audit_event(
    action: str,
    target_type: str = None,
    target_id: str = None,
    details: str = None,
    user_id: int = None,
    username: str = None,
    role: str = None,
    ip_address: str = None,
) -> AuditLog:
    """Record an audit trail event in database and application logs."""
    if has_request_context():
        if ip_address is None:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                ip_address = forwarded.split(",")[0].strip()
            else:
                ip_address = request.remote_addr

        if current_user and current_user.is_authenticated:
            user_id = user_id or current_user.id
            username = username or current_user.username
            role = role or current_user.role

    entry = AuditLog(
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        details=details,
        user_id=user_id,
        username=username,
        role=role,
        ip_address=ip_address,
    )
    try:
        db.session.add(entry)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to write audit log to database: %s", exc)

    logger.info(
        "AUDIT: action=%s user=%s(%s) target=%s:%s ip=%s details=%s",
        action,
        username or "anonymous",
        role or "none",
        target_type or "-",
        target_id or "-",
        ip_address or "-",
        details or "",
    )
    return entry
