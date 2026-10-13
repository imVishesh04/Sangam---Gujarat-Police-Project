"""
survey_cameras.py — Sentinel grid reconnaissance & ANPR viability tool
======================================================================
Hardened for Gujarat Police Innovation Hackathon (SANGAM).

Features:
1. Reads live catalogue from GET /api/cameras (or GET /api/ingest) with multi-host
   failover (live.corp8.cloud <-> cctv.corp8.cloud).
2. Falls back to local database registry (cctv.db) if remote catalogue is unreachable.
3. Fast HTTP pre-flight checks (detects 502 Bad Gateway, 403 Forbidden, 404 Not Found)
   before FFmpeg timeout.
4. Per-camera HLS cookie handshake with retry backoff.
5. RTSP TCP and local file fallback support.
6. YOLO license-plate + vehicle detection scoring.
7. Generates annotated image evidence (cam_<id>.jpg), plate crops (cam_<id>_plate_crop.jpg),
   and annotated MP4 video clips (cam_<id>_evidence.mp4).
8. Preserves historical records on single-camera runs and maintains backups in survey_backups/.
"""
import argparse
import csv
import json
import os
import shutil
import sys
import time
import urllib.parse
from datetime import datetime
import re
import requests

# Force RTSP over TCP and set ffmpeg socket timeout to 8s
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;8000000"

import cv2  # noqa: E402
from app.services.hls import (  # noqa: E402
    create_hls_session,
    establish_hls_session as shared_establish_hls_session,
    hls_capture_options,
    hls_target_url,
)

OUTPUT_DIR = "survey_output"
REPORT_PATH = "survey_report.csv"
BACKUP_DIR = "survey_backups"

OPEN_TIMEOUT_S = 15
DEFAULT_SAMPLE_SECONDS = 15
SAMPLE_INTERVAL_S = 0.5

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://cctv.corp8.cloud/",
    "Origin": "https://cctv.corp8.cloud",
}


def backup_previous_run():
    """Archive the prior survey_output/ + CSV before this run."""
    has_output = os.path.isdir(OUTPUT_DIR) and os.listdir(OUTPUT_DIR)
    has_report = os.path.exists(REPORT_PATH)
    if not has_output and not has_report:
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, stamp)
    os.makedirs(dest, exist_ok=True)
    if has_output:
        shutil.copytree(OUTPUT_DIR, os.path.join(dest, OUTPUT_DIR))
    if has_report:
        shutil.copy2(REPORT_PATH, os.path.join(dest, REPORT_PATH))
    print(f"[+] Backed up previous survey run to {dest}/")


def fetch_catalogue(hosts: list):
    """Attempt to fetch catalogue from multiple candidate endpoints."""
    from app.services.hls import get_sentinel_session
    session = get_sentinel_session()
    endpoints = ["/cameras.json", "/api/cameras", "/api/ingest"]
    for host in hosts:
        for ep in endpoints:
            url = f"https://{host}{ep}"
            print(f"[*] Probing catalogue: {url}")
            try:
                resp = session.get(url, timeout=8, allow_redirects=True)
                if resp.status_code == 200:
                    data = resp.json()
                    cams = data if isinstance(data, list) else data.get("cameras", [])
                    if cams:
                        print(f"[+] Catalogue returned {len(cams)} cameras from {url}")
                        return cams
                else:
                    print(f"    -> HTTP {resp.status_code}")
            except Exception as exc:
                print(f"    -> Connection failed: {exc}")
    return []


def load_cameras_from_db():
    """Load camera catalogue from local SQLite database (cctv.db)."""
    cams = []
    try:
        from app import create_app
        from app.models import Camera
        app = create_app("config.TestingConfig")
        with app.app_context():
            rows = Camera.query.all()
            for r in rows:
                cams.append({
                    "id": r.camera_id,
                    "camera_id": r.camera_id,
                    "location": r.location_name,
                    "stream_url": r.stream_url,
                    "format": r.format,
                    "status": r.status,
                    "vendor": r.vendor,
                })
        print(f"[+] Loaded {len(cams)} cameras from local database.")
    except Exception as exc:
        print(f"[!] Failed to load cameras from local DB: {exc}")
    return cams


