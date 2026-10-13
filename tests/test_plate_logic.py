"""Unit tests for app/services/plate_logic.py (pure logic - no GPU, no DB)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.plate_logic import (  # noqa: E402
    PlateVoter,
    correct_plate,
    edit_distance,
    is_valid_state,
)


class TestCorrectPlate(unittest.TestCase):
    def test_clean_plate_unchanged(self):
        fix = correct_plate("GJ01AB1234")
        self.assertEqual((fix.plate, fix.cost, fix.state_ok), ("GJ01AB1234", 0, True))

    def test_real_demo_plate(self):
        self.assertEqual(correct_plate("GJ11AB1234").plate, "GJ11AB1234")

    def test_letter_in_digit_slot(self):
        fix = correct_plate("GJ0IAB1234")          # I -> 1
        self.assertEqual(fix.plate, "GJ01AB1234")
        self.assertEqual(fix.cost, 1)

    def test_digit_in_letter_slot(self):
        self.assertEqual(correct_plate("6J01AB1234").plate, "GJ01AB1234")   # 6 -> G
        self.assertEqual(correct_plate("GJ01A81234").plate, "GJ01AB1234")   # 8 -> B

    def test_multiple_swaps(self):
        fix = correct_plate("GJ0SA8I234")           # S->5? no: slot 3 is digit-> ok below
        # RTO "0S" -> "05", series "A8" -> "AB", number "I234" -> "1234"
        self.assertEqual(fix.plate, "GJ05AB1234")
        self.assertEqual(fix.cost, 3)

    def test_noise_and_spacing_stripped(self):
        self.assertEqual(correct_plate("gj 01-AB 1234").plate, "GJ01AB1234")

    def test_junk_prefix_removed(self):
        self.assertEqual(correct_plate("INDGJ01AB1234").plate, "GJ01AB1234")

    def test_invalid_state_rejected(self):
        self.assertIsNone(correct_plate("XX01AB1234"))
        self.assertIsNotNone(correct_plate("XX01AB1234", require_state=False))

    def test_too_short_or_garbage(self):
        self.assertIsNone(correct_plate("GJ01"))
        self.assertIsNone(correct_plate(""))
        self.assertIsNone(correct_plate(None))
        self.assertIsNone(correct_plate("HELLOWORLDTHISISTEXT"))

    def test_cost_cap(self):
        self.assertIsNone(correct_plate("GJ0SA8I234", max_cost=2))

    def test_single_digit_rto(self):
        self.assertEqual(correct_plate("MH2AB1234").plate, "MH2AB1234")

    def test_three_letter_series(self):
        self.assertEqual(correct_plate("KA01ABC1234").plate, "KA01ABC1234")

    def test_state_helper(self):
        self.assertTrue(is_valid_state("GJ01AB1234"))
        self.assertFalse(is_valid_state("ZZ01AB1234"))


class TestEditDistance(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(edit_distance("GJ01AB1234", "GJ01AB1234"), 0)
        self.assertEqual(edit_distance("GJ01AB1234", "GJ01AB1284"), 1)
        self.assertEqual(edit_distance("GJ01AB1234", "GJ01AB123"), 1)
        self.assertEqual(edit_distance("", "ABC"), 3)


class TestPlateVoter(unittest.TestCase):
    def make(self, **kw):
        base = dict(window_s=6.0, min_votes=2, solo_conf=0.85, cooldown_s=30.0, max_dist=1)
        base.update(kw)
        return PlateVoter(**base)

    def test_single_low_conf_read_is_pending(self):
        v = self.make()
        r = v.observe("C1", "GJ01AB1234", 0.5, 100.0)
        self.assertFalse(r.committed)
        self.assertEqual(r.reason, "pending")

    def test_two_reads_commit(self):
        v = self.make()
        v.observe("C1", "GJ01AB1234", 0.5, 100.0)
        r = v.observe("C1", "GJ01AB1234", 0.6, 101.0)
        self.assertTrue(r.committed)
        self.assertEqual(r.votes, 2)
        self.assertEqual(r.first_seen_s, 100.0)

    def test_solo_high_conf_commits(self):
        v = self.make()
        r = v.observe("C1", "GJ11AB1234", 0.93, 50.0)
        self.assertTrue(r.committed)
        self.assertEqual(r.reason, "solo-high-conf")

    def test_per_position_vote_fixes_minority_error(self):
        v = self.make(min_votes=3)
        v.observe("C1", "GJ11AB1234", 0.7, 10.0)
        v.observe("C1", "GJ11AB1238", 0.4, 11.0)      # one-char misread, low conf
        r = v.observe("C1", "GJ11AB1234", 0.7, 12.0)
        self.assertTrue(r.committed)
        self.assertEqual(r.plate, "GJ11AB1234")
        self.assertEqual(r.votes, 3)

    def test_reads_outside_window_do_not_combine(self):
        v = self.make()
        v.observe("C1", "GJ01AB1234", 0.5, 100.0)
        r = v.observe("C1", "GJ01AB1234", 0.5, 120.0)   # 20s later > 6s window
        self.assertFalse(r.committed)

    def test_cooldown_suppresses_duplicates(self):
        v = self.make()
        v.observe("C1", "GJ01AB1234", 0.9, 100.0)               # solo commit
        r = v.observe("C1", "GJ01AB1234", 0.9, 105.0)
        self.assertFalse(r.committed)
        self.assertEqual(r.reason, "cooldown")
        r2 = v.observe("C1", "GJ01AB1234", 0.9, 140.0)          # cooldown over
        self.assertTrue(r2.committed)

    def test_cameras_are_isolated(self):
        v = self.make()
        v.observe("C1", "GJ01AB1234", 0.5, 100.0)
        r = v.observe("C2", "GJ01AB1234", 0.5, 101.0)
        self.assertFalse(r.committed)

    def test_distinct_plates_do_not_merge(self):
        v = self.make()
        v.observe("C1", "GJ01AB1234", 0.5, 100.0)
        r = v.observe("C1", "MH12XY9876", 0.5, 101.0)
        self.assertFalse(r.committed)

    def test_time_jump_backwards_resets(self):
        v = self.make()
        v.observe("C1", "GJ01AB1234", 0.9, 1000.0)              # commit
        r = v.observe("C1", "GJ01AB1234", 0.9, 5.0)             # looped video restart
        self.assertTrue(r.committed)

    def test_min_votes_one_disables_voting(self):
        v = self.make(min_votes=1)
        self.assertTrue(v.observe("C1", "GJ01AB1234", 0.2, 1.0).committed)


if __name__ == "__main__":
    unittest.main()
