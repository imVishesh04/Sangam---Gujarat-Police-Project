"""
SANGAM Database Migration & Initialization Script
-------------------------------------------------
Supports both PostgreSQL + PostGIS and SQLite fallback.

Usage:
    python migrate_db.py [--url DATABASE_URL] [--import-sqlite PATH] [--check]

Features:
- Detects database dialect (PostgreSQL or SQLite).
- On PostgreSQL:
    1. Installs/verifies PostGIS extension (CREATE EXTENSION IF NOT EXISTS postgis).
    2. Creates all tables (users, cameras, detections, watchlist, alerts).
    3. Creates GiST spatial index on cameras(geom).
    4. Backfills PostGIS geometries from lat/lon.
    5. Optionally imports records from existing SQLite database.
- On SQLite:
    1. Creates all tables if missing.
    2. Migrates schema (adds geom, alert acknowledgement columns).
    3. Backfills geom text coordinates (POINT(lon lat)).
"""
import os
import sys
import argparse
import logging
from sqlalchemy import create_engine, inspect, text

# Add project root to sys.path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import get_database_uri
from app import create_app, db
from app.models import User, Camera, Detection, Watchlist, Alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_db")


def run_migration(db_uri=None, import_sqlite_path=None, check_only=False):
    target_uri = db_uri or get_database_uri()
    logger.info("Target database URI: %s", target_uri.split("@")[-1] if "@" in target_uri else target_uri)

    if check_only:
        logger.info("Running database check...")
        engine = create_engine(target_uri)
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        logger.info("Existing tables in database: %s", tables)
        if "cameras" in tables:
            cols = [c["name"] for c in inspector.get_columns("cameras")]
            logger.info("Cameras columns: %s", cols)
            has_geom = "geom" in cols
            logger.info("Geom column present: %s", has_geom)
        return True

    # Use Flask app context to bind models and SQLAlchemy metadata
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = target_uri

    with app.app_context():
        engine = db.engine
        dialect = engine.dialect.name
        logger.info("Connected using SQLAlchemy dialect: %s", dialect)

        if dialect == "postgresql":
            logger.info("PostgreSQL detected. Checking PostGIS extension...")
            with engine.connect() as conn:
                try:
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
                    conn.commit()
                    logger.info("PostGIS extension enabled.")
                except Exception as e:
                    logger.warning("Could not execute CREATE EXTENSION postgis (may require superuser privileges): %s", e)

        # Create all tables from SQLAlchemy models
        logger.info("Ensuring all tables exist via db.create_all()...")
        db.create_all()

        # Schema updates & indexing
        with engine.connect() as conn:
            inspector = inspect(engine)
            cam_cols = {col["name"] for col in inspector.get_columns("cameras")}

            if "geom" not in cam_cols:
                logger.info("Adding geom column to cameras table...")
                if dialect == "postgresql":
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN geom geometry(Point, 4326);"))
                else:
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN geom TEXT;"))
                conn.commit()

            # Create PostGIS GiST index if PostgreSQL
            if dialect == "postgresql":
                logger.info("Creating spatial GiST index on cameras(geom)...")
                try:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cameras_geom ON cameras USING GIST (geom);"))
                    conn.commit()
                    logger.info("Spatial GiST index verified.")
                except Exception as e:
                    logger.warning("Spatial index creation warning: %s", e)

            # Backfill geom
            logger.info("Backfilling geometry points for cameras with lat/long...")
            if dialect == "postgresql":
                conn.execute(text("""
                    UPDATE cameras
                    SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
                    WHERE geom IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL;
                """))
            else:
                conn.execute(text("""
                    UPDATE cameras
                    SET geom = 'POINT(' || longitude || ' ' || latitude || ')'
                    WHERE (geom IS NULL OR geom = '') AND latitude IS NOT NULL AND longitude IS NOT NULL;
                """))
            conn.commit()

            # Ensure Alert columns exist
            if "alerts" in inspector.get_table_names():
                alert_cols = {col["name"] for col in inspector.get_columns("alerts")}
                alert_migrations = [
                    ("is_acknowledged", "ALTER TABLE alerts ADD COLUMN is_acknowledged BOOLEAN NOT NULL DEFAULT 0"),
                    ("acknowledged_at", "ALTER TABLE alerts ADD COLUMN acknowledged_at DATETIME"),
                    ("acknowledged_by", "ALTER TABLE alerts ADD COLUMN acknowledged_by VARCHAR(64)"),
                    ("action_taken", "ALTER TABLE alerts ADD COLUMN action_taken VARCHAR(256)"),
                ]
                for col_name, sql in alert_migrations:
                    if col_name not in alert_cols:
                        logger.info("Adding missing column %s to alerts...", col_name)
                        conn.execute(text(sql))
                conn.commit()

        # Optional import from SQLite
        if import_sqlite_path and os.path.exists(import_sqlite_path) and dialect == "postgresql":
            logger.info("Importing records from SQLite database: %s", import_sqlite_path)
            sqlite_engine = create_engine(f"sqlite:///{import_sqlite_path}")
            # Transfer users, cameras, watchlist, alerts, detections
            with sqlite_engine.connect() as s_conn:
                for table_name in ["users", "cameras", "watchlist", "alerts", "detections"]:
                    try:
                        rows = s_conn.execute(text(f"SELECT * FROM {table_name}")).mappings().fetchall()
                        logger.info("Read %d rows from SQLite table '%s'", len(rows), table_name)
                        if rows:
                            with engine.connect() as p_conn:
                                for row in rows:
                                    row_dict = dict(row)
                                    cols_str = ", ".join(row_dict.keys())
                                    vals_str = ", ".join([f":{k}" for k in row_dict.keys()])
                                    insert_sql = text(f"INSERT INTO {table_name} ({cols_str}) VALUES ({vals_str}) ON CONFLICT DO NOTHING")
                                    p_conn.execute(insert_sql, row_dict)
                                p_conn.commit()
                    except Exception as e:
                        logger.warning("Error migrating table %s: %s", table_name, e)

        # Print summary
        cam_count = Camera.query.count()
        user_count = User.query.count()
        alert_count = Alert.query.count()
        logger.info("Migration finished successfully! Summary: %d cameras, %d users, %d alerts.", cam_count, user_count, alert_count)
        return True


def main():
    parser = argparse.ArgumentParser(description="SANGAM Database Migration & Spatial Setup")
    parser.add_argument("--url", default=None, help="Target Database URL (default: from config/DATABASE_URL)")
    parser.add_argument("--import-sqlite", default=None, help="Path to SQLite database to import data from")
    parser.add_argument("--check", action="store_true", help="Check database connection and schema without modifying")

    args = parser.parse_args()
    success = run_migration(db_uri=args.url, import_sqlite_path=args.import_sqlite, check_only=args.check)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
