"""
End-to-end ANPR pipeline test with the heavy models stubbed out.

Checks the glue added in v4: noisy OCR -> character repair -> multi-frame vote
-> Detection row -> watchlist alert. Runs without torch / EasyOCR / a GPU.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db  # noqa: E402
from app.models import Alert, Detection, Watchlist  # noqa: E402
from app.services import anpr  # noqa: E402
from app.services.plate_logic import get_voter  # noqa: E402

CAM = "CAM-PIPE-TEST"
FRAME = np.zeros((200, 400, 3), dtype=np.uint8)
PLATE_BOX = [{"class_name": "license-plate", "confidence": 0.9, "coordinates": (50, 50, 250, 100)}]


class TestAnprPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("config.TestingConfig")
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        Alert.query.filter(Alert.camera_id == CAM).delete()
        Detection.query.filter(Detection.camera_id == CAM).delete()
        Watchlist.query.filter(Watchlist.reason == "pipeline-test").delete()
        db.session.commit()
        db.session.remove()
        cls.ctx.pop()

    def setUp(self):
        get_voter().reset()
        Alert.query.filter(Alert.camera_id == CAM).delete()
        Detection.query.filter(Detection.camera_id == CAM).delete()
        Watchlist.query.filter(Watchlist.reason == "pipeline-test").delete()
        db.session.commit()

    def _run(self, ocr_text, conf, ts, vote=True):
        with mock.patch.object(anpr, "_detect_yolo_boxes", return_value=[dict(b) for b in PLATE_BOX]), \
                mock.patch.object(anpr, "run_ocr", return_value=(ocr_text, conf)), \
                mock.patch.object(anpr, "_debug_dump_frame"):
            return anpr.process_frame(CAM, FRAME, ts, vote=vote)

    def _count(self):
        return Detection.query.filter_by(camera_id=CAM).count()

    def test_noisy_read_is_repaired_and_committed_on_second_read(self):
        t0 = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)
        self.assertIsNone(self._run("GJ0IAB1234", 0.6, t0))          # pending
        self.assertEqual(self._count(), 0)
        self._run("GJ01AB1234", 0.6, t0 + timedelta(seconds=1))       # 2nd agreeing read
        self.assertEqual(self._count(), 1)
        det = Detection.query.filter_by(camera_id=CAM).one()
        self.assertEqual(det.plate_number, "GJ01AB1234")

    def test_duplicate_suppressed_during_cooldown(self):
        t0 = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)
        self._run("GJ01AB1234", 0.95, t0)                             # solo high-conf
        for i in range(1, 6):
            self._run("GJ01AB1234", 0.95, t0 + timedelta(seconds=i))
        self.assertEqual(self._count(), 1)

    def test_garbage_state_code_rejected(self):
        t0 = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)
        self._run("XX01AB1234", 0.99, t0)
        self.assertEqual(self._count(), 0)

    def test_vote_false_persists_immediately(self):
        self._run("GJ01AB1234", 0.5, None, vote=False)
        self.assertEqual(self._count(), 1)

    def test_watchlist_alert_fires_once_voted(self):
        db.session.add(Watchlist(type="vehicle", value="GJ11AB1234", reason="pipeline-test"))
        db.session.commit()
        t0 = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)
        self._run("GJ11AB123O", 0.7, t0)                              # O in digit slot
        self._run("GJ11AB1234", 0.7, t0 + timedelta(seconds=1))
        self.assertEqual(Alert.query.filter_by(camera_id=CAM).count(), 1)


if __name__ == "__main__":
    unittest.main()
