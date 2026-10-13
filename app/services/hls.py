"""
hls.py — Sentinel HLS stream decryption & OpenCV capture adapter
================================================================
Handles:
1. Automated login session handshake to https://cctv.corp8.cloud/auth/login
2. Fetching & parsing HLS playlists (.m3u8) with AES-128 encryption tags
3. Key retrieval (/enc.key) and per-segment AES-128-CBC decryption via cryptography
4. OpenCV-compatible SentinelCapture reader (isOpened, read, get, release)
5. open_capture() factory with transparent fallback to cv2.VideoCapture for local files & RTSP
"""
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import cv2
import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from config import Config

logger = logging.getLogger(__name__)

# Global session cache & lock
_session_lock = threading.Lock()
_cached_session = None
_cached_session_time = 0.0
SESSION_TTL_SECONDS = 3600  # Refresh session every hour


def get_sentinel_session(force_refresh: bool = False) -> requests.Session:
    """Return an authenticated requests.Session for cctv.corp8.cloud."""
    global _cached_session, _cached_session_time

    with _session_lock:
        now = time.time()
        if not force_refresh and _cached_session is not None and (now - _cached_session_time) < SESSION_TTL_SECONDS:
            return _cached_session

        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://cctv.corp8.cloud/auth/login",
            "Origin": "https://cctv.corp8.cloud",
        })

        email = Config.SENTINEL_EMAIL
        password = Config.SENTINEL_PASSWORD

        if email and password:
            try:
                resp = session.post(
                    "https://cctv.corp8.cloud/auth/login",
                    data={"email": email, "password": password},
                    allow_redirects=True,
                    timeout=10,
                )
                cookie_val = session.cookies.get("sentinel")
                if cookie_val:
                    logger.info("Successfully authenticated with Sentinel grid (cookie present)")
                    _cached_session = session
                    _cached_session_time = now
                    return _cached_session
                else:
                    logger.warning(
                        f"Login to Sentinel grid returned status {resp.status_code} without sentinel cookie"
                    )
            except requests.RequestException as e:
                logger.error(f"Failed to connect to Sentinel auth endpoint: {e}")

        # Fallback to unauthenticated session with browser headers
        _cached_session = session
        _cached_session_time = now
        return _cached_session


