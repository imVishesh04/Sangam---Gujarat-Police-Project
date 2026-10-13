"""
SQLAlchemy ORM models for the CCTV Integration Platform.

Tables
------
User        — login accounts with role (admin / viewer)
Camera      — registered camera endpoints
Detection   — every ANPR hit logged by the pipeline
Watchlist   — vehicles / persons of interest
Alert       — generated when a detection matches the watchlist
"""
from datetime import datetime, timezone


def _iso(dt):
    """ISO-8601 with an explicit UTC offset. SQLite returns naive datetimes; without
    the offset browsers read them as LOCAL time (IST), so a brand-new alert showed
    up as "5h ago"."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()
from flask_login import UserMixin
from sqlalchemy import event
from sqlalchemy.types import TypeDecorator, Text
from geoalchemy2 import Geometry
from app import db, login_manager


# ---------------------------------------------------------------------------
# Cross-Platform Spatial Type (PostGIS on Postgres, Text on SQLite)
# ---------------------------------------------------------------------------

class SpatialPoint(TypeDecorator):
    """
    Cross-platform Point geometry type:
    - On PostgreSQL: compiles to PostGIS geometry(POINT, 4326)
    - On SQLite: compiles to Text() without failing on missing SpatiaLite extensions
    """
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Geometry("POINT", srid=4326))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return f"POINT({value[0]} {value[1]})"
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return str(value)


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    # role: "admin" | "viewer"
    role = db.Column(db.String(16), nullable=False, default="viewer")

    def to_dict(self):
        return {"id": self.id, "username": self.username, "role": self.role}


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

class Camera(db.Model):
    __tablename__ = "cameras"

    camera_id = db.Column(db.String(64), primary_key=True)
    department = db.Column(db.String(128), nullable=False)
    location_name = db.Column(db.String(256), nullable=False)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    geom = db.Column(SpatialPoint, nullable=True)
    vendor = db.Column(db.String(128), nullable=True)
    # stream_url: local file path OR rtsp:// URL — swappable without code changes
    stream_url = db.Column(db.String(512), nullable=False)
    # format: "rtsp" | "file" | "http"
    format = db.Column(db.String(32), nullable=False, default="rtsp")
    # status: "online" | "offline"
    status = db.Column(db.String(16), nullable=False, default="offline")
    last_heartbeat = db.Column(db.DateTime, nullable=True)

    detections = db.relationship("Detection", backref="camera", lazy=True)
    alerts = db.relationship("Alert", backref="camera", lazy=True)

    def to_dict(self):
        return {
            "camera_id": self.camera_id,
            "department": self.department,
            "location_name": self.location_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "geom": str(self.geom) if self.geom else None,
            "vendor": self.vendor,
            "stream_url": self.stream_url,
            "format": self.format,
            "status": self.status,
            "last_heartbeat": _iso(self.last_heartbeat),
            # True while the ANPR worker for this camera is running (even if
            # it has not received its first frame yet).
            "ingesting": _is_ingesting(self.camera_id),
        }


def _is_ingesting(camera_id) -> bool:
    try:
        from app.services.video_ingestion import running_camera_ids
        return camera_id in running_camera_ids()
    except Exception:  # noqa: BLE001
        return False


@event.listens_for(Camera, "before_insert")
@event.listens_for(Camera, "before_update")
def set_camera_geom(mapper, connection, target):
    """Automatically populate PostGIS geometry Point from lat/long."""
    if target.latitude is not None and target.longitude is not None:
        target.geom = f"POINT({target.longitude} {target.latitude})"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class Detection(db.Model):
    __tablename__ = "detections"

    detection_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    camera_id = db.Column(db.String(64), db.ForeignKey("cameras.camera_id"), nullable=False)
    plate_number = db.Column(db.String(32), nullable=False, index=True)
    confidence = db.Column(db.Float, nullable=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    def to_dict(self):
        return {
            "detection_id": self.detection_id,
            "camera_id": self.camera_id,
            "plate_number": self.plate_number,
            "confidence": self.confidence,
            "timestamp": _iso(self.timestamp),
        }


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

class Watchlist(db.Model):
    __tablename__ = "watchlist"

    watchlist_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # type: "vehicle" | "person"
    type = db.Column(db.String(16), nullable=False)
    value = db.Column(db.String(64), nullable=False, index=True)
    reason = db.Column(db.String(256), nullable=True)
    added_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    alerts = db.relationship("Alert", backref="watchlist_entry", cascade="all, delete-orphan", lazy=True)

    def to_dict(self):
        return {
            "watchlist_id": self.watchlist_id,
            "type": self.type,
            "value": self.value,
            "reason": self.reason,
            "added_date": _iso(self.added_date),
            # Same rule the alert engine uses (app/services/severity.py)
            **_watchlist_severity(self.reason),
        }


def _watchlist_severity(reason) -> dict:
    try:
        from app.services.severity import base_level
        level, why = base_level(reason or "")
        return {"severity": level, "severity_why": why}
    except Exception:  # noqa: BLE001
        return {"severity": "medium", "severity_why": ""}


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------

class Alert(db.Model):
    __tablename__ = "alerts"

    alert_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    watchlist_id = db.Column(db.Integer, db.ForeignKey("watchlist.watchlist_id"), nullable=False)
    camera_id = db.Column(db.String(64), db.ForeignKey("cameras.camera_id"), nullable=False)
    matched_value = db.Column(db.String(64), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    is_acknowledged = db.Column(db.Boolean, nullable=False, default=False)
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    acknowledged_by = db.Column(db.String(64), nullable=True)
    action_taken = db.Column(db.String(256), nullable=True)

    def to_dict(self):
        return {
            "alert_id": self.alert_id,
            "id": self.alert_id,
            "watchlist_id": self.watchlist_id,
            "camera_id": self.camera_id,
            "matched_value": self.matched_value,
            "timestamp": _iso(self.timestamp),
            "is_acknowledged": self.is_acknowledged,
            "acknowledged_at": _iso(self.acknowledged_at),
            "acknowledged_by": self.acknowledged_by,
            "action_taken": self.action_taken,
        }


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(64), nullable=True)
    role = db.Column(db.String(16), nullable=True)
    action = db.Column(db.String(64), nullable=False, index=True)
    target_type = db.Column(db.String(64), nullable=True)
    target_id = db.Column(db.String(128), nullable=True)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": _iso(self.timestamp),
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "details": self.details,
            "ip_address": self.ip_address,
        }
