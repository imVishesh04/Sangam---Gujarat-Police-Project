"""
Camera Registry Blueprint
-------------------------
Endpoints
  POST   /cameras/              — add a single camera (Admin)
  GET    /cameras/              — list all cameras
  GET    /cameras/<camera_id>   — get one camera
  PUT    /cameras/<camera_id>   — update camera fields (Admin)
  DELETE /cameras/<camera_id>   — remove camera (Admin)
  POST   /cameras/import        — bulk-import from CSV (Admin)
  POST   /cameras/<id>/start    — start ingestion thread (Admin)
  POST   /cameras/<id>/stop     — stop ingestion thread (Admin)
  GET    /cameras/<id>/status   — live ingestion status

CSV format (header row required):
  camera_id,department,location_name,latitude,longitude,vendor,stream_url,format,status"""
import csv
import os
import io
import re
from urllib.parse import quote, urljoin, urlparse, parse_qsl, urlencode, urlunparse
import math
import requests
import threading
from flask import Blueprint, request, jsonify, current_app, Response
from flask_login import login_required, current_user
from app import db, limiter
from app.models import Camera
from app.models.models import _iso
from app.services.audit import log_audit_event

cameras_bp = Blueprint("cameras", __name__, url_prefix="/cameras")
_hls_sessions = {}
_hls_sessions_lock = threading.Lock()


def _admin_required():
    """Return a 403 response dict if the current user is not an admin, else None."""
    if not current_user.is_authenticated or current_user.role != "admin":
        return jsonify({"error": "Admin access required"}), 403
    return None


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@cameras_bp.post("/")
@login_required
def add_camera():
    """Add a single camera to the registry."""
    guard = _admin_required()
    if guard:
        return guard

    data = request.get_json(force=True)
    required = ("camera_id", "department", "location_name", "stream_url")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {missing}"}), 400

    if Camera.query.get(data["camera_id"]):
        return jsonify({"error": "camera_id already exists"}), 409

    cam = Camera(
        camera_id=data["camera_id"],
        department=data["department"],
        location_name=data["location_name"],
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        vendor=data.get("vendor"),
        stream_url=data["stream_url"],
        format=data.get("format", "rtsp"),
        status=data.get("status", "offline"),
    )
    db.session.add(cam)
    db.session.commit()
    log_audit_event(
        action="CAMERA_ADD",
        target_type="camera",
        target_id=cam.camera_id,
        details=f"location={cam.location_name} dept={cam.department}",
    )
    return jsonify(cam.to_dict()), 201


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@cameras_bp.get("/")
@login_required
def list_cameras():
    """Return all registered cameras, optionally filtered by ?status= or ?department="""
    status = request.args.get("status")
    department = request.args.get("department")

    query = Camera.query
    if status:
        query = query.filter_by(status=status)
    if department:
        query = query.filter_by(department=department)

    return jsonify([c.to_dict() for c in query.all()])