class SentinelCapture:
    """
    OpenCV VideoCapture-compatible reader for (AES-128 encrypted) HLS streams.

    LIVE streams: the playlist is a sliding window - new segments appear at the
    end and old ones are deleted from the server within a minute or two. The
    playlist is therefore re-read whenever the queued segments run out, and only
    segments with a media-sequence number newer than the last one played are
    queued. (The earlier version read the playlist once and looped those
    segments, so a camera worked for a minute and then went dark.)
    VOD playlists (#EXT-X-ENDLIST) loop, which suits recorded demo clips.

    Frame decoding runs through run_blocking() so a busy camera does not freeze
    the server; segment downloads stay on the green (I/O) side.
    """

    LIVE_START_SEGMENTS = 3        # start this many segments behind the live edge
    MIN_REFRESH_S = 1.0            # do not hammer the playlist faster than this

    def __init__(self, stream_url: str, session: requests.Session = None):
        self.stream_url = stream_url
        self.playlist_url = stream_url
        self.session = session or get_sentinel_session()
        self._is_opened = False
        self._current_cap = None
        self._queue = []               # [(seq, seg_uri, key_uri, iv_bytes|None)]
        self._vod = []                 # full list for looping VOD playlists
        self._endlist = False
        self._last_seq = -1
        self._last_refresh = 0.0
        self._keys = {}                # key_uri -> 16 bytes
        self._temp_dir = tempfile.mkdtemp(prefix="sentinel_cap_")
        self._slot = 0
        self._frame_count = 0
        self._fps = 25.0
        self._width = 0
        self._height = 0
        self._closed = False
        self._lock = threading.Lock()
        self._init_stream()

    # -- helpers ---------------------------------------------------------
    def _fetch_with_auth_retry(self, url: str, timeout: float = 15):
        """Fetch URL using session; refresh the login and retry once on 401/403."""
        self.session = get_sentinel_session()
        resp = self.session.get(url, timeout=timeout)
        if resp.status_code in (401, 403):
            logger.info(f"Received HTTP {resp.status_code} fetching {url}; forcing session re-login...")
            self.session = get_sentinel_session(force_refresh=True)
            resp = self.session.get(url, timeout=timeout)
        return resp

    def _abs(self, uri: str, base: str) -> str:
        if uri.startswith("http"):
            return uri
        if uri.startswith("/"):
            parts = urlsplit(base)
            return f"{parts.scheme}://{parts.netloc}{uri}"
        return f"{base.rsplit('/', 1)[0]}/{uri}"

    @staticmethod
    def parse_playlist(text: str):
        """
        Returns (entries, endlist, variant_uri).
        entries: [(seq, seg_uri, key_uri|None, iv|None)]; variant_uri is set for a
        master playlist (then entries is empty).
        """
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        media_seq = 0
        key_uri, key_iv = None, None
        entries, variant = [], None
        expect_variant = False
        for line in lines:
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                try:
                    media_seq = int(line.split(":", 1)[1])
                except ValueError:
                    media_seq = 0
            elif line.startswith("#EXT-X-STREAM-INF"):
                expect_variant = True
            elif line.startswith("#EXT-X-KEY:"):
                if "METHOD=NONE" in line:
                    key_uri, key_iv = None, None
                else:
                    m = re.search(r'URI="([^"]+)"', line)
                    key_uri = m.group(1) if m else None
                    m = re.search(r"IV=0x([0-9a-fA-F]+)", line)
                    key_iv = bytes.fromhex(m.group(1).zfill(32)) if m else None
            elif not line.startswith("#"):
                if expect_variant and variant is None:
                    variant = line
                    expect_variant = False
                    continue
                entries.append((media_seq + len(entries), line, key_uri, key_iv))
        return entries, "#EXT-X-ENDLIST" in text, variant

    def _refresh_playlist(self) -> bool:
        """Queue segments newer than the last one played. True if any were added."""
        self._last_refresh = time.monotonic()
        resp = self._fetch_with_auth_retry(self.playlist_url, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"HLS playlist {self.playlist_url}: HTTP {resp.status_code}")
            return False
        entries, endlist, variant = self.parse_playlist(resp.text)
        if variant and not entries:
            self.playlist_url = self._abs(variant, self.playlist_url)
            return self._refresh_playlist()
        base = self.playlist_url
        entries = [(seq, self._abs(u, base), self._abs(k, base) if k else None, iv) for seq, u, k, iv in entries]
        self._endlist = endlist
        if endlist:
            self._vod = entries
        new = [e for e in entries if e[0] > self._last_seq]
        if self._last_seq < 0 and not endlist:
            new = new[-self.LIVE_START_SEGMENTS:]       # join near the live edge
        self._queue.extend(new)
        return bool(new)

    def _key_for(self, key_uri):
        if not key_uri:
            return None
        if key_uri not in self._keys:
            resp = self._fetch_with_auth_retry(key_uri, timeout=10)
            if resp.status_code == 200 and len(resp.content) == 16:
                self._keys = {key_uri: resp.content}    # keys rotate; keep only the current one
            else:
                logger.warning(f"Could not retrieve 16-byte AES key from {key_uri}")
                return None
        return self._keys[key_uri]

    def _load_entry(self, entry) -> bool:
        seq, seg_url, key_uri, iv = entry
        try:
            resp = self._fetch_with_auth_retry(seg_url, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"HLS segment {seg_url}: HTTP {resp.status_code}")
                return False
            data = resp.content
            key = self._key_for(key_uri)
            if key_uri and key is None:
                return False
            if key:
                # HLS spec: no IV attribute -> IV is the media sequence number
                iv = iv or seq.to_bytes(16, byteorder="big")
                decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
                data = decryptor.update(data) + decryptor.finalize()
            self._slot = (self._slot + 1) % 3
            seg_file = os.path.join(self._temp_dir, f"seg_{self._slot}.ts")
            with open(seg_file, "wb") as fh:
                fh.write(data)
            from app.services.blocking import run_blocking
            if self._current_cap is not None:
                self._current_cap.release()
            self._current_cap = run_blocking(cv2.VideoCapture, seg_file)
            if not self._current_cap.isOpened():
                return False
            w = self._current_cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            h = self._current_cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            if w > 0 and h > 0:
                self._width, self._height = int(w), int(h)
            fps = self._current_cap.get(cv2.CAP_PROP_FPS)
            if fps and fps > 0:
                self._fps = fps
            self._last_seq = seq
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Error loading HLS segment {seg_url}: {exc}")
            return False

    def _advance(self) -> bool:
        """Load the next playable segment. False when nothing is available yet."""
        if self._closed:
            return False
        if not self._queue:
            if self._endlist and self._vod:
                self._last_seq = -1
                self._queue = list(self._vod)          # loop a recorded clip
            else:
                wait = self.MIN_REFRESH_S - (time.monotonic() - self._last_refresh)
                if wait > 0:
                    time.sleep(wait)                    # green sleep: lets other cameras run
                try:
                    self._refresh_playlist()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"HLS playlist refresh failed for {self.playlist_url}: {exc}")
                    return False
        while self._queue:
            entry = self._queue.pop(0)
            if self._load_entry(entry):
                return True
            self._last_seq = max(self._last_seq, entry[0])   # skip a segment that is gone
        return False

    def _init_stream(self):
        try:
            self._refresh_playlist()
            if self._advance():
                self._is_opened = True
                logger.info(
                    f"SentinelCapture opened {self.stream_url} "
                    f"({self._width}x{self._height} @ {self._fps:.0f} FPS, live={not self._endlist})"
                )
            else:
                logger.error(f"SentinelCapture could not load a first segment for {self.stream_url}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Error initializing SentinelCapture: {exc}")

    # -- OpenCV-compatible API --------------------------------------------
    def isOpened(self) -> bool:
        return self._is_opened and not self._closed

    def read(self):
        from app.services.blocking import run_blocking
        with self._lock:
            if not self.isOpened() or self._current_cap is None:
                return False, None
            ret, frame = run_blocking(self._current_cap.read)
            if ret and frame is not None:
                self._frame_count += 1
                return True, frame
            if self._advance():
                ret, frame = run_blocking(self._current_cap.read)
                if ret and frame is not None:
                    self._frame_count += 1
                    return True, frame
            return False, None

    def get(self, propId: int) -> float:
        if propId == cv2.CAP_PROP_POS_MSEC:
            return (self._frame_count / self._fps) * 1000.0
        if propId == cv2.CAP_PROP_POS_FRAMES:
            return float(self._frame_count)
        if propId == cv2.CAP_PROP_FPS:
            return float(self._fps)
        if propId == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if propId == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        if self._current_cap:
            return self._current_cap.get(propId)
        return 0.0

    def release(self):
        with self._lock:
            self._closed = True
            self._is_opened = False
            if self._current_cap:
                self._current_cap.release()
                self._current_cap = None
            shutil.rmtree(self._temp_dir, ignore_errors=True)