def _pick(entry: dict, *keys, default=None):
    for k in keys:
        if k in entry and entry[k] not in (None, ""):
            return entry[k]
    return default


def probe_http_endpoint(url: str, timeout: float = 6.0):
    """Pre-flight check for HTTP stream endpoint. Returns (ok: bool, detail: str)."""
    try:
        from app.services.hls import get_sentinel_session
        session = get_sentinel_session()
        resp = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
        status = resp.status_code
        content_type = resp.headers.get("Content-Type", "")
        chunk = next(resp.iter_content(chunk_size=512), b"")
        resp.close()
        if status == 200:
            if b"#EXTM3U" in chunk or "mpegurl" in content_type:
                return True, "HLS 200 OK (Playlist valid)"
            return True, f"HTTP 200 ({content_type})"
        elif status == 502:
            return False, "HTTP 502 Bad Gateway"
        elif status in (401, 403):
            return False, f"HTTP {status} Forbidden/Unauthorized"
        elif status == 404:
            return False, "HTTP 404 Not Found"
        return False, f"HTTP {status}"
    except Exception as exc:
        return False, f"Connection error: {exc}"

        if status == 200:
            if b"#EXTM3U" in chunk or "mpegurl" in content_type.lower():
                return True, "HTTP 200 (Active HLS Playlist)"
            if b"<html" in chunk.lower() or b"cloudflare" in chunk.lower():
                return False, f"HTTP 200 but returned HTML error page (Cloudflare)"
            return True, f"HTTP 200 ({content_type})"
        elif status == 502:
            return False, "HTTP 502 Bad Gateway (Cloudflare: upstream origin offline)"
        elif status == 403:
            return False, "HTTP 403 Forbidden (Handshake / Token required)"
        elif status == 404:
            return False, "HTTP 404 Stream Not Found"
        return False, f"HTTP {status}"
    except requests.exceptions.Timeout:
        return False, f"HTTP Connection Timed Out (> {timeout}s)"
    except requests.exceptions.RequestException as exc:
        return False, f"Network Error: {type(exc).__name__}"


