"""
Application configuration.

Environment variables
---------------------
SECRET_KEY      : Flask session secret (change before deploying)
DATABASE_URL    : SQLAlchemy DB URI (default: sqlite:///cctv.db)
AUTO_START_CAMERAS : Set to "1" to resume background camera threads on startup.
                     Defaults to off; the frontend starts cameras explicitly.
CAMERA_CATALOGUE_HOST : Optional host for the live catalogue, e.g.
                        live.corp8.cloud
INGESTION_USE_CATALOGUE : Set to "1" to use the live catalogue at startup
INGESTION_CAMERA_IDS : Optional comma-separated allow-list; empty means all
                       cameras from the selected source
"""
import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _load_env_file(filepath=None):
    """Safely populate os.environ from .env if present."""
    if filepath is None:
        filepath = os.path.join(BASE_DIR, ".env")
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8-sig") as f:  # -sig: tolerate a BOM saved by Windows editors
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                            v = v[1:-1]
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass


_load_env_file()


def get_database_uri():
    """Return database URI with PostgreSQL normalization and SQLite fallback."""
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw or raw == "sqlite:///cctv.db":
        return f"sqlite:///{os.path.join(BASE_DIR, 'cctv.db')}"
    if raw.startswith("sqlite:///") and not os.path.isabs(raw.replace("sqlite:///", "")):
        rel = raw.replace("sqlite:///", "")
        return f"sqlite:///{os.path.join(BASE_DIR, rel)}"
    # SQLAlchemy 1.4+ deprecated postgres:// in favor of postgresql://
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql://", 1)
    return raw


_WEAK_SECRET_KEYS = {"", "dev-secret-change-in-prod", "changeme", "secret"}


def _resolve_secret_key():
    """
    Session-signing key. A known/placeholder key lets anyone forge a session
    cookie (and become admin), so it is never used: if SECRET_KEY is unset or
    a placeholder, a random key is generated for this run (users simply log in
    again after a restart) - and production refuses to start without one.
    """
    key = os.environ.get("SECRET_KEY", "").strip()
    if key not in _WEAK_SECRET_KEYS and len(key) >= 16:
        return key
    if os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError("SECRET_KEY must be set to a strong random value in production")
    import logging
    import secrets
    logging.getLogger(__name__).warning(
        "SECRET_KEY missing or a placeholder - using a random key for this run. "
        "Set a real one in .env: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
    return secrets.token_hex(32)


class Config:
    SECRET_KEY = _resolve_secret_key()
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    SQLALCHEMY_DATABASE_URI = get_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SOCKETIO_ASYNC_MODE = "eventlet"
    # Frontend (localhost:3000) and backend (localhost:5000) differ only by
    # port, which browsers treat as "same-site" (SameSite only cares about
    # scheme + registrable domain, not port) — so SameSite=Lax cookies are
    # sent on the frontend's credentials:'include' fetches. Secure=False is
    # required because local dev runs over plain HTTP, not HTTPS.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production" or os.environ.get("ENFORCE_HTTPS", "0") == "1"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Rate limiting configuration (Flask-Limiter)
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "300 per minute; 30 per second")
    RATELIMIT_STORAGE_URI = "memory://"
    RATELIMIT_STRATEGY = "fixed-window"
    RATELIMIT_HEADERS_ENABLED = True

    AUTO_START_CAMERAS = os.environ.get("AUTO_START_CAMERAS", "0") == "1"
    CAMERA_CATALOGUE_HOST = os.environ.get("CAMERA_CATALOGUE_HOST", "cctv.corp8.cloud")
    INGESTION_USE_CATALOGUE = os.environ.get("INGESTION_USE_CATALOGUE", "0") == "1"
    INGESTION_CAMERA_IDS = tuple(
        camera_id.strip()
        for camera_id in os.environ.get("INGESTION_CAMERA_IDS", "").split(",")
        if camera_id.strip()
    )

    # Sentinel Grid upstream authentication (loaded strictly from environment/.env)
    SENTINEL_EMAIL = os.environ.get("SENTINEL_EMAIL", "")
    SENTINEL_PASSWORD = os.environ.get("SENTINEL_PASSWORD", "")


class TestingConfig(Config):
    """Used by seed.py and unit tests — no camera threads, in-memory DB."""
    TESTING = True
    AUTO_START_CAMERAS = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", get_database_uri())
    RATELIMIT_ENABLED = False
