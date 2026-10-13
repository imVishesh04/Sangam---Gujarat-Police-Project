"""
ANPR (Automatic Number Plate Recognition) Service — Module 3 (v3 — YOLOv8 localizer)

Pipeline
--------
1. detect_plate_regions_yolo(frame)
   Runs a pretrained YOLOv8 model that detects two classes: "license-plate"
   and "vehicle". Returns all plate-class crops, sorted by confidence
   (highest first).

   Model source: trained on the Roboflow "license-plates-us-eu" public
   dataset (https://public.roboflow.com/object-detection/license-plates-us-eu).
   It localises plates generically (any rectangular plate shape) rather than
   reading a specific country's format, so it works as a *localizer* even
   though it wasn't trained on Indian plates specifically. No formal license
   file was published with the weights — fine for hackathon/demo use, credit
   the source in your HLD document.

2. preprocess_plate(crop)
   Sharpens and binarises the crop for better OCR accuracy.

3. run_ocr(plate_crop)
   EasyOCR extracts text + confidence from a preprocessed crop.

4. run_ocr_full_frame(frame)
   Fallback path if YOLO finds no plate-class box at all — scans the whole
   frame with EasyOCR's own text detector and regex-filters every fragment.

5. process_frame(camera_id, frame, timestamp)
   Orchestrates steps 1-4, writes a Detection row, and calls the matching engine.

Accuracy strategy (v3)
-----------------------
v1 used a hand-rolled Canny-edge + contour heuristic to *guess* where the
plate was — that's what produced garbage reads like "PNCF" (it was cropping
windows/signboards, not plates).

v2 added a regex gate (INDIAN_PLATE_REGEX) so garbage text is rejected
before it reaches the database, but the underlying region-guessing was
still weak, so many frames simply produced *no* detection.

v3 (this file) replaces the contour guess with an actual trained object
detector (YOLOv8) for the localisation step. This should substantially
increase how often the plate crop is *actually a plate*, which in turn
gives EasyOCR a much cleaner input and higher genuine hit rate. The v2
regex gate is kept as a second line of defence.

Setup
-----
Requires the `ultralytics` package (add to requirements.txt) and the model
weight file at app/services/models/plate_detector.pt (included alongside
this file — do not forget to copy that models/ folder into your project).
"""
import logging
import os
import re
import threading
from datetime import datetime, timezone

import cv2
import numpy as np

from app.services.plate_logic import correct_plate, get_voter

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.35       # secondary quality gate (regex match is the primary gate)
MIN_PLATE_CHARS = 8         # Indian plates are 9-10 chars; filters short noise early
YOLO_CONF_THRESHOLD = 0.35  # minimum YOLO box confidence to even attempt OCR on it
CASCADE_UPSCALE_FACTOR = 3.0

# v4 accuracy layer (see plate_logic.py) --------------------------------------
# Max characters we may "repair" (O->0, I->1, B->8 ...) per read. Crop reads come
# from a YOLO-localised plate so we allow more repair; full-frame fragments are
# riskier (could be any text on a signboard) so we allow very little.
CROP_MAX_FIX_COST = int(os.environ.get("ANPR_CROP_MAX_FIX_COST", "3"))
FULLFRAME_MAX_FIX_COST = int(os.environ.get("ANPR_FULLFRAME_MAX_FIX_COST", "1"))
# Reject reads whose first two letters aren't a real Indian state/UT code.
REQUIRE_STATE_CODE = os.environ.get("ANPR_REQUIRE_STATE", "1") == "1"
# Confidence penalty per repaired character, so repaired reads rank lower.
FIX_CONF_PENALTY = 0.9

# Standard Indian number plate format: SS DD L{1,3} DDDD
INDIAN_PLATE_REGEX = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$")

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "plate_detector.pt")

# Lazily-initialised globals (heavy imports / model loads happen on first use)
_reader = None
_yolo_model = None
_latest_annotations: dict[str, dict] = {}
# A NATIVE lock: inference runs in eventlet's native thread pool (see
# app/services/blocking.py) where a green lock would not protect anything.
from app.services.blocking import native_lock, run_blocking  # noqa: E402
_inference_lock = native_lock()


