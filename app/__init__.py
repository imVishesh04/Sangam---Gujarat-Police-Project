"""
Application factory.
All extensions are initialised here and blueprints are registered.
"""
from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_socketio import SocketIO
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
login_manager = LoginManager()
socketio = SocketIO()
bcrypt = Bcrypt()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per minute", "30 per second"],
    storage_uri="memory://",
    strategy="fixed-window",
    headers_enabled=True,
)


@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({"error": "Not authenticated"}), 401

# Frontend dev server origin (Vite). REST CORS and SocketIO CORS are
# configured independently — both are required for the frontend to work.
FRONTEND_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_object)

    # Initialise extensions
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    CORS(app, supports_credentials=True, origins=FRONTEND_ORIGINS)
    limiter.enabled = app.config.get("RATELIMIT_ENABLED", True)
    limiter.init_app(app)
    socketio.init_app(app, cors_allowed_origins=FRONTEND_ORIGINS)  # was "*": any website could subscribe

    # Security Headers
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' 'unsafe-inline' http://localhost:* ws://localhost:* https://live.corp8.cloud https://cctv.corp8.cloud; "
            "img-src 'self' data: blob: http: https:; "
            "media-src 'self' blob: http: https:; "
            "connect-src 'self' http://localhost:* ws://localhost:* https://live.corp8.cloud https://cctv.corp8.cloud;"
        )
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify({
            "error": "Too Many Requests",
            "message": f"Rate limit exceeded: {e.description}",
        }), 429

    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.cameras import cameras_bp
    from app.routes.detections import detections_bp
    from app.routes.watchlist import watchlist_bp
    from app.routes.alerts import alerts_bp
    from app.routes.tracking import tracking_bp
    from app.routes.health import health_bp
    from app.routes.audit import audit_bp
    from app.routes.notifications import notifications_bp
    from app.routes.dispatch import dispatch_bp

    for bp in (auth_bp, cameras_bp, detections_bp, watchlist_bp, alerts_bp, tracking_bp, health_bp, audit_bp,
               notifications_bp, dispatch_bp):
        app.register_blueprint(bp)

    # Import socket event handlers so they are registered
    from app.sockets import events  # noqa: F401

    with app.app_context():
        db.create_all()
        # Ensure Alert and Camera schemas have updated columns (backwards compatibility)
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            if "alerts" in inspector.get_table_names():
                existing_cols = {col["name"] for col in inspector.get_columns("alerts")}
                migrations = [
                    ("is_acknowledged", "ALTER TABLE alerts ADD COLUMN is_acknowledged BOOLEAN NOT NULL DEFAULT 0"),
                    ("acknowledged_at", "ALTER TABLE alerts ADD COLUMN acknowledged_at DATETIME"),
                    ("acknowledged_by", "ALTER TABLE alerts ADD COLUMN acknowledged_by VARCHAR(64)"),
                    ("action_taken", "ALTER TABLE alerts ADD COLUMN action_taken VARCHAR(256)"),
                ]
                with db.engine.connect() as conn:
                    for col_name, sql in migrations:
                        if col_name not in existing_cols:
                            conn.execute(text(sql))
                    conn.commit()

            if "cameras" in inspector.get_table_names():
                existing_cam_cols = {col["name"] for col in inspector.get_columns("cameras")}
                with db.engine.connect() as conn:
                    if "geom" not in existing_cam_cols:
                        if db.engine.dialect.name == "postgresql":
                            conn.execute(text("ALTER TABLE cameras ADD COLUMN geom geometry(Point, 4326)"))
                        else:
                            conn.execute(text("ALTER TABLE cameras ADD COLUMN geom TEXT"))
                    if db.engine.dialect.name == "postgresql":
                        conn.execute(text("UPDATE cameras SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) WHERE geom IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL"))
                    else:
                        conn.execute(text("UPDATE cameras SET geom = 'POINT(' || longitude || ' ' || latitude || ')' WHERE (geom IS NULL OR geom = '') AND latitude IS NOT NULL AND longitude IS NOT NULL"))
                    conn.commit()
        except Exception as exc:
            app.logger.warning("Schema column check skipped or failed: %s", exc)

    # Camera workers are opt-in. Without this flag, every registered camera
    # remains offline until the frontend explicitly starts it.
    if app.config.get("AUTO_START_CAMERAS", False):
        try:
            from app.services.video_ingestion import start_all_cameras
            start_all_cameras(
                app,
                use_catalogue=app.config.get("INGESTION_USE_CATALOGUE", False),
                catalogue_host=app.config.get("CAMERA_CATALOGUE_HOST"),
                camera_ids=app.config.get("INGESTION_CAMERA_IDS", ()),
            )
        except Exception as exc:  # pragma: no cover
            import logging
            logging.getLogger(__name__).warning("Video ingestion not started: %s", exc)
    else:
        # Persisted status describes the last process, not the current workers.
        # Clear it when booting in frontend-controlled mode.
        with app.app_context():
            from app.models import Camera
            changed = Camera.query.filter(Camera.status != "offline").update(
                {Camera.status: "offline"}, synchronize_session=False
            )
            if changed:
                db.session.commit()

    return app
