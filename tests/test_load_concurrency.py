"""
Unit and Integration Tests for Concurrent Ingestion and Thread Safety
======================================================================
Tests multi-camera worker lifecycle, thread-safe inference synchronization,
and clean parallel teardown under concurrency.
"""
import os
import threading
import time
import unittest
import numpy as np

from app import create_app, db
from app.models import Camera, Detection
from app.services.video_ingestion import (
    start_camera,
    stop_camera,
    stop_all_cameras,
    get_all_statuses,
    get_latest_frame,
    _workers,
    CAMERA_STATE,
)
from app.services.anpr import (
    warm_up,
    _detect_yolo_boxes,
    run_ocr,
    _inference_lock,
)
from load_test_ingestion import calculate_jains_fairness


class TestLoadConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("config.TestingConfig")
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()
        warm_up()

    @classmethod
    def tearDownClass(cls):
        stop_all_cameras()
        db.session.remove()
        cls.app_context.pop()

    def setUp(self):
        stop_all_cameras()

    def tearDown(self):
        stop_all_cameras()

    def test_jains_fairness_index(self):
        """Test calculation of Jain's fairness index."""
        # Perfectly balanced
        self.assertAlmostEqual(calculate_jains_fairness([10.0, 10.0, 10.0, 10.0]), 1.0)
        # Empty or zeros
        self.assertEqual(calculate_jains_fairness([]), 1.0)
        self.assertEqual(calculate_jains_fairness([0.0, 0.0]), 1.0)
        # Partially skewed: 10 vs 0 vs 0 vs 0 -> 1/4 = 0.25
        self.assertAlmostEqual(calculate_jains_fairness([10.0, 0.0, 0.0, 0.0]), 0.25)

    def test_concurrent_inference_thread_safety(self):
        """
        Invoke YOLO box detection and EasyOCR across 10 concurrent threads
        to verify that the synchronized inference lock prevents race conditions
        (such as Ultralytics predictor corruption / 'Conv object has no attribute bn').
        """
        errors = []
        test_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Draw a synthetic white rectangle resembling a plate
        test_frame[200:260, 250:450] = 255

        def worker(worker_id):
            try:
                for _ in range(3):
                    boxes = _detect_yolo_boxes(test_frame)
                    self.assertIsInstance(boxes, list)
                    crop = test_frame[200:260, 250:450]
                    text, conf = run_ocr(crop)
                    self.assertIsInstance(text, str)
                    self.assertIsInstance(conf, float)
            except Exception as e:
                errors.append((worker_id, e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Thread errors occurred during concurrent inference: {errors}")

    def test_concurrent_worker_lifecycle_and_teardown(self):
        """
        Start 8 camera workers simultaneously, verify all register in the status map,
        and verify stop_all_cameras joins and terminates all threads cleanly.
        """
        test_video = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_videos", "my_test.mp4")
        if not os.path.exists(test_video):
            test_video = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_videos", "cam1.mp4")

        cams = [f"TEST-CONCUR-CAM-{i:02d}" for i in range(8)]
        workers = []
        for cid in cams:
            w = start_camera(cid, test_video, self.app)
            workers.append(w)

        # Allow threads to initialize
        time.sleep(0.5)

        statuses = get_all_statuses()
        for cid in cams:
            self.assertIn(cid, statuses)
            self.assertTrue(cid in _workers and _workers[cid].is_alive())

        # Test idempotent start
        duplicate_worker = start_camera(cams[0], test_video, self.app)
        self.assertEqual(duplicate_worker, workers[0])

        # Test parallel teardown
        t0 = time.monotonic()
        stop_all_cameras()
        teardown_time = time.monotonic() - t0

        # Should teardown in < 6 seconds (not 8 * 5 = 40 seconds)
        self.assertLess(teardown_time, 6.0)
        for w in workers:
            self.assertFalse(w.is_alive(), f"Worker {w.name} failed to terminate")
        self.assertEqual(len(_workers), 0)


if __name__ == "__main__":
    unittest.main()
