"""
Detections Blueprint — Module 3 (complete)

Endpoints
---------
GET  /detections/                  — list detections (filterable, paginated)
GET  /detections/<detection_id>    — single detection detail
POST /detections/manual            — manually submit a frame for ANPR (demo/testing)
"""
import base64
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import and_
import numpy as np
import cv2

from app.models import Detection, Camera

detections_bp = Blueprint("detections", __name__, url_prefix="/detections")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@detections_bp.get("/")
@login_required
def list_detections():
    """
    List detections with optional filters.

    Query params
    ------------
    camera_id  : exact camera ID
    plate      : partial plate number match (case-insensitive)
    from       : ISO-8601 start datetime
    to         : ISO-8601 end datetime
    page       : page number (default 1)
    per_page   : results per page (default 50, max 200)
    """
    camera_id = request.args.get("camera_id")
    plate = request.args.get("plate")
    from_dt = _parse_iso(request.args.get("from"))
    to_dt = _parse_iso(request.args.get("to"))
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(200, max(1, int(request.args.get("per_page", 50))))

    filters = []
    if camera_id:
        filters.append(Detection.camera_id == camera_id)
    if plate:
        filters.append(Detection.plate_number.ilike(f"%{plate}%"))
    if from_dt:
        filters.append(Detection.timestamp >= from_dt)
    if to_dt:
        filters.append(Detection.timestamp <= to_dt)

    query = Detection.query.filter(and_(*filters)).order_by(Detection.timestamp.desc())
    total = query.count()
    detections = query.offset((page - 1) * per_page).limit(per_page).all()

    # Cache camera lookups to avoid N+1 queries
    cam_cache: dict[str, Camera] = {}
    enriched_detections = []
    for d in detections:
        if d.camera_id not in cam_cache:
            cam_cache[d.camera_id] = Camera.query.get(d.camera_id)
        cam = cam_cache[d.camera_id]
        item = d.to_dict()
        item["location_name"] = cam.location_name if cam else None
        item["city"] = cam.department if cam else None
        enriched_detections.append(item)

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "detections": enriched_detections,
    })


# ---------------------------------------------------------------------------
# Single
# ---------------------------------------------------------------------------

@detections_bp.get("/<int:detection_id>")
@login_required
def get_detection(detection_id):
    """Return a single detection enriched with camera location."""
    d = Detection.query.get_or_404(detection_id)
    data = d.to_dict()
    cam = Camera.query.get(d.camera_id)
    if cam:
        data["location_name"] = cam.location_name
        data["latitude"] = cam.latitude
        data["longitude"] = cam.longitude
    return jsonify(data)


# ---------------------------------------------------------------------------
# Manual trigger — for demo and integration testing
# ---------------------------------------------------------------------------

@detections_bp.post("/manual")
@login_required
def manual_detection():
    """
    Manually inject a detection without a live camera stream.
    Useful for demoing the full pipeline (ANPR → matching → alert) during the hackathon.

    Accepts two modes:

    Mode 1 — direct plate submission (no image processing):
      { "camera_id": "CAM-AHM-001", "plate_number": "GJ01AB1234", "confidence": 0.95 }

    Mode 2 — base64-encoded frame (runs full ANPR pipeline):
      { "camera_id": "CAM-AHM-001", "frame_b64": "<base64 encoded JPEG>" }

    Admin only.
    """
    if not current_user.is_authenticated or current_user.role != "admin":
        return jsonify({"error": "Admin access required"}), 403

    data = request.get_json(force=True)
    camera_id = data.get("camera_id")
    if not camera_id:
        return jsonify({"error": "camera_id is required"}), 400

    if not Camera.query.get(camera_id):
        return jsonify({"error": f"Camera '{camera_id}' not found"}), 404

    # --- Mode 1: direct plate ---
    if data.get("plate_number"):
        from app import db
        from app.models import Detection
        from app.services.matching_engine import check_and_alert

        detection = Detection(
            camera_id=camera_id,
            plate_number=data["plate_number"].strip().upper(),
            confidence=float(data.get("confidence", 1.0)),
            timestamp=datetime.now(timezone.utc),
        )
        db.session.add(detection)
        db.session.commit()

        alert = check_and_alert(detection)
        return jsonify({
            "detection": detection.to_dict(),
            "alert": alert.to_dict() if alert else None,
        }), 201

    # --- Mode 2: base64 frame → full ANPR pipeline ---
    if data.get("frame_b64"):
        try:
            img_bytes = base64.b64decode(data["frame_b64"])
            arr = np.frombuffer(img_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return jsonify({"error": "Could not decode image"}), 400
        except Exception as exc:
            return jsonify({"error": f"Image decode failed: {exc}"}), 400

        from app.services.anpr import process_frame
        # single manual submission: persist this read immediately (no multi-frame vote)
        process_frame(camera_id, frame, vote=False)
        return jsonify({"message": "Frame submitted to ANPR pipeline"}), 202

    return jsonify({"error": "Provide either plate_number or frame_b64"}), 400
