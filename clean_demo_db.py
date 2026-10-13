"""
Remove old junk ANPR rows from cctv.db before the demo.

Early pipeline versions (before the Indian-plate format check) saved garbage
OCR reads such as "PNCEA", "GSRTC", "ES4412". They still show up in the
Detections Log and look bad in front of judges. This script deletes:

  * detections whose plate is not a valid Indian plate format
  * alerts whose matched value is not a valid Indian plate format (old test rows)

Watchlist entries and cameras are NOT touched. A timestamped backup of the
database is written first.

Usage (from the project folder):
    python clean_demo_db.py            # dry run - only shows what would be deleted
    python clean_demo_db.py --apply    # backup + delete
"""
import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PLATE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="cctv.db", help="path to the SQLite database (default: cctv.db)")
    ap.add_argument("--apply", action="store_true", help="actually delete (default is a dry run)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"Database not found: {db_path.resolve()}")

    con = sqlite3.connect(db_path)
    dets = con.execute("SELECT detection_id, plate_number FROM detections").fetchall()
    alerts = con.execute("SELECT alert_id, matched_value FROM alerts").fetchall()
    bad_dets = [(i, p) for i, p in dets if not PLATE_RE.match(p or "")]
    bad_alerts = [(i, v) for i, v in alerts if not PLATE_RE.match(v or "")]

    print(f"Detections: {len(dets)} total, {len(bad_dets)} invalid, {len(dets) - len(bad_dets)} kept")
    print(f"Alerts:     {len(alerts)} total, {len(bad_alerts)} invalid, {len(alerts) - len(bad_alerts)} kept")
    sample = sorted({p for _, p in bad_dets})[:12]
    if sample:
        print("Examples of invalid plates:", ", ".join(map(str, sample)))
    kept = [p for _, p in dets if PLATE_RE.match(p or "")]
    if kept:
        print("Valid plates kept:", ", ".join(sorted(set(kept))))

    if not args.apply:
        print("\nDry run only. Run again with --apply to delete.")
        return

    backup = db_path.with_name(f"{db_path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}{db_path.suffix}")
    con.close()
    shutil.copy2(db_path, backup)
    print(f"\nBackup written: {backup}")

    con = sqlite3.connect(db_path)
    with con:
        con.executemany("DELETE FROM detections WHERE detection_id = ?", [(i,) for i, _ in bad_dets])
        con.executemany("DELETE FROM alerts WHERE alert_id = ?", [(i,) for i, _ in bad_alerts])
    con.close()
    print(f"Deleted {len(bad_dets)} detections and {len(bad_alerts)} alerts. Done.")


if __name__ == "__main__":
    main()
