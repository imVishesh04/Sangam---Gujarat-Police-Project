"""
Remove junk test data before a demo.

Early test runs (before the Indian-plate format check existed) saved garbage
OCR reads like "PNCEA" or "GSRTC" as detections, plus a "Test match" watchlist
entry. They make the Plate Reads page look broken. This script removes:

  * detections whose plate is not a valid Indian plate format
  * alerts whose matched plate is not a valid Indian plate format
  * VEHICLE watchlist entries whose plate is not valid (person entries are kept)

Safe by default: without --yes it only PRINTS what it would delete.
With --yes it first copies the SQLite file to cctv.db.bak-<timestamp>.

    python cleanup_demo_data.py          # preview
    python cleanup_demo_data.py --yes    # backup + delete
"""
import os
import re
import shutil
import sys
import time

from app import create_app, db
from app.models import Alert, Detection, Watchlist

PLATE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$")


def valid(plate):
    return bool(PLATE_RE.match((plate or "").strip().upper()))


def main():
    apply = "--yes" in sys.argv
    app = create_app()
    with app.app_context():
        bad_det = [d for d in Detection.query.all() if not valid(d.plate_number)]
        bad_alert = [a for a in Alert.query.all() if not valid(a.matched_value)]
        bad_wl = [w for w in Watchlist.query.filter_by(type="vehicle").all() if not valid(w.value)]
        # alerts pointing at a watchlist entry we are about to delete must go too
        bad_wl_ids = {w.watchlist_id for w in bad_wl}
        for a in Alert.query.filter(Alert.watchlist_id.in_(bad_wl_ids)).all() if bad_wl_ids else []:
            if a not in bad_alert:
                bad_alert.append(a)

        total = Detection.query.count()
        print(f"Detections: {len(bad_det)} invalid of {total}")
        print("  examples:", sorted({d.plate_number for d in bad_det})[:12])
        print(f"Alerts:     {len(bad_alert)} to remove -> {[a.matched_value for a in bad_alert]}")
        print(f"Watchlist:  {len(bad_wl)} to remove -> {[w.value for w in bad_wl]}")

        if not apply:
            print("\nPreview only. Run with --yes to back up the database and delete these rows.")
            return

        if db.engine.url.get_backend_name() == "sqlite":
            src = db.engine.url.database  # the exact file SQLAlchemy is using
            if src and os.path.exists(src):
                dst = f"{src}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
                shutil.copy2(src, dst)
                print(f"Backup written: {dst}")
            else:
                print("WARNING: SQLite file not found for backup; aborting to be safe.")
                return

        for a in bad_alert:
            db.session.delete(a)
        for d in bad_det:
            db.session.delete(d)
        db.session.flush()
        for w in bad_wl:
            db.session.delete(w)
        db.session.commit()
        print(f"Done. Detections left: {Detection.query.count()}, alerts left: {Alert.query.count()}")


if __name__ == "__main__":
    main()
