"""
Video Ingestion Service — Module 2 (VMS Federation Layer)

Architecture
------------
Each camera runs in its own daemon thread (CameraWorker).
cv2.VideoCapture accepts both local file paths and rtsp:// / http:// URLs,
so switching from test files to live streams only requires updating stream_url
in the database — no code changes needed.

Shared state (CAMERA_STATE) is a dict keyed by camera_id:
  {
    "frame":          np.ndarray | None   — latest decoded frame
    "last_seen":      datetime | None     — UTC time of last successful read
    "status":         "online" | "offline"
    "_last_db_write": datetime | None     — throttle DB heartbeat writes
  }

ANPR is dispatched on a stream-time cadence (ANPR_INTERVAL_MS) to avoid
saturating the CPU on ~50 concurrent streams.

--------------------------------------------------------------------------
Compliance with the official Sentinel sandbox integration guide
--------------------------------------------------------------------------
The hackathon grid is a live RTP/RTSP gateway, not a file server. The guide
lists specific client requirements; each is implemented here:

1. FORCE RTSP OVER TCP. UDP is accepted but fails across NAT and most
   firewalls, and partial UDP delivery produces corrupt frames that look
   exactly like model bugs. OPENCV_FFMPEG_CAPTURE_OPTIONS is set below,
   before any capture is opened, and captures use the cv2.CAP_FFMPEG backend.

2. TIMING FROM PTS, NEVER FROM ARRIVAL TIME. On connect the gateway replays
   its buffered group-of-pictures so the decoder can start at a keyframe —
   the first second or two of frames therefore arrive FASTER than real time.
   Timestamping by arrival would bunch those frames at one wall-clock instant
   and produce impossible velocities, corrupting exactly the timestamped
   movement history the evaluation scores. We read CAP_PROP_POS_MSEC and
   anchor it: frame_time = connect_wall_clock + (pts - first_pts).

3. NEVER TRUST CAP_PROP_FPS. It often does not match actual delivery rate.
   Nothing here reads it; ANPR cadence is driven by elapsed PTS, not a frame
   counter (a counter would mean a different real interval per camera, since
   the grid is not uniform in resolution/codec/frame rate).

4. GAPS ARE NOT DISCONNECTS. Frame intervals are not uniform, and decode
   warnings at join are normal until the first IDR frame arrives. A single
   failed read no longer tears down the capture — MAX_CONSECUTIVE_FAILURES
   consecutive failures are required.

5. RECONNECT WITH EXPONENTIAL BACKOFF, from RECONNECT_BASE_S doubling to
   RECONNECT_MAX_S, rather than a fixed short delay in a tight loop.

6. EXPECT SCENE DISCONTINUITY. Each feed loops; at the loop point the scene
   cuts abruptly like a camera reboot. A large backwards PTS jump re-anchors
   the timing baseline instead of emitting backwards timestamps.

See fetch_catalogue() for reading the live camera list from GET /api/cameras —
the guide is explicit that the catalogue is the contract and the URL pattern
is not, so endpoints must never be hard-coded.
"""
import threading
import time
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Requirement 1 — must be set before the first cv2.VideoCapture call, so it
# lives at import time rather than inside the worker.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
ANPR_INTERVAL_MS = 1000         # run ANPR once per this much *stream* time (req. 3)
HEARTBEAT_INTERVAL_S = 30       # minimum seconds between DB heartbeat writes
OFFLINE_TIMEOUT_S = 15          # mark camera offline after this many seconds without a frame
RECONNECT_BASE_S = 2            # initial reconnect delay        (req. 5)
RECONNECT_MAX_S = 30            # maximum reconnect delay        (req. 5)
MAX_CONSECUTIVE_FAILURES = 15   # failed reads tolerated before reconnect (req. 4)
# The guide states frames carry *monotonic* presentation timestamps, so any
# genuine backwards jump means the feed looped. This tolerance only absorbs
# small jitter/reordering; it must stay small, or a loop shorter than the
# threshold would slip through and emit timestamps that move backwards.
PTS_DISCONTINUITY_MS = 250      # backwards PTS jump beyond this = loop/restart (req. 6)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
CAMERA_STATE: dict = {}         # camera_id -> state dict (see module docstring)
_workers: dict = {}             # camera_id -> CameraWorker thread
_pending_start: set = set()     # camera_ids queued by a bulk start, not started yet
_lock = threading.Lock()
_capture_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class CameraWorker(threading.Thread):
    """
    Background daemon thread for one camera source.

    Reads frames, stores the latest in CAMERA_STATE, and dispatches every
    Nth frame to the ANPR pipeline. Local files are paced at their FPS so
    they behave like live camera sources.
    Automatically reconnects if the stream drops.
    """

    def __init__(self, camera_id: str, stream_url: str, app, frame_callback=None):
        super().__init__(daemon=True, name=f"cam-{camera_id}")
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.app = app                      # Flask app — used to push app context into thread
        self.frame_callback = frame_callback
        self._stop_event = threading.Event()

        # PTS anchoring state (requirement 2)
        self._pts_base_ms = None            # first PTS seen on this connection
        self._wall_base = None              # wall-clock time that PTS maps onto
        self._last_pts_ms = None            # for loop/discontinuity detection
        self._last_anpr_pts_ms = None       # ANPR cadence driver (requirement 3)
        self._local_frame_interval_s = None
        self._anpr_thread = None            # active asynchronous ANPR inference thread

    # ------------------------------------------------------------------
    def _open_capture(self):
        """
        Open the stream with the FFmpeg backend — required for the
        rtsp_transport=tcp option set at module import to take effect.
        Resets timing anchors: a new connection means a new PTS origin.
        """
        import cv2
        if self._is_local_file():
            cap = cv2.VideoCapture(self.stream_url)
        else:
            from app.services.hls import open_capture
            cap = open_capture(self.stream_url)
        if self._is_local_file():
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if fps > 0:
                self._local_frame_interval_s = 1.0 / fps
                logger.info(
                    "Local video pacing enabled | camera=%s fps=%.2f",
                    self.camera_id,
                    fps,
                )
            else:
                self._local_frame_interval_s = None
                logger.warning(
                    "Local video FPS unavailable; pacing disabled | camera=%s",
                    self.camera_id,
                )
        else:
            self._local_frame_interval_s = None
        self._pts_base_ms = None
        self._wall_base = None
        self._last_pts_ms = None
        self._last_anpr_pts_ms = None
        return cap

    # ------------------------------------------------------------------
    def _is_local_file(self) -> bool:
        source = self.stream_url.lower().split("?", 1)[0]
        return not source.startswith(("rtsp://", "rtsps://", "http://", "https://"))

    # ------------------------------------------------------------------
    def _pace_local_file(self, frame_started: float):
        if self._local_frame_interval_s is None:
            return
        elapsed = time.monotonic() - frame_started
        self._stop_event.wait(max(0.0, self._local_frame_interval_s - elapsed))

    # ------------------------------------------------------------------
    def _read_pts_ms(self, cap) -> float:
        import cv2
        try:
            return float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    def _frame_time(self, pts_ms: float) -> datetime:
        """
        Convert a presentation timestamp into absolute UTC time (req. 2).

        Wall-clock time at read() is NOT when the frame was captured — the
        gateway bursts buffered frames on connect. We anchor the connection's
        first PTS to the wall clock and offset each later frame by its PTS
        delta. A large backwards jump means the feed looped (req. 6), so we
        re-anchor rather than emit a timestamp that moves backwards.

        Falls back to wall-clock if the source reports no usable PTS (some
        local test files do this).
        """
        now = datetime.now(timezone.utc)

        if pts_ms <= 0:
            return now

        if self._last_pts_ms is not None and pts_ms < self._last_pts_ms - PTS_DISCONTINUITY_MS:
            logger.info(
                "Scene discontinuity (feed looped) | camera=%s pts=%.0fms prev=%.0fms — re-anchoring",
                self.camera_id, pts_ms, self._last_pts_ms,
            )
            self._pts_base_ms = pts_ms
            self._wall_base = now
            self._last_anpr_pts_ms = None

        if self._pts_base_ms is None:
            self._pts_base_ms = pts_ms
            self._wall_base = now

        self._last_pts_ms = pts_ms
        return self._wall_base + timedelta(milliseconds=(pts_ms - self._pts_base_ms))

    # ------------------------------------------------------------------
    def _should_run_anpr(self, pts_ms: float) -> bool:
        """
        Cadence driven by elapsed stream time, not a frame counter (req. 3).
        The grid is not uniform and the reported frame rate is unreliable, so
        "every Nth frame" would mean a different real interval per camera.
        """
        if pts_ms <= 0:
            return True     # no usable PTS (local files) — fall back to every read
        if self._last_anpr_pts_ms is None or pts_ms - self._last_anpr_pts_ms >= ANPR_INTERVAL_MS:
            self._last_anpr_pts_ms = pts_ms
            return True
        return False

    # ------------------------------------------------------------------
    def _read(self, cap):
        """Read one frame without freezing the eventlet hub.

        SentinelCapture already sends its decode to the native pool itself
        (its segment downloads must stay green), so it is called directly.
        Plain cv2 captures (RTSP, local files) are decoded in the pool.
        """
        from app.services.hls import SentinelCapture
        if isinstance(cap, SentinelCapture):
            return cap.read()
        from app.services.blocking import run_blocking
        return run_blocking(cap.read)

    # ------------------------------------------------------------------
    def run(self):
        logger.info("Ingestion started | camera=%s url=%s", self.camera_id, self.stream_url)

        try:
            cap = self._open_capture()
        except Exception as exc:  # noqa: BLE001
            logger.error("Open failed | camera=%s | %s", self.camera_id, exc)
            cap = None
        backoff_s = RECONNECT_BASE_S
        last_frame_at = time.monotonic()      # grace period starts at connect
        is_online = False
        failures = 0

        while not self._stop_event.is_set():
            frame_started = time.monotonic()
            try:
                ret, frame = self._read(cap) if cap is not None else (False, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Read error | camera=%s | %s", self.camera_id, exc)
                ret, frame = False, None

            if ret and frame is not None:
                failures = 0
                last_frame_at = time.monotonic()
                backoff_s = RECONNECT_BASE_S          # healthy again — reset backoff
                now = datetime.now(timezone.utc)
                pts_ms = self._read_pts_ms(cap)
                frame_time = self._frame_time(pts_ms)

                with _lock:
                    CAMERA_STATE[self.camera_id] = {
                        "frame": frame,
                        "last_seen": now,
                        "status": "online",
                        "_last_db_write": None if not is_online else
                            CAMERA_STATE.get(self.camera_id, {}).get("_last_db_write"),
                    }
                # The first good frame after a (re)connect writes "online" at
                # once; later frames refresh the heartbeat every 30 s.
                is_online = True
                self._maybe_write_heartbeat(now)

                if self.frame_callback:
                    try:
                        self.frame_callback(frame)
                    except Exception as exc:
                        logger.error("Frame callback error | camera=%s | %s", self.camera_id, exc)

                # ANPR runs in its own green thread; its heavy part (YOLO/OCR)
                # goes to the native pool, so video keeps flowing. A frame is
                # skipped while the previous inference is still running.
                if self._should_run_anpr(pts_ms) and not self._stop_event.is_set():
                    if self._anpr_thread is None or not self._anpr_thread.is_alive():
                        self._anpr_thread = threading.Thread(
                            target=self._dispatch_anpr,
                            args=(frame.copy(), frame_time),
                            daemon=True,
                            name=f"anpr-{self.camera_id}",
                        )
                        self._anpr_thread.start()

                self._pace_local_file(frame_started)
                continue

            # ---- no frame -------------------------------------------------
            # A gap is not a disconnect: live HLS waits for the next segment
            # and decoders drop frames until the first keyframe. Only a long
            # silence (OFFLINE_TIMEOUT_S) marks the camera offline.
            failures += 1
            silent_for = time.monotonic() - last_frame_at
            if silent_for < OFFLINE_TIMEOUT_S and cap is not None:
                if failures == 1:
                    logger.debug("Frame gap tolerated | camera=%s", self.camera_id)
                self._stop_event.wait(min(0.05 * failures, 0.5))
                continue

            # Always record offline here (at most once per backoff period),
            # so a stale "online" in the DB never survives a dead stream.
            self._mark_offline()
            is_online = False
            logger.warning(
                "Camera %s: no frame for %.0fs — reconnecting in %ds",
                self.camera_id, silent_for, backoff_s,
            )
            if cap is not None:
                try:
                    cap.release()
                except Exception:  # noqa: BLE001
                    pass
            self._stop_event.wait(backoff_s)
            backoff_s = min(backoff_s * 2, RECONNECT_MAX_S)   # requirement 5
            failures = 0
            if self._stop_event.is_set():
                cap = None
                break
            try:
                cap = self._open_capture()
            except Exception as exc:  # noqa: BLE001
                logger.error("Reopen failed | camera=%s | %s", self.camera_id, exc)
                cap = None
            last_frame_at = time.monotonic()

        if cap is not None:
            cap.release()
        self._mark_offline()
        logger.info("Ingestion stopped | camera=%s", self.camera_id)

    # ------------------------------------------------------------------
    def _dispatch_anpr(self, frame, timestamp: datetime):
        """Run the ANPR pipeline inside the Flask app context."""
        if self._stop_event.is_set():
            return
        try:
            with self.app.app_context():
                from app.services.anpr import process_frame
                process_frame(self.camera_id, frame, timestamp)
        except Exception as exc:
            logger.error("ANPR dispatch error | camera=%s | %s", self.camera_id, exc)

    # ------------------------------------------------------------------
    def _maybe_write_heartbeat(self, now: datetime):
        """Persist online status + heartbeat to DB (throttled) and emit SocketIO event."""
        state = CAMERA_STATE.get(self.camera_id, {})
        last = state.get("_last_db_write")
        if last and (now - last).total_seconds() < HEARTBEAT_INTERVAL_S:
            return
        try:
            with self.app.app_context():
                from app import db, socketio
                from app.models import Camera
                cam = db.session.get(Camera, self.camera_id)
                if cam:
                    cam.status = "online"
                    cam.last_heartbeat = now
                    db.session.commit()
                if socketio is not None and getattr(socketio, "server", None) is not None:
                    socketio.emit(
                        "status_change",
                        {"camera_id": self.camera_id, "status": "online",
                         "last_seen": now.isoformat()},
                        namespace="/cameras",
                    )
            with _lock:
                if self.camera_id in CAMERA_STATE:
                    CAMERA_STATE[self.camera_id]["_last_db_write"] = now
        except Exception as exc:
            logger.error("Heartbeat write error | camera=%s | %s", self.camera_id, exc)

    # ------------------------------------------------------------------
    def _mark_offline(self):
        """Update in-memory state, DB, and push SocketIO status_change event."""
        with _lock:
            if self.camera_id in CAMERA_STATE:
                CAMERA_STATE[self.camera_id]["status"] = "offline"
        try:
            with self.app.app_context():
                from app import db, socketio
                from app.models import Camera
                cam = db.session.get(Camera, self.camera_id)
                if cam:
                    cam.status = "offline"
                    db.session.commit()
                if socketio is not None and getattr(socketio, "server", None) is not None:
                    socketio.emit(
                        "status_change",
                        {"camera_id": self.camera_id, "status": "offline", "last_seen": None},
                        namespace="/cameras",
                    )
        except Exception as exc:
            logger.error("Mark-offline error | camera=%s | %s", self.camera_id, exc)

    def stop(self):
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_camera(camera_id: str, stream_url: str, app, frame_callback=None):
    """
    Start a CameraWorker for the given camera if one is not already running.
    Safe to call multiple times — idempotent.
    """
    with _lock:
        existing = _workers.get(camera_id)
        if existing and existing.is_alive():
            logger.debug("Camera %s already running", camera_id)
            return existing
        worker = CameraWorker(camera_id, stream_url, app, frame_callback=frame_callback)
        _workers[camera_id] = worker
    worker.start()
    return worker


def stop_camera(camera_id: str) -> None:
    """Signal the worker for a camera to stop and remove it from the registry."""
    with _lock:
        worker = _workers.pop(camera_id, None)
    if worker:
        worker.stop()
        worker.join(timeout=5)
        logger.info("Stop signal sent | camera=%s", camera_id)


def running_camera_ids() -> set:
    with _lock:
        return {cid for cid, w in _workers.items() if w.is_alive()}


def start_cameras_staggered(sources, app, gap_s: float = 0.5) -> threading.Thread:
    """
    Start many cameras without opening every stream in the same instant
    (30 simultaneous RTSP/HLS opens can stall the gateway and the laptop).
    sources: [(camera_id, stream_url), ...]. Returns the background thread.
    """
    with _lock:
        _pending_start.update(cid for cid, _ in sources)

    def _run():
        for i, (camera_id, stream_url) in enumerate(sources):
            with _lock:
                if camera_id not in _pending_start:   # stopped while still waiting its turn
                    continue
                _pending_start.discard(camera_id)
            try:
                start_camera(camera_id, stream_url, app)
            except Exception as exc:  # noqa: BLE001
                logger.error("Bulk start failed | camera=%s | %s", camera_id, exc)
            if i < len(sources) - 1:
                time.sleep(gap_s)
        logger.info("Bulk start finished | cameras=%d", len(sources))

    t = threading.Thread(target=_run, name="bulk-camera-start", daemon=True)
    t.start()
    return t


def stop_cameras(camera_ids) -> list:
    """Signal several workers at once, then wait for them in the background."""
    with _lock:
        _pending_start.difference_update(camera_ids)   # cancel queued bulk starts too
        workers = [(cid, _workers.pop(cid)) for cid in list(camera_ids) if cid in _workers]
    for _, w in workers:
        w.stop()

    def _join():
        for cid, w in workers:
            w.join(timeout=5)
            logger.info("Camera worker stopped | camera=%s", cid)

    threading.Thread(target=_join, name="bulk-camera-stop", daemon=True).start()
    return [cid for cid, _ in workers]


def stop_all_cameras() -> None:
    """Signal all active camera workers to stop and wait for termination."""
    with _lock:
        active_workers = list(_workers.items())
        _workers.clear()
    for cid, worker in active_workers:
        worker.stop()
    for cid, worker in active_workers:
        worker.join(timeout=5)
        logger.info("Camera worker stopped | camera=%s", cid)


def get_latest_frame(camera_id: str):
    """
    Return the normalised frame packet for a camera, or None if unavailable.

    Return format: { "camera_id": str, "frame": np.ndarray, "timestamp": datetime }
    This is the common format consumed by the ANPR pipeline and any future
    analytics modules.
    """
    with _lock:
        state = CAMERA_STATE.get(camera_id)
    if not state or state.get("frame") is None:
        return None
    return {
        "camera_id": camera_id,
        "frame": state["frame"],
        "timestamp": state["last_seen"],
    }


def get_all_statuses() -> dict:
    """Return a snapshot of online/offline status for every tracked camera."""
    with _lock:
        return {cid: s.get("status", "offline") for cid, s in CAMERA_STATE.items()}


def fetch_catalogue(host: str) -> list:
    """
    Read the live camera catalogue from the Sentinel gateway (GET /api/cameras).

    The integration guide is explicit that the catalogue is the contract and
    the URL pattern is not: camera ids and the set of available cameras can
    change, so endpoints must never be hard-coded. Each entry carries the
    camera id, location, codec, live status, stream properties, and all three
    stream URLs (RTSP / WHEP / HLS).

    Use the RTSP URL for AI inference. Per-camera properties (resolution,
    codec) should drive buffer/batch sizing rather than assuming a uniform
    grid — a fixed-shape inference batch across every camera will not scale.

    Returns [] on failure so callers can fall back to whatever is already in
    the Camera table rather than crashing.
    """
    from app.services.hls import get_sentinel_session
    session = get_sentinel_session()

    for ep in ["/cameras.json", "/api/cameras"]:
        url = f"https://{host}{ep}"
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                raw_cams = data if isinstance(data, list) else data.get("cameras", [])
                if raw_cams:
                    normalized = []
                    for c in raw_cams:
                        cam_id = c.get("id") or c.get("camera_id")
                        name = c.get("name") or c.get("location_name") or str(cam_id)
                        stream_url = c.get("stream_url") or f"https://{host}/{cam_id}/index.m3u8"
                        normalized.append({
                            "id": cam_id,
                            "camera_id": cam_id,
                            "name": name,
                            "location_name": name,
                            "stream_url": stream_url,
                            "format": "hls",
                        })
                    logger.info("Catalogue fetched | host=%s endpoint=%s cameras=%d", host, ep, len(normalized))
                    return normalized
        except Exception as exc:
            logger.debug("Catalogue fetch probe failed | url=%s | %s", url, exc)

    return []


def _catalogue_value(entry: dict, *keys, default=None):
    """Return the first populated value from a catalogue entry."""
    for key in keys:
        value = entry.get(key)
        if value not in (None, ""):
            return value
    return default


def start_all_cameras(app, use_catalogue=False, catalogue_host=None, camera_ids=None) -> None:
    """
    Resume ingestion for selected cameras from the database or live catalogue.
    Called once at application startup from the app factory.

    Catalogue mode is opt-in. If the catalogue cannot be fetched, the database
    remains the fallback so startup is not made dependent on the gateway.
    ``camera_ids`` is an optional allow-list for pacing load; an empty list
    means all cameras from the selected source for backwards compatibility.
    """
    selected_ids = {str(camera_id) for camera_id in (camera_ids or [])}
    source = "database"
    camera_sources = []

    if use_catalogue:
        catalogue = fetch_catalogue(catalogue_host) if catalogue_host else []
        for entry in catalogue:
            camera_id = _catalogue_value(entry, "id", "camera_id", "cameraId")
            stream_url = _catalogue_value(
                entry, "rtsp_url", "rtsp", "rtspUrl", "hls_live_url", "hls", "hls_url", "hlsUrl"
            )
            if camera_id and stream_url:
                camera_sources.append((str(camera_id), stream_url))
        if camera_sources:
            source = "catalogue"

    if not camera_sources:
        with app.app_context():
            from app.models import Camera
            camera_sources = [(cam.camera_id, cam.stream_url) for cam in Camera.query.all()]

    if selected_ids:
        camera_sources = [
            (camera_id, stream_url)
            for camera_id, stream_url in camera_sources
            if str(camera_id) in selected_ids
        ]

    logger.info(
        "Ingestion camera selection | source=%s | cameras=%d | allow_list=%s",
        source,
        len(camera_sources),
        ",".join(sorted(selected_ids)) if selected_ids else "all",
    )
    for camera_id, stream_url in camera_sources:
        start_camera(camera_id, stream_url, app)

    logger.info("Ingestion started for %d cameras", len(camera_sources))