def _haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two GPS coordinates in kilometers."""
    R = 6371.0088  # Earth mean radius in km (WGS 84)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


@cameras_bp.get("/nearby")
@login_required
def get_nearby_cameras():
    """
    Find all cameras within radius_km of a GPS point (lat, lon/lng).
    Uses PostGIS ST_DWithin on PostgreSQL, or spherical Haversine distance on SQLite fallback.
    """
    lat_str = request.args.get("lat") or request.args.get("latitude")
    lng_str = request.args.get("lng") or request.args.get("lon") or request.args.get("longitude")
    radius_str = request.args.get("radius_km") or request.args.get("radius") or "5.0"

    if lat_str is None or lng_str is None:
        return jsonify({"error": "Missing required query parameters: lat and lng (or lon)"}), 400

    try:
        lat = float(lat_str)
        lng = float(lng_str)
        radius_km = float(radius_str)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid lat, lng, or radius_km: must be numeric"}), 400

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return jsonify({"error": "Coordinates out of bounds: lat must be [-90, 90], lng [-180, 180]"}), 400

    if radius_km <= 0:
        return jsonify({"error": "radius_km must be greater than 0"}), 400

    status = request.args.get("status")
    department = request.args.get("department")

    engine_used = "sqlite_haversine"
    cameras_with_dist = []

    # Attempt PostGIS spatial query if connected to PostgreSQL
    if db.engine.dialect.name == "postgresql":
        try:
            from sqlalchemy import text
            radius_m = radius_km * 1000.0
            sql_query = """
                SELECT camera_id, department, location_name, latitude, longitude, geom, vendor, stream_url, format, status, last_heartbeat,
                       ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography) / 1000.0 AS distance_km
                FROM cameras
                WHERE geom IS NOT NULL
                  AND ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :radius_m)
            """
            params = {"lat": lat, "lng": lng, "radius_m": radius_m}
            if status:
                sql_query += " AND status = :status"
                params["status"] = status
            if department:
                sql_query += " AND department = :department"
                params["department"] = department
            sql_query += " ORDER BY distance_km ASC"

            with db.engine.connect() as conn:
                rows = conn.execute(text(sql_query), params).fetchall()
                for row in rows:
                    r_dict = dict(row._mapping)
                    if r_dict.get("last_heartbeat"):
                        r_dict["last_heartbeat"] = _iso(r_dict["last_heartbeat"])
                    if r_dict.get("geom") is not None:
                        r_dict["geom"] = str(r_dict["geom"])
                    r_dict["distance_km"] = round(float(r_dict["distance_km"]), 3)
                    cameras_with_dist.append(r_dict)
            engine_used = "postgis"
        except Exception as exc:
            current_app.logger.warning("PostGIS spatial query failed, falling back to Haversine: %s", exc)
            cameras_with_dist = []
            engine_used = "sqlite_haversine"

    # SQLite fallback (or if PostGIS query failed/not on PostgreSQL)
    if engine_used == "sqlite_haversine":
        query = Camera.query.filter(Camera.latitude.isnot(None), Camera.longitude.isnot(None))
        if status:
            query = query.filter_by(status=status)
        if department:
            query = query.filter_by(department=department)

        candidates = query.all()
        for cam in candidates:
            dist = _haversine_distance_km(lat, lng, cam.latitude, cam.longitude)
            if dist <= radius_km:
                cam_data = cam.to_dict()
                cam_data["distance_km"] = round(dist, 3)
                cameras_with_dist.append(cam_data)

        cameras_with_dist.sort(key=lambda c: c["distance_km"])

    return jsonify({
        "engine": engine_used,
        "center": {"latitude": lat, "longitude": lng},
        "radius_km": radius_km,
        "count": len(cameras_with_dist),
        "cameras": cameras_with_dist,
    })


@cameras_bp.get("/<camera_id>")
@login_required
def get_camera(camera_id):
    """Return a single camera by ID."""
    cam = Camera.query.get_or_404(camera_id)
    return jsonify(cam.to_dict())


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

@cameras_bp.put("/<camera_id>")
@login_required
def update_camera(camera_id):
    """Update any mutable field on a camera record."""
    guard = _admin_required()
    if guard:
        return guard

    cam = Camera.query.get_or_404(camera_id)
    data = request.get_json(force=True)

    updatable = ("department", "location_name", "latitude", "longitude",
                 "vendor", "stream_url", "format", "status")
    for field in updatable:
        if field in data:
            setattr(cam, field, data[field])

    db.session.commit()
    log_audit_event(
        action="CAMERA_UPDATE",
        target_type="camera",
        target_id=camera_id,
        details=f"updated fields: {list(data.keys())}",
    )
    return jsonify(cam.to_dict())


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@cameras_bp.delete("/<camera_id>")
@login_required
def delete_camera(camera_id):
    """Remove a camera from the registry."""
    guard = _admin_required()
    if guard:
        return guard

    cam = Camera.query.get_or_404(camera_id)
    db.session.delete(cam)
    db.session.commit()
    log_audit_event(
        action="CAMERA_DELETE",
        target_type="camera",
        target_id=camera_id,
        details=f"deleted camera {camera_id}",
    )
    return jsonify({"message": f"Camera {camera_id} deleted"}), 200


# ---------------------------------------------------------------------------
# Bulk CSV Import
# ---------------------------------------------------------------------------

@cameras_bp.post("/import")
@limiter.limit("30 per minute")
@login_required
def import_cameras():
    """
    Bulk-import cameras from an uploaded CSV file or JSON csv_data payload.

    Supports:
    - multipart/form-data with 'file' field (.csv)
    - application/json with 'csv_data' or 'csv' field
    - text/csv in raw request body
    """
    guard = _admin_required()
    if guard:
        return guard

    csv_text = None

    if request.is_json and request.json:
        csv_text = request.json.get("csv_data") or request.json.get("csv")
    elif "file" in request.files:
        file = request.files["file"]
        if not file.filename.endswith(".csv"):
            return jsonify({"error": "Only .csv files are accepted"}), 400
        csv_text = file.stream.read().decode("utf-8-sig")
    elif request.data:
        try:
            csv_text = request.data.decode("utf-8-sig")
        except Exception:
            csv_text = None

    if not csv_text or not csv_text.strip():
        return jsonify({"error": "No CSV content provided. Send multipart 'file' or JSON 'csv_data'"}), 400

    stream = io.StringIO(csv_text.strip())
    first_line = stream.readline().strip()
    stream.seek(0)

    has_header = "camera_id" in first_line.lower()
    inserted, skipped, errors = 0, 0, []

    if has_header:
        reader = csv.DictReader(stream)
        for i, row in enumerate(reader, start=2):
            cam_id = (row.get("camera_id") or "").strip()
            if not cam_id:
                errors.append(f"Row {i}: missing camera_id")
                continue

            if db.session.get(Camera, cam_id):
                skipped += 1
                continue

            try:
                cam = Camera(
                    camera_id=cam_id,
                    department=row.get("department", "Unknown").strip(),
                    location_name=row.get("location_name", "Unknown").strip(),
                    latitude=float(row["latitude"]) if row.get("latitude") else None,
                    longitude=float(row["longitude"]) if row.get("longitude") else None,
                    vendor=row.get("vendor", "Standard CCTV").strip() if row.get("vendor") else "Standard CCTV",
                    stream_url=row.get("stream_url", "").strip(),
                    format=row.get("format", "rtsp").strip().lower() if row.get("format") else "rtsp",
                    status=row.get("status", "offline").strip().lower() if row.get("status") else "offline",
                )
                db.session.add(cam)
                inserted += 1
            except Exception as exc:
                errors.append(f"Row {i}: {exc}")
    else:
        # Headerless positional CSV
        reader = csv.reader(stream)
        for i, row in enumerate(reader, start=1):
            if not row or not any(row):
                continue
            cols = [c.strip() for c in row]
            cam_id = cols[0] if len(cols) > 0 else ""
            if not cam_id:
                errors.append(f"Row {i}: missing camera_id")
                continue

            if db.session.get(Camera, cam_id):
                skipped += 1
                continue

            try:
                cam = Camera(
                    camera_id=cam_id,
                    department=cols[1] if len(cols) > 1 and cols[1] else "Unknown",
                    location_name=cols[2] if len(cols) > 2 and cols[2] else "Unknown",
                    latitude=float(cols[3]) if len(cols) > 3 and cols[3] else None,
                    longitude=float(cols[4]) if len(cols) > 4 and cols[4] else None,
                    vendor=cols[5] if len(cols) > 5 and cols[5] else "Standard CCTV",
                    stream_url=cols[6] if len(cols) > 6 else "",
                    format=cols[7].lower() if len(cols) > 7 and cols[7] else "rtsp",
                    status=cols[8].lower() if len(cols) > 8 and cols[8] else "offline",
                )
                db.session.add(cam)
                inserted += 1
            except Exception as exc:
                errors.append(f"Row {i}: {exc}")

    db.session.commit()
    log_audit_event(
        action="CAMERA_IMPORT",
        target_type="camera",
        target_id="bulk",
        details=f"inserted={inserted} skipped={skipped} errors={len(errors)}",
    )
    return jsonify({
        "inserted": inserted,
        "imported": inserted,
        "skipped": skipped,
        "errors": errors,
        "total": inserted + skipped + len(errors)
    }), 200


# ---------------------------------------------------------------------------
# Ingestion control
# ---------------------------------------------------------------------------

@cameras_bp.post("/<camera_id>/start")
@login_required
def start_ingestion(camera_id):
    """
    Start the background ingestion thread for a camera.
    Safe to call if the thread is already running (idempotent).
    """
    guard = _admin_required()
    if guard:
        return guard

    cam = Camera.query.get_or_404(camera_id)
    # Status is NOT set to online here: only the worker does that, when the
    # first real frame arrives. The UI shows "Starting…" until then.
    from app.services.video_ingestion import start_camera
    start_camera(cam.camera_id, cam.stream_url, current_app._get_current_object())
    data = cam.to_dict()
    data["ingestion"] = "starting"
    return jsonify(data)


@cameras_bp.post("/<camera_id>/stop")
@login_required
def stop_ingestion(camera_id):
    """Stop the background ingestion thread for a camera."""
    guard = _admin_required()
    if guard:
        return guard

    cam = Camera.query.get_or_404(camera_id)
    from app.services.video_ingestion import stop_camera
    stop_camera(camera_id)
    cam.status = "offline"
    db.session.commit()
    return jsonify(cam.to_dict())


@cameras_bp.post("/bulk")
@limiter.limit("20 per minute")
@login_required
def bulk_ingestion():
    """
    Start or stop many cameras in one click.
    Body: {"action": "start" | "stop",
           "camera_ids": [...]            # optional - exact cameras
           "scope": "all" | "offline" | "online"}   # used when camera_ids is absent
    Start is staggered (BULK_START_GAP_S, default 0.5 s). Status turns "online" only
    when a camera really delivers frames (worker heartbeat), never optimistically.
    """
    guard = _admin_required()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("start", "stop"):
        return jsonify({"error": "action must be 'start' or 'stop'"}), 400

    from app.services import video_ingestion as vi
    ids = data.get("camera_ids")
    q = Camera.query
    if isinstance(ids, list) and ids:
        cams = q.filter(Camera.camera_id.in_([str(i) for i in ids][:500])).all()
    else:
        scope = data.get("scope", "all")
        cams = q.all()
        if scope in ("online", "offline"):
            running = vi.running_camera_ids()
            cams = [c for c in cams if (c.camera_id in running) == (scope == "online")]

    running = vi.running_camera_ids()
    if action == "start":
        todo = [c for c in cams if c.stream_url and c.camera_id not in running]
        skipped = [{"camera_id": c.camera_id, "reason": "already running" if c.camera_id in running else "no stream URL"}
                   for c in cams if c not in todo]
        try:
            gap = max(0.0, min(5.0, float(os.environ.get("BULK_START_GAP_S", "0.5"))))
        except ValueError:
            gap = 0.5
        vi.start_cameras_staggered([(c.camera_id, c.stream_url) for c in todo], current_app._get_current_object(), gap)
        log_audit_event(action="CAMERA_BULK_START", target_type="camera", target_id="bulk",
                        details=f"queued={len(todo)} skipped={len(skipped)}")
        return jsonify({"action": "start", "queued": [c.camera_id for c in todo], "skipped": skipped,
                        "seconds_to_finish": round(gap * max(0, len(todo) - 1), 1)}), 202

    stopped = vi.stop_cameras([c.camera_id for c in cams])
    for c in cams:
        c.status = "offline"
    db.session.commit()
    log_audit_event(action="CAMERA_BULK_STOP", target_type="camera", target_id="bulk",
                    details=f"stopped={len(stopped)} marked_offline={len(cams)}")
    return jsonify({"action": "stop", "stopped": stopped,
                    "marked_offline": [c.camera_id for c in cams]}), 200


@cameras_bp.get("/<camera_id>/status")
@login_required
def ingestion_status(camera_id):
    """
    Return the live in-memory ingestion status for a camera.
    Reflects the actual thread state, not just the DB value.
    """
    Camera.query.get_or_404(camera_id)
    from app.services.video_ingestion import CAMERA_STATE, running_camera_ids
    state = CAMERA_STATE.get(camera_id, {})
    return jsonify({
        "camera_id": camera_id,
        "running": camera_id in running_camera_ids(),
        "status": state.get("status", "offline"),
        "last_seen": state["last_seen"].isoformat() if state.get("last_seen") else None,
    })


def _proxy_url(camera_id, upstream_url):
    """Build a same-origin URL for a playlist child resource."""
    return f"/cameras/{quote(camera_id, safe='')}/stream-proxy?path={quote(upstream_url, safe='')}"


def _normalise_child_url(playlist_url, child):
    """Resolve HLS children and remove playlist-only cookieCheck flags."""
    absolute = urljoin(playlist_url, child)
    parts = urlparse(absolute)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key != "cookieCheck"]
    return urlunparse(parts._replace(query=urlencode(query)))


def _fetch_hls_upstream(session_url, upstream_url):
    """Fetch one upstream HLS playlist/segment/key (green I/O under eventlet)."""
    from app.services.hls import create_hls_session, get_sentinel_session
    if "cctv.corp8.cloud" in upstream_url or "cctv.corp8.cloud" in session_url:
        session = get_sentinel_session()
    else:
        with _hls_sessions_lock:
            session = _hls_sessions.get(session_url)
        if session is None:
            session = create_hls_session(session_url)
            if session:
                with _hls_sessions_lock:
                    _hls_sessions[session_url] = session
    if not session:
        return "session_failed", "Session could not be established with upstream gateway", 502, b""
    try:
        response = session.get(upstream_url, timeout=10, allow_redirects=True)
        if response.status_code in (401, 403) and ("cctv.corp8.cloud" in upstream_url or "cctv.corp8.cloud" in session_url):
            session = get_sentinel_session(force_refresh=True)
            response = session.get(upstream_url, timeout=10, allow_redirects=True)

        if response.status_code != 200:
            status = response.status_code if response.status_code in (502, 503, 504) else 502
            return "error", f"Upstream returned HTTP {response.status_code}", status, b""

        content_type = response.headers.get("Content-Type", "")
        # If upstream returned an HTML page (e.g. Cloudflare error/challenge or login page)
        # when a playlist or video segment was requested:
        is_playlist_req = upstream_url.endswith(".m3u8") or ".m3u8" in upstream_url
        if "text/html" in content_type.lower() or (
            is_playlist_req and not response.content.strip().startswith(b"#EXTM3U")
        ):
            return "error", "Upstream returned non-HLS HTML/error page", 502, b""

        return response.status_code, response.url, content_type, response.content
    except requests.Timeout as exc:
        with _hls_sessions_lock:
            _hls_sessions.pop(session_url, None)
        session.close()
        return "timeout", f"Upstream gateway timed out: {exc}", 504, b""
    except requests.RequestException as exc:
        with _hls_sessions_lock:
            _hls_sessions.pop(session_url, None)
        session.close()
        return "error", str(exc), 502, b""


@cameras_bp.get("/<camera_id>/stream-proxy")
@login_required
def stream_proxy(camera_id):
    """Proxy a Sentinel HLS playlist and its child segments to the browser."""
    cam = Camera.query.get_or_404(camera_id)
    source_url = request.args.get("path") or cam.stream_url
    if not source_url.startswith("http"):
        source_url = urljoin(cam.stream_url, source_url)

    parsed = urlparse(source_url)
    allowed_hosts = {
        "live.corp8.cloud",
        "cctv.corp8.cloud",
        "localhost",
        "127.0.0.1",
    }
    catalogue_host = current_app.config.get("CAMERA_CATALOGUE_HOST")
    if catalogue_host:
        allowed_hosts.add(catalogue_host.lower())

    if (
        parsed.scheme not in ("http", "https")
        or (parsed.netloc.lower() not in allowed_hosts and not parsed.netloc.startswith("127.0.0.1"))
    ):
        return jsonify({"error": f"Unsupported stream source host: {parsed.netloc}"}), 400

    from app.services.hls import hls_target_url
    upstream_url = hls_target_url(source_url) if parsed.path.endswith(".m3u8") else source_url
    # Called directly (NOT through eventlet.tpool). Under eventlet, requests
    # already uses green sockets, so this never blocks other requests. Running
    # it in tpool's native threads made those threads share green locks and the
    # Sentinel session's connection pool with the camera workers, which broke
    # previews at random and failed when several cameras ran together.
    upstream_result = _fetch_hls_upstream(cam.stream_url, upstream_url)

    if not upstream_result:
        return jsonify({"error": "Live stream unavailable: session creation failed", "camera_id": camera_id}), 502

    status_tag = upstream_result[0]
    if status_tag == "timeout":
        detail = upstream_result[1]
        current_app.logger.warning("HLS proxy timeout | camera=%s | %s", camera_id, detail)
        return jsonify({
            "error": "Gateway timeout contacting upstream camera server",
            "detail": detail,
            "camera_id": camera_id
        }), 504

    if status_tag in ("error", "session_failed"):
        detail = upstream_result[1]
        http_code = upstream_result[2] if len(upstream_result) > 2 and isinstance(upstream_result[2], int) else 502
        current_app.logger.warning("HLS proxy fetch failed | camera=%s | %s", camera_id, detail)
        return jsonify({
            "error": "Live stream unavailable",
            "detail": detail,
            "camera_id": camera_id
        }), http_code

    _, upstream_response_url, content_type, upstream_content = upstream_result

    cors_headers = {
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": request.headers.get("Origin", "*"),
        "Access-Control-Allow-Credentials": "true",
    }

    if parsed.path.endswith(".m3u8") or "mpegurl" in content_type.lower():
        playlist = upstream_content.decode("utf-8", errors="replace")
        playlist_url = upstream_response_url

        def rewrite(match):
            child = match.group(1)
            absolute = _normalise_child_url(playlist_url, child)
            return f'URI="{_proxy_url(camera_id, absolute)}"'

        playlist = re.sub(r'URI="([^"]+)"', rewrite, playlist)
        lines = []
        for line in playlist.splitlines():
            if line and not line.startswith("#"):
                line = _proxy_url(camera_id, _normalise_child_url(playlist_url, line))
            lines.append(line)
        return Response(
            "\n".join(lines) + "\n",
            content_type="application/vnd.apple.mpegurl",
            headers=cors_headers,
        )

    return Response(
        upstream_content,
        content_type=content_type or "video/mp2t",
        headers=cors_headers,
    )
