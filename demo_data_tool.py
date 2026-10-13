"""
Small fixes to demo data without touching the database by hand.

    python demo_data_tool.py reason GJ11AB1234 "Demo: suspicious vehicle check (test entry)"
    python demo_data_tool.py gps CAM-AHM-001 21.5222 70.4579
    python demo_data_tool.py show GJ11AB1234

`reason` changes the watchlist reason (WhatsApp alerts and the dashboard read it from there).
`gps` fixes a camera's coordinates (used for the map link and the cloned-plate check).
"""
import sys

from app import create_app, db
from app.models import Camera, Watchlist


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[0]
    app = create_app()
    with app.app_context():
        if cmd == "reason" and len(argv) >= 3:
            plate, reason = argv[1].upper(), " ".join(argv[2:])
            wl = Watchlist.query.filter(Watchlist.value.ilike(plate)).first()
            if not wl:
                print(f"{plate} is not on the watchlist")
                return 1
            print(f"{plate}: '{wl.reason}'  ->  '{reason}'")
            wl.reason = reason
            db.session.commit()
            return 0
        if cmd == "gps" and len(argv) == 4:
            cam = db.session.get(Camera, argv[1])
            if not cam:
                print(f"Camera {argv[1]} not found")
                return 1
            lat, lon = float(argv[2]), float(argv[3])
            if not (20.0 <= lat <= 24.8 and 68.0 <= lon <= 74.6):
                print("Those coordinates are outside Gujarat - check lat/lon order")
                return 1
            print(f"{cam.camera_id} ({cam.location_name}): {cam.latitude},{cam.longitude} -> {lat},{lon}")
            cam.latitude, cam.longitude = lat, lon
            if hasattr(cam, "geom"):
                cam.geom = f"POINT({lon} {lat})"
            db.session.commit()
            return 0
        if cmd == "show":
            wl = Watchlist.query.filter(Watchlist.value.ilike(argv[1])).first()
            print(wl.to_dict() if wl else "not on watchlist")
            return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
