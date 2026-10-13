"""
Check camera GPS against the town in the camera's name.

Routes on the map, the Google Maps link in WhatsApp alerts and the cloned-plate
check all use the camera registry coordinates. If "Junagadh" cameras sit near
Surat, every route through them looks wrong. This tool finds those.

    python check_camera_gps.py            # report only, changes nothing
    python check_camera_gps.py --apply    # move flagged cameras to the town (approximate)

--apply puts a flagged camera at the town centre plus a small fixed offset
(< 1.5 km, so cameras of one town don't sit on top of each other). That is an
APPROXIMATE position. If you know a camera's exact spot, set it with:
    python demo_data_tool.py gps <CAMERA_ID> <lat> <lon>
"""
import hashlib
import math
import sys

from app import create_app, db
from app.models import Camera

# keyword in camera name -> (town, lat, lon). Town-centre coordinates (approximate).
TOWNS = [
    ("gir-somnath", "Veraval (Gir Somnath)", 20.9070, 70.3700),
    ("gir somnath", "Veraval (Gir Somnath)", 20.9070, 70.3700),
    ("junagadh", "Junagadh", 21.5222, 70.4579),
    ("adalaj", "Adalaj", 23.1667, 72.5800),
    ("rajkot", "Rajkot", 22.3039, 70.8022),
    ("gandevi", "Gandevi", 20.8139, 72.9986),
    ("bilimora", "Bilimora", 20.7697, 72.9614),
    ("patan", "Patan", 23.8493, 72.1266),
    ("dehgam", "Dehgam", 23.1700, 72.8200),
    ("gandhidham", "Gandhidham", 23.0753, 70.1337),
    ("gandhinagar", "Gandhinagar", 23.2156, 72.6369),
    ("visat", "Visat Circle, Ahmedabad", 23.1030, 72.5880),
]
MAX_KM = 15.0


def km(a, b):
    r = 6371.0
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def offset(camera_id):
    """Same small offset every run for the same camera (about 0.3-1.3 km)."""
    d = hashlib.sha1(camera_id.encode()).digest()
    angle = d[0] / 255 * 2 * math.pi
    dist = 0.003 + d[1] / 255 * 0.009
    return dist * math.sin(angle), dist * math.cos(angle)


def town_for(name):
    low = (name or "").lower()
    for key, town, lat, lon in TOWNS:
        if key in low:
            return town, lat, lon
    return None


def main(apply):
    app = create_app()
    with app.app_context():
        cams = Camera.query.order_by(Camera.camera_id).all()
        flagged = []
        for cam in cams:
            hit = town_for(cam.location_name)
            if not hit:
                continue
            town, tlat, tlon = hit
            if cam.latitude is None or cam.longitude is None:
                flagged.append((cam, town, tlat, tlon, None))
                continue
            dist = km((cam.latitude, cam.longitude), (tlat, tlon))
            if dist > MAX_KM:
                flagged.append((cam, town, tlat, tlon, dist))

        no_town = [c for c in cams if not town_for(c.location_name)]
        print(f"{len(cams)} cameras | {len(cams) - len(no_town)} have a town in the name | "
              f"{len(flagged)} look misplaced (> {MAX_KM:.0f} km from that town)\n")
        for cam, town, tlat, tlon, dist in flagged:
            where = "no GPS" if dist is None else f"{dist:.0f} km away"
            print(f"  {cam.camera_id:<18} {cam.location_name[:42]:<42} -> {town} ({where})")
            if apply:
                dlat, dlon = offset(cam.camera_id)
                cam.latitude, cam.longitude = round(tlat + dlat, 6), round(tlon + dlon, 6)
                if hasattr(cam, "geom"):
                    cam.geom = f"POINT({cam.longitude} {cam.latitude})"
                print(f"  {'':<18} moved to {cam.latitude}, {cam.longitude} (approximate)")
        if apply and flagged:
            db.session.commit()
            print(f"\nSaved. {len(flagged)} cameras moved. Restart the backend and refresh the dashboard.")
        elif flagged:
            print("\nNothing changed. Run with --apply to move them.")
        if no_town:
            print(f"\nNot checked (no known town in the name): {', '.join(c.camera_id for c in no_town)}")
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv[1:]))