def open_capture(stream_url: str):
    """
    Factory function returning an OpenCV-compatible capture object.
    Routes encrypted HLS streams to SentinelCapture and files/RTSP to cv2.VideoCapture.
    """
    if "cctv.corp8.cloud" in stream_url or (".m3u8" in stream_url and not stream_url.startswith("rtsp")):
        capture = SentinelCapture(stream_url)
        if capture.isOpened():
            return capture
        # Fallback to standard capture if SentinelCapture failed to initialize
        logger.warning(f"SentinelCapture failed to open {stream_url}, falling back to cv2.VideoCapture")

    # FFmpeg's open does its own network I/O in C and can block for seconds;
    # run it off the eventlet hub so other cameras and the API keep working.
    from app.services.blocking import run_blocking
    return run_blocking(cv2.VideoCapture, stream_url)


# ---------------------------------------------------------------------------
# Legacy Handshake Helpers (Retained for Backward Compatibility & Fallback)
# ---------------------------------------------------------------------------

def create_hls_session(hls_url: str):
    """Create a cookie-authenticated session for Sentinel HLS requests."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    })
    try:
        resp = session.get(hls_url, allow_redirects=True, timeout=10, stream=True)
        final_url = resp.url
        resp.close()

        parts = urlsplit(final_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["cookieCheck"] = "1"
        cookie_check_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
        resp2 = session.get(cookie_check_url, allow_redirects=True, timeout=10, stream=True)
        resp2.close()
        return session
    except requests.RequestException:
        session.close()
        return None


def establish_hls_session(hls_url: str) -> str:
    """Perform the gateway handshake and return an FFmpeg Cookie header."""
    session = create_hls_session(hls_url)
    if session:
        try:
            cookie = "; ".join(
                f"{key}={value}" for key, value in session.cookies.get_dict().items()
            )
            session.close()
            return cookie
        except requests.RequestException:
            session.close()
    return ""


def hls_capture_options(cookie_str: str = "") -> str:
    """Return OpenCV's FFmpeg options for an HLS capture."""
    options = "rtsp_transport;tcp|stimeout;8000000"
    if cookie_str:
        options += f"|headers;Cookie: {cookie_str}\r\n"
    return options


def hls_target_url(hls_url: str) -> str:
    """Add the gateway's cookieCheck query parameter to an HLS URL."""
    parts = urlsplit(hls_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["cookieCheck"] = "1"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )