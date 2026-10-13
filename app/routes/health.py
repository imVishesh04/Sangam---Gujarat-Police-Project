"""
Health Blueprint
----------------
GET /health — system status snapshot for monitoring dashboards.

Returns:
  - Camera online/offline counts (from DB)
  - Live ingestion thread statuses (from in-memory CAMERA_STATE)
  - Today's detection and alert totals
  - Server UTC timestamp

No authentication required — allows load-balancers and uptime monitors
to poll without credentials.
"""
from datetime import datetime, timezone
from flask import Blueprint, jsonify, current_app
from app.models import Camera, Detection, Alert, Watchlist

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    online_db = Camera.query.filter_by(status="online").count()
    offline_db = Camera.query.filter_by(status="offline").count()
    detections_today = Detection.query.filter(Detection.timestamp >= today_start).count()
    alerts_today = Alert.query.filter(Alert.timestamp >= today_start).count()

    # Live thread statuses — may differ from DB if a camera just went offline
    try:
        from app.services.video_ingestion import get_all_statuses
        live_statuses = get_all_statuses()
        live_online = sum(1 for s in live_statuses.values() if s == "online")
        live_offline = len(live_statuses) - live_online
        # With AUTO_START_CAMERAS disabled, no worker exists until an operator
        # starts a feed. Report the DB registry instead of showing 0/0.
        if not live_statuses:
            live_online, live_offline = online_db, offline_db
    except Exception:
        live_statuses = {}
        live_online = live_offline = 0

    db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    db_engine = "PostgreSQL + PostGIS" if "postgres" in db_uri else "SQLite 3 (Local Core DB)"

    return jsonify({
        "status": "ok",
        "database_engine": db_engine,
        "cameras": {
            "db_online": online_db,
            "db_offline": offline_db,
            "db_total": online_db + offline_db,
            "live_online": live_online,
            "live_offline": live_offline,
            "live_statuses": live_statuses,
        },
        "detections_today": detections_today,
        "alerts_today": alerts_today,
        # The current Watchlist schema has no status field; every row is active
        # until it is deleted or matched by a separate workflow.
        "active_watchlists": Watchlist.query.count(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