def warm_up():
    """Load both inference models and run one inference on a blank frame."""
    blank_frame = np.zeros((640, 640, 3), dtype=np.uint8)

    with _inference_lock:
        try:
            yolo_model = _get_yolo_model()
            if yolo_model is not None:
                yolo_model(blank_frame, verbose=False)
                logger.info("YOLO warm-up complete")
        except Exception as exc:
            logger.error("YOLO warm-up failed: %s", exc)

        try:
            reader = _get_reader()
            reader.readtext(blank_frame, detail=1, paragraph=False)
            logger.info("EasyOCR warm-up complete")
        except Exception as exc:
            logger.error("EasyOCR warm-up failed: %s", exc)


def _ocr_use_gpu() -> bool:
    """
    EasyOCR device choice. ANPR_OCR_GPU = auto (default) | 1 | 0.
    'auto' / '1' use CUDA only if torch can actually see a GPU; '0' forces CPU.
    """
    pref = os.environ.get("ANPR_OCR_GPU", "auto").strip().lower()
    if pref in ("0", "false", "no", "cpu"):
        return False
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        use_gpu = _ocr_use_gpu()
        logger.info("Initialising EasyOCR reader (first use) | gpu=%s", use_gpu)
        try:
            _reader = easyocr.Reader(["en"], gpu=use_gpu)
        except Exception as exc:
            if not use_gpu:
                raise
            logger.error("EasyOCR GPU init failed (%s) - falling back to CPU", exc)
            _reader = easyocr.Reader(["en"], gpu=False)
    return _reader


def _get_yolo_model():
    """
    Lazily load the YOLOv8 plate/vehicle detector.
    Returns None (instead of raising) if the model file is missing or
    ultralytics isn't installed — callers fall back to full-frame OCR.
    """
    global _yolo_model
    if _yolo_model is None:
        try:
            from ultralytics import YOLO
            if not os.path.exists(_MODEL_PATH):
                logger.error("YOLO plate model not found at %s — falling back to full-frame OCR only.", _MODEL_PATH)
                return None
            _yolo_model = YOLO(_MODEL_PATH)
            logger.info("YOLOv8 plate detector loaded | classes=%s", _yolo_model.names)
        except Exception as exc:
            logger.error("Failed to load YOLO plate model: %s — falling back to full-frame OCR only.", exc)
            return None
    return _yolo_model


def _normalise(raw_text: str) -> str:
    """Uppercase and strip everything except A-Z and 0-9."""
    return re.sub(r"[^A-Z0-9]", "", raw_text.upper())


def is_plausible_plate(text: str) -> bool:
    """True if `text` matches the standard Indian number-plate format."""
    return bool(INDIAN_PLATE_REGEX.match(text))


# ---------------------------------------------------------------------------
# Debug helper — dump frames/crops to disk so you can SEE what the pipeline
# is actually looking at. Enable with env var ANPR_DEBUG_DUMP=1. Only keeps
# the most recent DEBUG_DUMP_KEEP images per (camera, tag) to avoid filling
# the disk — each new save overwrites the oldest.
# ---------------------------------------------------------------------------
DEBUG_DUMP_ENABLED = os.environ.get("ANPR_DEBUG_DUMP", "0") == "1"
DEBUG_DUMP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "debug_frames")
DEBUG_DUMP_KEEP = 5
_dump_counters: dict[str, int] = {}


def _debug_dump_frame(camera_id: str, image: np.ndarray, tag: str):
    if not DEBUG_DUMP_ENABLED or image is None or image.size == 0:
        return
    try:
        key = f"{camera_id}_{tag}"
        n = _dump_counters.get(key, 0) % DEBUG_DUMP_KEEP
        _dump_counters[key] = _dump_counters.get(key, 0) + 1
        os.makedirs(DEBUG_DUMP_DIR, exist_ok=True)
        path = os.path.join(DEBUG_DUMP_DIR, f"{camera_id}_{tag}_{n}.jpg")
        cv2.imwrite(path, image)
    except Exception as exc:
        logger.error("Debug dump failed | camera=%s tag=%s | %s", camera_id, tag, exc)


# ---------------------------------------------------------------------------
# Step 1 — Plate region detection (YOLOv8)
# ---------------------------------------------------------------------------

