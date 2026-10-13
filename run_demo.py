"""Run a single-camera demo and print accepted ANPR detections.

Modes:
    own         local close-range video + exact plate (own-feed demo)
    recorded    loop a RECORDED clip (e.g. a saved Sentinel camera clip) - works
                with no network at all; plate is optional
    government  live Sentinel camera. Runs a pre-flight check first; if the grid
                is down / login fails and --fallback-video is given, it switches
                to the recorded clip automatically so the demo never goes blank.

Examples:
    python run_demo.py own --video test_videos/my_car.mp4 --plate GJ01AB1234
    python run_demo.py recorded --video clips/sentinel_cam06_clip.mp4 --plate GJ11AB1234
    python run_demo.py government --camera-id 6 --fallback-video clips/sentinel_cam06_clip.mp4
"""
import argparse
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app import create_app, db
from app.models import Alert, Camera, Detection, Watchlist
from app.services import anpr
from app.services.video_ingestion import start_camera, stop_camera

PLATE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$")
DEMO_CAMERA_ID = "DEMO-CAMERA-001"
RECORDED_CAMERA_ID = "DEMO-RECORDED-001"
GOV_STREAM_BASE_URL = "https://cctv.corp8.cloud"


def upsert_camera(camera_id, stream_url, location, camera_format):
    camera = db.session.get(Camera, camera_id)
    if camera is None:
        camera = Camera(
            camera_id=camera_id,
            department="Hackathon Demo",
            location_name=location,
            latitude=None,
            longitude=None,
            vendor="Demo Feed",
            stream_url=stream_url,
            format=camera_format,
            status="offline",
        )
        db.session.add(camera)
    else:
        camera.stream_url = stream_url
        camera.format = camera_format
        camera.location_name = location
    db.session.commit()


def ensure_watchlist(plate):
    item = Watchlist.query.filter_by(type="vehicle", value=plate).first()
    if item is None:
        item = Watchlist(
            type="vehicle",
            value=plate,
            reason="Own-feed demo verification",
        )
        db.session.add(item)
        db.session.commit()
        print(f"WATCHLIST ADDED | plate={plate} id={item.watchlist_id}")
    else:
        print(f"WATCHLIST EXISTS | plate={plate} id={item.watchlist_id}")


def sentinel_preflight(camera_number, timeout=8):
    """
    Cheap go/no-go check for the live Sentinel grid: log in, fetch one playlist.
    Returns (ok, reason). Never raises - a demo must not die in its own pre-check.
    """
    try:
        from app.services.hls import get_sentinel_session

        session = get_sentinel_session(force_refresh=True)
        if not session.cookies.get("sentinel"):
            return False, "login failed (no session cookie) - check SENTINEL_EMAIL/SENTINEL_PASSWORD in .env"
        url = f"{GOV_STREAM_BASE_URL}/cam{int(camera_number):02d}/index.m3u8"
        resp = session.get(url, timeout=timeout)
        if resp.status_code != 200:
            return False, f"playlist HTTP {resp.status_code} (upstream down / blocked?)"
        if "#EXTM3U" not in resp.text[:512]:
            return False, "playlist response is not valid HLS"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        return False, f"{type(exc).__name__}: {exc}"


def monitor(camera_id, duration, baseline_detection_id, baseline_alert_id):
    deadline = time.monotonic() + duration
    seen_detections = set()
    seen_alerts = set()
    print(f"RUNNING | camera={camera_id} duration={duration}s")
    while time.monotonic() < deadline:
        detections = (
            Detection.query.filter(
                Detection.camera_id == camera_id,
                Detection.detection_id > baseline_detection_id,
            )
            .order_by(Detection.detection_id.asc())
            .all()
        )
        for detection in detections:
            if detection.detection_id in seen_detections:
                continue
            seen_detections.add(detection.detection_id)
            if PLATE_RE.fullmatch(detection.plate_number or ""):
                print(
                    "VALID DETECTION | id=%s plate=%s timestamp=%s confidence=%.3f"
                    % (
                        detection.detection_id,
                        detection.plate_number,
                        detection.timestamp.isoformat(),
                        detection.confidence or 0.0,
                    )
                )

        alerts = (
            Alert.query.filter(
                Alert.camera_id == camera_id,
                Alert.alert_id > baseline_alert_id,
            )
            .order_by(Alert.alert_id.asc())
            .all()
        )
        for alert in alerts:
            if alert.alert_id in seen_alerts:
                continue
            seen_alerts.add(alert.alert_id)
            print(
                "WATCHLIST ALERT | id=%s plate=%s timestamp=%s"
                % (alert.alert_id, alert.matched_value, alert.timestamp.isoformat())
            )
        db.session.remove()
        time.sleep(2)

    print(
        "FINISHED | new_valid_detections=%d new_alerts=%d"
        % (
            sum(
                1
                for detection in Detection.query.filter(
                    Detection.camera_id == camera_id,
                    Detection.detection_id > baseline_detection_id,
                ).all()
                if PLATE_RE.fullmatch(detection.plate_number or "")
            ),
            Alert.query.filter(
                Alert.camera_id == camera_id,
                Alert.alert_id > baseline_alert_id,
            ).count(),
        )
    )


