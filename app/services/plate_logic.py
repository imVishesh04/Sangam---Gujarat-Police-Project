"""
Pure-Python plate logic for SANGAM ANPR (no OpenCV / torch / Flask imports).

Two jobs live here so they can be unit-tested without a GPU or a database:

1. ``correct_plate``  - position-aware character correction for Indian plates.
   Indian plates follow  SS DD L{1,3} DDDD  (state, RTO, series, number).
   OCR confuses look-alike glyphs (O/0, I/1, B/8, S/5, Z/2, G/6 ...). Because
   we KNOW which slots must be letters and which must be digits, we can fix
   those swaps deterministically instead of rejecting the whole read.

2. ``PlateVoter``     - multi-frame consensus. One noisy OCR read of one frame
   must not become a Detection (and certainly not a watchlist alert). Reads of
   the same vehicle within a short window are clustered (edit distance <= 1),
   a per-position weighted vote picks the final string, and a cooldown stops
   the same plate from being written again and again while it stays in view.

Time handling uses the *frame timestamp* (stream PTS-derived), never the wall
clock, so recorded / looping demo videos behave exactly like live feeds.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

INDIAN_PLATE_REGEX = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$")

# RTO state / UT series codes currently in circulation (incl. legacy DN/OR).
STATE_CODES = frozenset(
    "AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP "
    "MZ NL OD OR PB PY RJ SK TN TR TS TG UK UP WB".split()
)

# Glyph confusions, keyed by what the OCR *returned* -> what the slot needs.
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2",
             "S": "5", "G": "6", "B": "8", "A": "4"}
_TO_ALPHA = {"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G",
             "7": "T", "8": "B"}

# (rto_digits, series_letters) layouts in tie-break preference order -
# 2-digit RTO + 2-letter series is by far the most common shape.
_LAYOUTS = [(2, 2), (2, 1), (1, 1), (1, 2), (2, 3), (1, 3)]
_MIN_LEN, _MAX_LEN = 8, 11


def normalise(raw_text: str) -> str:
    """Uppercase and strip everything except A-Z and 0-9."""
    return re.sub(r"[^A-Z0-9]", "", (raw_text or "").upper())


def is_plausible_plate(text: str) -> bool:
    return bool(INDIAN_PLATE_REGEX.match(text or ""))


def is_valid_state(plate: str) -> bool:
    return len(plate) >= 2 and plate[:2] in STATE_CODES


@dataclass(frozen=True)
class PlateFix:
    plate: str        # corrected, regex-valid plate
    cost: int         # number of characters we had to change
    state_ok: bool    # first two letters are a real state/UT code


def _fix_char(ch: str, want: str) -> tuple[Optional[str], int]:
    """Coerce ``ch`` into slot type ``want`` ('L' letter / 'D' digit)."""
    if want == "D":
        if ch.isdigit():
            return ch, 0
        m = _TO_DIGIT.get(ch)
    else:
        if ch.isalpha():
            return ch, 0
        m = _TO_ALPHA.get(ch)
    return (m, 1) if m else (None, 0)


def _best_layout_fix(s: str) -> Optional[PlateFix]:
    n = len(s)
    best: Optional[tuple] = None
    for pref, (r, l) in enumerate(_LAYOUTS):
        if 2 + r + l + 4 != n:
            continue
        mask = "LL" + "D" * r + "L" * l + "DDDD"
        out, cost, ok = [], 0, True
        for ch, want in zip(s, mask):
            fixed, c = _fix_char(ch, want)
            if fixed is None:
                ok = False
                break
            out.append(fixed)
            cost += c
        if not ok:
            continue
        plate = "".join(out)
        if not is_plausible_plate(plate):
            continue
        # RTO codes start at 01 - "0" / "00" cannot be a real district code.
        # (Stops "GJ0IAB1234" being read as RTO "0" + 3-letter series "IAB".)
        if int(plate[2:2 + r]) == 0:
            continue
        key = (cost, not is_valid_state(plate), pref)
        if best is None or key < best[0]:
            best = (key, PlateFix(plate, cost, is_valid_state(plate)))
    return best[1] if best else None


def correct_plate(raw: str, max_cost: int = 3, require_state: bool = True) -> Optional[PlateFix]:
    """
    Turn noisy OCR text into a valid Indian plate, or return None.

    * ``max_cost``      - give up if more than this many characters must change.
    * ``require_state`` - reject plates whose first two letters aren't a real
      state/UT code (removes most garbage reads that happen to fit the shape).

    Leading/trailing junk (e.g. an "IND" strip glued onto the plate) is handled
    by sliding a window over strings longer than 11 characters.
    """
    s = normalise(raw)
    if len(s) < _MIN_LEN:
        return None
    if len(s) <= _MAX_LEN:
        windows = [s]
    elif len(s) <= 18:
        windows = [s[i:i + w] for w in range(_MIN_LEN, _MAX_LEN + 1)
                   for i in range(0, len(s) - w + 1)]
    else:
        return None

    best: Optional[PlateFix] = None
    for w in windows:
        fix = _best_layout_fix(w)
        if fix is None:
            continue
        if fix.cost > max_cost:
            continue
        if require_state and not fix.state_ok:
            continue
        # lower cost wins; for equal cost prefer a real state, then longer text
        if best is None or (fix.cost, not fix.state_ok, -len(fix.plate)) < \
                (best.cost, not best.state_ok, -len(best.plate)):
            best = fix
    return best


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------
# Multi-frame voting
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def to_seconds(ts) -> float:
    """datetime (aware or naive-UTC) or number -> epoch seconds."""
    if ts is None:
        return datetime.now(timezone.utc).timestamp()
    if isinstance(ts, (int, float)):
        return float(ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


@dataclass
class VoteResult:
    committed: bool
    plate: str
    confidence: float
    votes: int
    first_seen_s: float
    reason: str          # committed | solo-high-conf | pending | cooldown


@dataclass
class _CamState:
    reads: list = field(default_factory=list)     # (ts_s, plate, conf)
    commits: list = field(default_factory=list)   # (ts_s, plate)


class PlateVoter:
    def __init__(self, window_s: Optional[float] = None, min_votes: Optional[int] = None,
                 solo_conf: Optional[float] = None, cooldown_s: Optional[float] = None,
                 max_dist: Optional[int] = None):
        self.window_s = window_s if window_s is not None else _env_float("ANPR_VOTE_WINDOW_S", 6.0)
        self.min_votes = min_votes if min_votes is not None else _env_int("ANPR_MIN_VOTES", 2)
        self.solo_conf = solo_conf if solo_conf is not None else _env_float("ANPR_SOLO_CONF", 0.85)
        self.cooldown_s = cooldown_s if cooldown_s is not None else _env_float("ANPR_COOLDOWN_S", 30.0)
        self.max_dist = max_dist if max_dist is not None else _env_int("ANPR_VOTE_MAX_DIST", 1)
        self._cams: dict[str, _CamState] = {}
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self._cams.clear()

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _consensus(cluster: list) -> str:
        plates = {p for _, p, _ in cluster}
        if len(plates) == 1:
            return cluster[0][1]
        lengths = {len(p) for _, p, _ in cluster}
        if len(lengths) == 1:
            n = lengths.pop()
            out = []
            for i in range(n):
                tally: dict[str, float] = {}
                for _, p, c in cluster:
                    tally[p[i]] = tally.get(p[i], 0.0) + c
                out.append(max(tally.items(), key=lambda kv: kv[1])[0])
            cand = "".join(out)
            if is_plausible_plate(cand):
                return cand
        weights: dict[str, float] = {}
        for _, p, c in cluster:
            weights[p] = weights.get(p, 0.0) + c
        return max(weights.items(), key=lambda kv: kv[1])[0]

    # -- main entry -------------------------------------------------------
    def observe(self, camera_id: str, plate: str, conf: float, ts) -> VoteResult:
        t = to_seconds(ts)
        with self._lock:
            st = self._cams.setdefault(camera_id, _CamState())

            # Stream looped / re-anchored backwards: forget stale state.
            if st.reads and t < st.reads[-1][0] - self.window_s:
                st.reads.clear()
            st.commits = [c for c in st.commits if c[0] <= t + self.window_s]

            st.reads = [r for r in st.reads if t - r[0] <= self.window_s]

            for ct, cp in st.commits:
                if 0 <= t - ct < self.cooldown_s and edit_distance(cp, plate) <= self.max_dist:
                    return VoteResult(False, cp, conf, 0, ct, "cooldown")

            st.reads.append((t, plate, conf))
            cluster = [r for r in st.reads if edit_distance(r[1], plate) <= self.max_dist]
            votes = len(cluster)
            consensus = self._consensus(cluster)
            mean_conf = sum(c for _, _, c in cluster) / votes
            first_seen = min(r[0] for r in cluster)

            if votes >= self.min_votes:
                reason = "committed"
            elif votes == 1 and conf >= self.solo_conf:
                reason = "solo-high-conf"
            else:
                return VoteResult(False, consensus, mean_conf, votes, first_seen, "pending")

            st.commits.append((t, consensus))
            st.reads = [r for r in st.reads if r not in cluster]
            return VoteResult(True, consensus, round(mean_conf, 4), votes, first_seen, reason)


_voter: Optional[PlateVoter] = None
_voter_lock = threading.Lock()


def get_voter() -> PlateVoter:
    global _voter
    with _voter_lock:
        if _voter is None:
            _voter = PlateVoter()
        return _voter
