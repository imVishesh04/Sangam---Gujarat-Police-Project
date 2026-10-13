"""
Alert severity: LOW / MEDIUM / HIGH / CRITICAL - rule based and explainable.

Every alert gets a level AND a one-line reason, so an officer (or a judge) can
see exactly why. No black box:

1. Base level from the watchlist reason (keyword groups below, first match wins,
   checked from CRITICAL down).
2. Escalation from what the cameras saw:
   - possible cloned plate (two sightings too far apart for the time) -> at least HIGH
   - vehicle actively moving: seen at 3+ different cameras in the last 30 min
     -> one level up (max CRITICAL)
3. Nothing matched -> MEDIUM (a watchlist hit always deserves a look).

Keywords are deliberately simple English/Hinglish words that appear in FIR
remarks; the lists are env-overridable later if the department wants its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

LEVELS = ["low", "medium", "high", "critical"]

KEYWORDS = {
    "critical": ("kidnap", "abduct", "murder", "terror", "ats", "weapon", "arms", "explosive",
                 "bomb", "missing child", "child missing", "hostage", "shooting", "firing"),
    "high": ("stolen", "theft", "robbery", "loot", "snatch", "hit-and-run", "hit and run",
             "narcotic", "drug", "ganja", "smuggl", "extortion", "wanted", "absconding",
             "missing", "fake plate", "cloned"),
    "low": ("expired", "challan", "fine", "e-challan", "insurance", "puc", "verification",
            "test entry", "demo", "parking"),
}


def _has(text: str, word: str) -> bool:
    if len(word) <= 4:  # short words ("ats", "arms") must be whole words
        import re
        return re.search(rf"\b{re.escape(word)}\b", text) is not None
    return word in text


def base_level(reason: str) -> tuple[str, str]:
    text = (reason or "").lower()
    for level in ("critical", "high", "low"):
        for w in KEYWORDS[level]:
            if _has(text, w):
                return level, f'reason mentions "{w}"'
    return "medium", "watchlist match, no risk keyword in reason"


def _up(level: str, steps: int = 1) -> str:
    return LEVELS[min(len(LEVELS) - 1, LEVELS.index(level) + steps)]


def classify(reason: str, sightings: list[dict] | None = None, clone: dict | None = None,
             now: datetime | None = None) -> dict:
    """
    sightings: newest first, each {"time": aware datetime, "camera_id": str}.
    Returns {"level", "why"}.
    """
    level, why = base_level(reason)
    notes = [why]

    if clone and LEVELS.index(level) < LEVELS.index("high"):
        level = "high"
        notes.append("possible cloned plate")
    elif clone:
        notes.append("possible cloned plate")

    if sightings:
        now = now or datetime.now(timezone.utc)
        recent = {s["camera_id"] for s in sightings
                  if s.get("time") and now - s["time"] <= timedelta(minutes=30)}
        if len(recent) >= 3 and level != "critical":
            level = _up(level)
            notes.append(f"moving: seen at {len(recent)} cameras in 30 min")

    return {"level": level, "why": "; ".join(notes)}