def score_frame_with_box(model, frame):
    if model is None:
        return (0, 0, 0.0, 0), None, frame

    results = model(frame, verbose=False)[0]
    n_plates = n_vehicles = 0
    best_conf = 0.0
    largest_px = 0
    best_box = None
    annotated = frame.copy()

    for box in results.boxes:
        cls_name = results.names.get(int(box.cls[0]))
        conf = float(box.conf[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0])

        if cls_name == "license-plate":
            n_plates += 1
            best_conf = max(best_conf, conf)
            w_box = x2 - x1
            largest_px = max(largest_px, w_box)
            if best_box is None or (w_box, conf) > (best_box[2] - best_box[0], best_box[4]):
                best_box = (x1, y1, x2, y2, conf)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(annotated, f"PLATE {conf:.2f}", (x1, max(y1 - 5, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        elif cls_name == "vehicle":
            n_vehicles += 1
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 180, 0), 1)
            cv2.putText(annotated, f"VEHICLE {conf:.2f}", (x1, max(y1 - 5, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 180, 0), 1)

    return (n_plates, n_vehicles, round(best_conf, 3), largest_px), best_box, annotated


def viability(n_plates: int, largest_px: int) -> str:
    if n_plates == 0:
        return "NONE - no plate detected (overview shot or empty)"
    if largest_px >= 80:
        return "GOOD - plate large enough for OCR (>=80px)"
    if largest_px >= 40:
        return "MARGINAL - try upscaling / cascade (40-79px)"
    return "POOR - plate too small to read (<40px)"


def grab_snapshot(
    rtsp_url: str = None,
    hls_url: str = None,
    local_path: str = None,
    cookie_str: str = "",
    model=None,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    retries: int = 1,
    record_video_path: str = None,
):
    """Attempt capture across available transports, scoring frames and saving evidence."""

    def sample_stream(cap, transport_name):
        best = None
        best_score = (0, 0, 0.0, 0)
        best_box = None
        sampled_frames = 0
        plate_frames = 0
        recorded_frames = []

        deadline = time.time() + sample_seconds
        next_sample = time.monotonic()

        while time.time() < deadline:
            ok, frame = cap.read()
            if not ok or frame is None or not frame.size:
                time.sleep(0.05)
                continue

            now = time.monotonic()
            if now < next_sample:
                continue
            next_sample = now + SAMPLE_INTERVAL_S
            sampled_frames += 1

            frame_score, plate_box, annotated = score_frame_with_box(model, frame)
            if frame_score[0] > 0:
                plate_frames += 1

            if len(recorded_frames) < 120:
                recorded_frames.append(annotated)

            if best is None or (frame_score[3], frame_score[2], frame_score[0]) > (
                best_score[3], best_score[2], best_score[0]
            ):
                best = frame
                best_score = frame_score
                best_box = plate_box

        cap.release()

        if best is None:
            return None, 0, 0, transport_name, sampled_frames, plate_frames, best_score, best_box, "Opened but no valid frames received"

        h, w = best.shape[:2]

        # Record video evidence if enabled and frames collected
        if record_video_path and recorded_frames:
            try:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(record_video_path, fourcc, 10.0, (w, h))
                for rf in recorded_frames:
                    writer.write(rf)
                writer.release()
                print(f"    -> Recorded {len(recorded_frames)} frames video evidence: {record_video_path}")
            except Exception as exc:
                print(f"    -> Warning: could not write evidence video: {exc}")

        return best, w, h, transport_name, sampled_frames, plate_frames, best_score, best_box, "OK"

    # 1. Local file transport (if path exists)
    if local_path and os.path.exists(local_path):
        print(f"    trying Local Video File: {local_path}")
        cap = cv2.VideoCapture(local_path)
        if cap.isOpened():
            res = sample_stream(cap, "FILE")
            if res[0] is not None:
                print("    -> FILE OK")
                return res

    # 2. HLS Transport (Fast pre-flight check)
    hls_detail = ""
    if hls_url:
        pre_ok, pre_detail = probe_http_endpoint(hls_url)
        hls_detail = pre_detail
        print(f"    HLS Pre-flight: {pre_detail}")
        if pre_ok:
            for attempt in range(1, retries + 1):
                print(f"    trying HLS (attempt {attempt}/{retries}): {hls_url}")
                from app.services.hls import open_capture
                cap = open_capture(hls_url)
                if cap.isOpened():
                    res = sample_stream(cap, "HLS")
                    if res[0] is not None:
                        print("    -> HLS OK")
                        return res
                print("    -> HLS failed to open")
        elif "502" in pre_detail:
            # When gateway is 502, RTSP to the same host will also fail or block
            return None, 0, 0, "HLS", 0, 0, (0, 0, 0.0, 0), None, pre_detail

    # 3. RTSP Transport (if HLS didn't confirm gateway outage)
    if rtsp_url:
        for attempt in range(1, retries + 1):
            print(f"    trying RTSP (attempt {attempt}/{retries}): {rtsp_url}")
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;8000000"
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            if cap.isOpened():
                res = sample_stream(cap, "RTSP")
                if res[0] is not None:
                    print("    -> RTSP OK")
                    return res
            print("    -> RTSP failed to open")

    return None, 0, 0, "NONE", 0, 0, (0, 0, 0.0, 0), None, hls_detail or "All transports exhausted"


def main():
    ap = argparse.ArgumentParser(description="Sentinel CCTV Grid Reconnaissance & Viability Survey")
    ap.add_argument("--host", default="cctv.corp8.cloud", help="Primary gateway host")
    ap.add_argument("--alt-host", default="live.corp8.cloud", help="Alternate gateway host")
    ap.add_argument("--no-yolo", action="store_true", help="Snapshots only, skip YOLO scoring")
    ap.add_argument("--only", help="Survey a single camera ID (e.g. 6 or CAM-AHM-001)")
    ap.add_argument("--model", default="app/services/models/plate_detector.pt")
    ap.add_argument("--hls-only", action="store_true", help="Skip RTSP probes")
    ap.add_argument("--retries", type=int, default=2, help="Connection attempts per stream")
    ap.add_argument("--sample-seconds", type=float, default=12.0, help="Observation window per stream")
    ap.add_argument("--include-local", action="store_true", help="Include registered local demo streams in survey")
    ap.add_argument("--record-video", action="store_true", default=True, help="Record annotated video evidence")
    args = ap.parse_args()

    backup_previous_run()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    model = None
    if not args.no_yolo:
        if not os.path.exists(args.model):
            print(f"[!] YOLO model not found at {args.model} — snapshots only.")
        else:
            from ultralytics import YOLO
            model = YOLO(args.model)
            print(f"[+] Detector loaded successfully: {model.names}")

    # Fetch catalogue with multi-host fallback
    cameras = fetch_catalogue([args.host, args.alt_host])
    if not cameras:
        print("[*] Remote catalogue unavailable. Falling back to local database registry...")
        cameras = load_cameras_from_db()

    if not cameras:
        print("[!] No cameras found from catalogue or database. Exiting.")
        sys.exit(1)

    # Filter for single camera if requested
    if args.only:
        target = str(args.only).strip().lower()
        matched = []
        for c in cameras:
            cid = str(_pick(c, "id", "camera_id", default="")).lower()
            surl = str(_pick(c, "stream", "stream_url", default="")).lower()
            loc = str(_pick(c, "location", "location_name", "name", default="")).lower()
            if target == cid or (target.isdigit() and (cid == f"cam{int(target):02d}" or cid == f"cam-sentinel-{int(target):02d}")):
                matched.append(c)
            elif (target in ["6", "06"]) and ("cam06" in surl or "06" in loc or "cam-ahm-001" in cid):
                matched.append(c)
            elif target in cid or target in surl:
                matched.append(c)
        if matched:
            cameras = matched
        else:
            print(f"[*] Camera {target} not in list — creating targeted probe entry.")
            clean_digits = re.sub(r"[^0-9]", "", target)
            cam_num = int(clean_digits) if clean_digits else 6
            cameras = [{
                "id": f"cam{cam_num:02d}",
                "camera_id": f"CAM-SENTINEL-{cam_num:02d}",
                "location": f"Camera {cam_num:02d}",
                "stream_url": f"https://{args.host}/cam{cam_num:02d}/index.m3u8",
                "format": "hls",
            }]

    fieldnames = [
        "camera_id", "location", "codec", "live", "width", "height", "plates",
        "vehicles", "best_plate_conf", "largest_plate_px", "verdict",
        "transport", "endpoint", "rtsp", "sampled_frames", "plate_detection_frames", "error_detail"
    ]

    # Load existing report rows to prevent wiping out other cameras' data on partial runs
    existing_report = {}
    if os.path.exists(REPORT_PATH):
        try:
            with open(REPORT_PATH, "r", newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    existing_report[row.get("camera_id")] = row
        except Exception:
            pass

    rows_dict = dict(existing_report)

    for i, cam in enumerate(cameras, 1):
        cam_id = str(_pick(cam, "id", "camera_id", default=f"idx{i}"))
        location = _pick(cam, "location", "location_name", "name", default="")
        codec = _pick(cam, "codec", "video_codec", default="h264")
        live = _pick(cam, "status", "live", default="offline")
        stream_url = _pick(cam, "stream_url", default="")
        format_type = _pick(cam, "format", default="hls")

        # Resolve numerical ID if present
        clean_num = re.sub(r"[^0-9]", "", cam_id)
        num_int = int(clean_num) if clean_num else i

        # Build endpoint URLs
        rtsp = None if args.hls_only else f"rtsp://{args.host}:8554/stream/{num_int}"
        if stream_url and stream_url.startswith("http"):
            hls = stream_url
        else:
            hls = f"https://{args.host}/cam{num_int:02d}/index.m3u8"
        local_file = stream_url if (stream_url and os.path.exists(stream_url)) else None

        if format_type == "file" and not args.include_local and not args.only:
            continue

        print(f"\n[{i}/{len(cameras)}] camera={cam_id} | {location}")

        video_evidence_path = os.path.join(OUTPUT_DIR, f"cam_{cam_id}_evidence.mp4") if args.record_video else None

        frame, w, h, transport, sampled_f, plate_f, sampled_score, best_box, detail = grab_snapshot(
            rtsp_url=rtsp,
            hls_url=hls,
            local_path=local_file,
            model=model,
            sample_seconds=args.sample_seconds,
            retries=args.retries,
            record_video_path=video_evidence_path,
        )

        n_plates, n_vehicles, best_conf, largest_px = sampled_score
        verdict = viability(n_plates, largest_px) if (frame is not None and model) else ("UNREACHABLE" if frame is None else "SCORED")

        if frame is not None:
            snap_path = os.path.join(OUTPUT_DIR, f"cam_{cam_id}.jpg")
            annotated_frame = frame.copy()
            if best_box is not None:
                x1, y1, x2, y2, plate_conf = best_box
                fh_box, fw_box = frame.shape[:2]
                x1 = max(0, min(x1, fw_box - 1))
                y1 = max(0, min(y1, fh_box - 1))
                x2 = max(x1 + 1, min(x2, fw_box))
                y2 = max(y1 + 1, min(y2, fh_box))
                cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
                crop_path = os.path.join(OUTPUT_DIR, f"cam_{cam_id}_plate_crop.jpg")
                cv2.imwrite(crop_path, frame[y1:y2, x1:x2])
                print(f"    -> Saved plate crop: {crop_path} (conf={plate_conf:.3f}, {x2-x1}px)")
            cv2.imwrite(snap_path, annotated_frame)
            print(f"    -> Saved annotated snapshot: {snap_path}")

        row = {
            "camera_id": cam_id,
            "location": location,
            "codec": codec,
            "live": live,
            "width": w,
            "height": h,
            "plates": n_plates,
            "vehicles": n_vehicles,
            "best_plate_conf": best_conf,
            "largest_plate_px": largest_px,
            "verdict": verdict,
            "transport": transport,
            "endpoint": local_file or hls or rtsp,
            "sampled_frames": sampled_f,
            "plate_detection_frames": plate_f,
            "error_detail": detail,
        }
        rows_dict[cam_id] = row
        print(f"    -> Verdict: {verdict} | Detail: {detail}")

        # Incremental save to CSV
        with open(REPORT_PATH, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows_dict.values())

    # Final sorted save: GOOD -> MARGINAL -> POOR -> NONE -> UNREACHABLE
    final_rows = list(rows_dict.values())
    final_rows.sort(
        key=lambda r: (
            1 if r.get("verdict", "").startswith("GOOD") else
            (2 if r.get("verdict", "").startswith("MARGINAL") else
             (3 if r.get("verdict", "").startswith("POOR") else
              (4 if r.get("verdict", "").startswith("NONE") else 5))),
            -int(r.get("largest_plate_px", 0)),
        )
    )
    with open(REPORT_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(final_rows)

    print(f"\n{'='*70}")
    print(f"Survey Finished. Report saved to: {REPORT_PATH}")
    print(f"Evidence snapshots & video clips saved in: {OUTPUT_DIR}/")
    good = [r for r in final_rows if r.get("verdict", "").startswith("GOOD")]
    marginal = [r for r in final_rows if r.get("verdict", "").startswith("MARGINAL")]
    print(f"Summary: {len(good)} GOOD, {len(marginal)} MARGINAL out of {len(final_rows)} total cameras recorded.")


if __name__ == "__main__":
    main()