def _detect_yolo_boxes(frame: np.ndarray) -> list[dict]:
    """Return all detected plate/vehicle boxes and their metadata."""
    model = _get_yolo_model()
    if model is None:
        return []

    with _inference_lock:
        results = model(frame, verbose=False)[0]
    boxes = []
    for box in results.boxes:
        conf = float(box.conf[0])
        if conf < YOLO_CONF_THRESHOLD:
            continue
        cls_id = int(box.cls[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
        if x2 <= x1 or y2 <= y1:
            continue
        boxes.append({
            "class_name": model.names.get(cls_id, str(cls_id)),
            "confidence": conf,
            "coordinates": (x1, y1, x2, y2),
        })
    return boxes


def _detect_vehicle_first_plate_boxes(
    frame: np.ndarray,
    vehicle_detections: list[dict],
    upscale_factor: float = CASCADE_UPSCALE_FACTOR,
) -> list[dict]:
    """Detect plates inside upscaled vehicle crops and map them to the frame."""
    cascade_boxes = []
    frame_height, frame_width = frame.shape[:2]
    for vehicle in vehicle_detections:
        if vehicle["class_name"] != "vehicle":
            continue

        vehicle_x1, vehicle_y1, vehicle_x2, vehicle_y2 = vehicle["coordinates"]
        vehicle_crop = frame[vehicle_y1:vehicle_y2, vehicle_x1:vehicle_x2]
        if vehicle_crop.size == 0:
            continue
        upscaled_crop = cv2.resize(
            vehicle_crop,
            None,
            fx=upscale_factor,
            fy=upscale_factor,
            interpolation=cv2.INTER_CUBIC,
        )
        for plate in _detect_yolo_boxes(upscaled_crop):
            if plate["class_name"] != "license-plate":
                continue
            local_x1, local_y1, local_x2, local_y2 = plate["coordinates"]
            full_x1 = round(vehicle_x1 + local_x1 / upscale_factor)
            full_y1 = round(vehicle_y1 + local_y1 / upscale_factor)
            full_x2 = round(vehicle_x1 + local_x2 / upscale_factor)
            full_y2 = round(vehicle_y1 + local_y2 / upscale_factor)
            full_x1 = max(0, min(full_x1, frame_width - 1))
            full_y1 = max(0, min(full_y1, frame_height - 1))
            full_x2 = max(full_x1 + 1, min(full_x2, frame_width))
            full_y2 = max(full_y1 + 1, min(full_y2, frame_height))
            cascade_boxes.append({
                "class_name": "license-plate",
                "confidence": plate["confidence"],
                "coordinates": (full_x1, full_y1, full_x2, full_y2),
                "source": "vehicle-first-cascade",
                "crop_offset": (vehicle_x1, vehicle_y1),
                "upscale_factor": upscale_factor,
            })
    return cascade_boxes


def detect_plate_regions_yolo(frame: np.ndarray) -> list[np.ndarray]:
    """
    Run the YOLOv8 detector and return every "license-plate"-class crop,
    sorted by detection confidence (highest first). Empty list if the
    model isn't available or found nothing.
    """
    scored_crops = []
    for detection in _detect_yolo_boxes(frame):
        if detection["class_name"] != "license-plate":
            continue
        x1, y1, x2, y2 = detection["coordinates"]
        scored_crops.append((detection["confidence"], frame[y1:y2, x1:x2]))

    scored_crops.sort(key=lambda c: c[0], reverse=True)
    return [crop for _, crop in scored_crops]


def annotate_frame(frame: np.ndarray, camera_id: str = "") -> np.ndarray:
    """Draw YOLO boxes and the latest ANPR/watchlist evidence on a frame."""
    annotated = frame.copy()
    evidence = _latest_annotations.get(camera_id, {})
    for detection in evidence.get("boxes", []):
        x1, y1, x2, y2 = detection["coordinates"]
        class_name = detection["class_name"]
        color = (0, 220, 255) if class_name == "license-plate" else (255, 180, 0)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            annotated,
            f"{class_name} {detection['confidence']:.2f}",
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )

    plate_text = evidence.get("plate")
    if plate_text:
        cv2.putText(
            annotated,
            plate_text,
            (24, 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.1,
            (0, 255, 0),
            3,
            cv2.LINE_AA,
        )
    alert_plate = evidence.get("alert_plate")
    if alert_plate:
        cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 64), (0, 0, 180), -1)
        cv2.putText(
            annotated,
            f"ALERT: {alert_plate} MATCHED",
            (24, 43),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            3,
            cv2.LINE_AA,
        )
    return annotated


# ---------------------------------------------------------------------------
# Step 2 — Pre-processing
# ---------------------------------------------------------------------------

