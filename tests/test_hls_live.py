"""SentinelCapture must follow a LIVE (sliding) playlist, decrypt AES-128 and not loop old segments."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

from app.services import hls  # noqa: E402

KEY = bytes(range(16))


class Resp:
    def __init__(self, code, content=b""):
        self.status_code = code
        self.content = content
        self.text = content.decode("utf-8", "ignore") if isinstance(content, bytes) else content


class LiveServer:
    """Sliding window of 3 segments; the window moves one segment per playlist fetch."""

    def __init__(self, segs):
        self.segs = segs
        self.fetches = 0

    def window(self):
        start = min(self.fetches, len(self.segs) - 3)
        return start, start + 3

    def get(self, url, timeout=None):
        if url.endswith("live.m3u8"):
            start, end = self.window()
            self.fetches += 1
            body = ["#EXTM3U", "#EXT-X-TARGETDURATION:1", f"#EXT-X-MEDIA-SEQUENCE:{start}",
                    '#EXT-X-KEY:METHOD=AES-128,URI="enc.key"']
            for i in range(start, end):
                body += ["#EXTINF:1.0,", f"seg{i}.ts"]
            return Resp(200, "\n".join(body).encode())
        if url.endswith("enc.key"):
            return Resp(200, KEY)
        name = url.rsplit("/", 1)[1]
        i = int(name[3:-3])
        start, _ = self.window()
        if i < start - 1:                       # deleted from the server
            return Resp(404)
        iv = i.to_bytes(16, "big")
        data = self.segs[i]
        pad = 16 - len(data) % 16
        data += bytes([pad]) * pad
        enc = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
        return Resp(200, enc.update(data) + enc.finalize())


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class TestLiveHls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                        "-t", "8", "-c:v", "libx264", "-g", "10", "-f", "hls", "-hls_time", "1",
                        "-hls_list_size", "0", "-hls_segment_filename", os.path.join(cls.tmp, "seg%d.ts"),
                        os.path.join(cls.tmp, "x.m3u8")], check=True)
        cls.segs = [open(os.path.join(cls.tmp, f"seg{i}.ts"), "rb").read() for i in range(8)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_follows_sliding_playlist(self):
        server = LiveServer(self.segs)
        with mock.patch.object(hls, "get_sentinel_session", return_value=server), \
             mock.patch.object(hls.SentinelCapture, "MIN_REFRESH_S", 0):
            cap = hls.SentinelCapture("https://example.test/cam01/live.m3u8")
            self.assertTrue(cap.isOpened())
            frames = 0
            for _ in range(400):
                ok, frame = cap.read()
                if ok:
                    frames += 1
                    self.assertEqual(frame.shape[:2], (120, 160))
            cap.release()
        self.assertGreater(cap._last_seq, 5, "should keep moving forward through new segments")
        self.assertGreater(frames, 40)

    def test_parse_master_and_iv(self):
        entries, endlist, variant = hls.SentinelCapture.parse_playlist(
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow/index.m3u8\n")
        self.assertEqual(variant, "low/index.m3u8")
        entries, endlist, _ = hls.SentinelCapture.parse_playlist(
            "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:40\n#EXT-X-KEY:METHOD=AES-128,URI=\"k\",IV=0x01\na.ts\nb.ts\n#EXT-X-ENDLIST")
        self.assertEqual([e[0] for e in entries], [40, 41])
        self.assertEqual(entries[0][3], (1).to_bytes(16, "big"))
        self.assertTrue(endlist)


if __name__ == "__main__":
    unittest.main()