def run(args):
    app = create_app(config_object="config.TestingConfig")
    with app.app_context():
        mode = args.mode

        # --- Live grid pre-flight with automatic fallback to a recorded clip ---
        if mode == "government" and not getattr(args, "skip_preflight", False):
            num = str(args.camera_id).strip()
            num = int(num) if num.isdigit() else 6
            ok, reason = sentinel_preflight(num)
            print(f"PREFLIGHT | sentinel cam{num:02d} ok={ok} reason={reason}")
            fallback = getattr(args, "fallback_video", None) or os.environ.get("DEMO_FALLBACK_VIDEO")
            if not ok and fallback:
                if not os.path.exists(fallback):
                    raise SystemExit(f"Live grid unavailable AND fallback video not found: {fallback}")
                print(f"FALLBACK | live grid unavailable -> replaying recorded clip: {fallback}")
                mode = "recorded"
                args.video = fallback
                args.plate = getattr(args, "fallback_plate", None)
            elif not ok:
                print("WARNING | continuing without fallback (pass --fallback-video <clip> to be safe)")

        use_video = mode in ("own", "recorded")

        if use_video:
            if not os.path.exists(args.video):
                raise SystemExit(f"Video not found: {args.video}")
            plate = (args.plate or "").strip().upper() or None
            if mode == "own" and plate is None:
                raise SystemExit("own mode needs --plate (the exact plate visible in the video)")
            if plate and not PLATE_RE.fullmatch(plate):
                raise SystemExit(f"Invalid Indian plate format: {plate}")
            camera_id = DEMO_CAMERA_ID if mode == "own" else RECORDED_CAMERA_ID
            label = "Own-feed demo" if mode == "own" else "Recorded-clip demo (Sentinel replay)"
            upsert_camera(camera_id, args.video, label, "file")
            if plate:
                ensure_watchlist(plate)
            stream_url = args.video
        else:
            camera_number = str(args.camera_id).strip()
            num_int = int(camera_number) if camera_number.isdigit() else 6
            camera_id = f"CAM-SENTINEL-{num_int:02d}"
            stream_url = f"{GOV_STREAM_BASE_URL}/cam{num_int:02d}/index.m3u8"
            upsert_camera(camera_id, stream_url, f"Sentinel Camera {num_int:02d}", "hls")

        baseline_detection_id = db.session.query(db.func.max(Detection.detection_id)).filter(
            Detection.camera_id == camera_id
        ).scalar() or 0
        baseline_alert_id = db.session.query(db.func.max(Alert.alert_id)).filter(
            Alert.camera_id == camera_id
        ).scalar() or 0
        print(
            f"BASELINE | detection_id={baseline_detection_id} alert_id={baseline_alert_id} "
            f"started={datetime.now(timezone.utc).isoformat()}"
        )
        print("WARMING UP | YOLO + EasyOCR")
        anpr.warm_up()
        writer = None
        render_thread = None
        if use_video:
            import cv2
            capture = cv2.VideoCapture(stream_url)
            if not capture.isOpened():
                capture.release()
                raise SystemExit(f"Could not open demo video: {stream_url}")
            fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            capture.release()
            if width <= 0 or height <= 0:
                raise SystemExit(f"Could not read demo video dimensions: {stream_url}")
            output_dir = "demo_output"
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(
                output_dir,
                f"{os.path.splitext(os.path.basename(stream_url))[0]}_annotated.mp4",
            )
            writer = cv2.VideoWriter(
                output_path,
                cv2.VideoWriter_fourcc(*"mp4v"),
                fps,
                (width, height),
            )
            if not writer.isOpened():
                writer.release()
                raise SystemExit(f"Could not create annotated video: {output_path}")
            print(f"ANNOTATED VIDEO | path={output_path}")

            def render_video():
                render_capture = cv2.VideoCapture(stream_url)
                try:
                    target_frames = max(1, round(args.duration * fps))
                    for _ in range(target_frames):
                        ok, frame = render_capture.read()
                        if not ok:
                            break
                        writer.write(anpr.annotate_frame(frame, camera_id))
                finally:
                    render_capture.release()

            render_thread = threading.Thread(target=render_video, name="demo-video-writer", daemon=True)
            render_thread.start()

        worker = start_camera(camera_id, stream_url, app, frame_callback=lambda frame: None)
        try:
            monitor(camera_id, args.duration, baseline_detection_id, baseline_alert_id)
        finally:
            stop_camera(camera_id)
            if render_thread is not None:
                render_thread.join()
            if writer is not None:
                writer.release()
            print("STOPPED | camera=%s" % camera_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Single-camera ANPR demo runner")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    own = subparsers.add_parser("own", help="Run a local close-range video")
    own.add_argument("--video", required=True, help="Path to the recorded video")
    own.add_argument("--plate", required=True, help="Exact plate visible in the video")
    own.add_argument("--duration", type=int, default=180)

    recorded = subparsers.add_parser("recorded", help="Loop a recorded clip (no network needed)")
    recorded.add_argument("--video", required=True, help="Path to the recorded clip")
    recorded.add_argument("--plate", default=None, help="Optional plate to put on the watchlist")
    recorded.add_argument("--duration", type=int, default=180)

    government = subparsers.add_parser("government", help="Run live Sentinel Camera 6")
    government.add_argument("--duration", type=int, default=900)
    government.add_argument("--fallback-video", default=None,
                            help="Recorded clip to replay if the live grid fails the pre-flight check "
                                 "(or set DEMO_FALLBACK_VIDEO)")
    government.add_argument("--fallback-plate", default=None,
                            help="Watchlist plate to use with the fallback clip")
    government.add_argument("--skip-preflight", action="store_true",
                            help="Skip the live-grid pre-flight check")
    government.add_argument(
        "--camera-id",
        default="6",
        help="Sentinel camera number to stream (default: 6)",
    )

    run(parser.parse_args())