def preprocess_plate(crop: np.ndarray) -> np.ndarray:
    """
    Sharpen and binarise a plate crop to improve OCR accuracy.
    Resizes to a standard height so EasyOCR gets consistent input.
    """
    target_h = 64
    h, w = crop.shape[:2]
    scale = target_h / h
    resized = cv2.resize(crop, (max(1, int(w * scale)), target_h), interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    return binary


# ---------------------------------------------------------------------------
# Step 3 — OCR on a single (plate-sized) crop
# ---------------------------------------------------------------------------

def run_ocr(plate_crop: np.ndarray) -> tuple[str, float]:
    """
    Run EasyOCR on a (preprocessed) plate crop.
    Returns (normalised_plate_text, confidence) or ("", 0.0) on failure.
    """
    try:
        processed = preprocess_plate(plate_crop)
        reader = _get_reader()
        with _inference_lock:
            results = reader.readtext(processed, detail=1, paragraph=False)
        if not results:
            return "", 0.0

        combined_text = "".join(r[1] for r in results)
        plate_text = _normalise(combined_text)
        mean_conf = round(sum(r[2] for r in results) / len(results), 4)

        return plate_text, mean_conf
    except Exception as exc:
        logger.error("OCR error: %s", exc)
        return "", 0.0


# ---------------------------------------------------------------------------
# Step 4 — Full-frame fallback OCR (used only if YOLO finds nothing)
# ---------------------------------------------------------------------------

def run_ocr_full_frame(frame: np.ndarray) -> tuple[str, float]:
    """
    Scan the entire frame for text using EasyOCR's own text detector.
    Every fragment is regex-validated; the highest-confidence match wins.
    Slower than run_ocr() on a small crop, so only used as a last resort.
    """
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        reader = _get_reader()
        with _inference_lock:
            results = reader.readtext(gray, detail=1, paragraph=False)

        best_text, best_conf = "", 0.0
        for _, raw_text, conf in results:
            fix = correct_plate(
                raw_text,
                max_cost=FULLFRAME_MAX_FIX_COST,
                require_state=REQUIRE_STATE_CODE,
            )
            if fix is None:
                continue
            eff_conf = conf * (FIX_CONF_PENALTY ** fix.cost)
            if eff_conf > best_conf:
                best_text, best_conf = fix.plate, eff_conf

        return best_text, round(best_conf, 4)
    except Exception as exc:
        logger.error("Full-frame OCR error: %s", exc)
        return "", 0.0


# ---------------------------------------------------------------------------
# Step 5 — Full pipeline entry point
# ---------------------------------------------------------------------------

def _read_plate(camera_id: str, frame: np.ndarray):
    """Pure CPU part of ANPR: YOLO boxes + OCR + plate repair.

    Runs in the native thread pool (no DB, no sockets, no Flask context).
    Log lines are collected in `notes` and written by the caller, so this
    function never touches the green logging locks from a native thread.
    Returns (plate_text, confidence, yolo_detections, notes).
    """
    notes: list = []

    def note(*args):
        notes.append(args)

    plate_text, confidence = "", 0.0

    # --- DEBUG: dump raw frames + YOLO crops to disk so we can visually
    # inspect what the pipeline is actually seeing. Controlled by env var
    # ANPR_DEBUG_DUMP=1. Keeps only the last DEBUG_DUMP_KEEP frames per
    # camera to avoid filling the disk.
    _debug_dump_frame(camera_id, frame, tag="frame")

    # --- Primary path: YOLO-localised crops, best confidence first ---
    yolo_detections = _detect_yolo_boxes(frame)
    plate_detections = [
        detection for detection in yolo_detections
        if detection["class_name"] == "license-plate"
    ]
    if not plate_detections:
        plate_detections = _detect_vehicle_first_plate_boxes(frame, yolo_detections)
        yolo_detections.extend(plate_detections)
        note(
            "Vehicle-first cascade | camera=%s plates=%d scale=%.1fx",
            camera_id,
            len(plate_detections),
            CASCADE_UPSCALE_FACTOR,
        )
    yolo_crops = [
        frame[y1:y2, x1:x2]
        for detection in plate_detections
        for x1, y1, x2, y2 in [detection["coordinates"]]
    ]
    note("ANPR attempt | camera=%s yolo_crops=%d", camera_id, len(yolo_crops))
    best_cost = None
    for i, crop in enumerate(yolo_crops):
        _debug_dump_frame(camera_id, crop, tag=f"crop{i}")
        raw_text, raw_conf = run_ocr(crop)
        fix = correct_plate(
            raw_text,
            max_cost=CROP_MAX_FIX_COST,
            require_state=REQUIRE_STATE_CODE,
        )
        note(
            "ANPR raw OCR (yolo crop) | camera=%s raw=%r conf=%.2f fixed=%r cost=%s",
            camera_id, raw_text, raw_conf,
            fix.plate if fix else None, fix.cost if fix else None,
        )
        if fix is None:
            continue
        eff_conf = raw_conf * (FIX_CONF_PENALTY ** fix.cost)
        # keep the cleanest read across crops (fewest repairs, then highest conf)
        if best_cost is None or (fix.cost, -eff_conf) < (best_cost, -confidence):
            plate_text, confidence, best_cost = fix.plate, eff_conf, fix.cost
        if fix.cost == 0:
            break


    # --- Fallback: full-frame scan (only if YOLO path found nothing valid) ---
    if not plate_text:
        plate_text, confidence = run_ocr_full_frame(frame)
        note(
            "ANPR raw OCR (full-frame fallback) | camera=%s raw=%r conf=%.2f",
            camera_id, plate_text, confidence,
        )

    return plate_text, confidence, yolo_detections, notes


def process_frame(
    camera_id: str,
    frame: np.ndarray,
    timestamp: datetime | None = None,
    vote: bool = True,
):
    """
    End-to-end ANPR for one frame.

    1. Ask YOLO for every plate-shaped region, best confidence first.
       OCR each crop; noisy text is repaired with position-aware correction
       (plate_logic.correct_plate) and must end up as a valid Indian plate.
    2. If YOLO found nothing (or none of its crops yielded a valid plate),
       fall back to scanning the whole frame with EasyOCR's text detector.
    3. Multi-frame vote (plate_logic.PlateVoter): a plate is only persisted
       once enough reads of the same vehicle agree (or one read is very
       confident), and the same plate is not re-logged during a cooldown.
       Pass vote=False to persist a single read immediately (manual/test
       submissions).
    4. Persist the Detection and hand off to the matching engine.
    """
    from app import db
    from app.models import Detection
    from app.services.matching_engine import check_and_alert

    timestamp = timestamp or datetime.now(timezone.utc)

    plate_text, confidence, yolo_detections, notes = run_blocking(_read_plate, camera_id, frame)
    for note in notes:
        logger.info(*note)
    _latest_annotations[camera_id] = {
        "boxes": yolo_detections,
        "plate": plate_text or None,
        "alert_plate": None,
    }

    # --- Final quality gate ---
    if not plate_text or len(plate_text) < MIN_PLATE_CHARS or confidence < MIN_CONFIDENCE:
        logger.info("ANPR rejected | camera=%s reason=quality_gate raw=%r conf=%.2f", camera_id, plate_text, confidence)
        return
    if not is_plausible_plate(plate_text):
        logger.info("ANPR rejected | camera=%s reason=regex_mismatch raw=%r", camera_id, plate_text)
        return

    # --- Multi-frame consensus: one noisy frame must not become a Detection ---
    if vote:
        verdict = get_voter().observe(camera_id, plate_text, confidence, timestamp)
        if verdict.reason == "cooldown":
            logger.info("ANPR duplicate suppressed | camera=%s plate=%s", camera_id, verdict.plate)
            return
        if not verdict.committed:
            logger.info(
                "ANPR pending vote | camera=%s candidate=%s votes=%d/%d",
                camera_id, verdict.plate, verdict.votes, get_voter().min_votes,
            )
            return
        logger.info(
            "ANPR vote committed | camera=%s plate=%s votes=%d reason=%s",
            camera_id, verdict.plate, verdict.votes, verdict.reason,
        )
        plate_text, confidence = verdict.plate, verdict.confidence
        # stamp the detection with the vehicle's FIRST sighting in this cluster
        timestamp = datetime.fromtimestamp(verdict.first_seen_s, tz=timezone.utc)

    detection = Detection(
        camera_id=camera_id,
        plate_number=plate_text,
        confidence=confidence,
        timestamp=timestamp,
    )
    db.session.add(detection)
    db.session.commit()

    logger.info("ANPR hit | camera=%s plate=%s conf=%.2f", camera_id, plate_text, confidence)

    alert = check_and_alert(detection)
    _latest_annotations[camera_id] = {
        "boxes": yolo_detections,
        "plate": plate_text,
        "alert_plate": detection.plate_number if alert else None,
    }
    return annotate_frame(frame, camera_id)
