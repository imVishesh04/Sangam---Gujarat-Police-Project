"""
Seed script — populates the database with sample data for local testing.

Run once after first launch:
    python seed.py

Uses the live Sentinel HLS stream for the first camera so the complete
ingestion pipeline can be exercised against the government feed.
"""
import re
from app import create_app, db, bcrypt
from app.models import User, Camera, Watchlist

# TestingConfig disables automatic camera startup so OpenCV never tries to open
# the placeholder video files during seeding.
app = create_app(config_object="config.TestingConfig")

CATALOGUE_HOST = "cctv.corp8.cloud"


def fetch_camera_catalogue():
    """Fetch the authoritative 30-camera Sentinel catalogue."""
    from app.services.video_ingestion import fetch_catalogue
    cameras = fetch_catalogue(CATALOGUE_HOST)
    if len(cameras) != 30:
        raise RuntimeError(f"Expected 30 Sentinel cameras, received {len(cameras)}")
    return cameras


def catalogue_cameras(catalogue):
    """Convert catalogue records to the app schema with Gujarat map fallbacks."""
    # Proper Gujarat district-wise coordinates — spread across the state
    GUJARAT_COORDS = [
        (23.0469, 72.5073),   # Ahmedabad - Iskcon SG Highway
        (23.0350, 72.5200),   # Ahmedabad - Pakwan Crossroads
        (23.0268, 72.5985),   # Ahmedabad - Kalupur Railway
        (23.0156, 72.5311),   # Ahmedabad - Shivranjani
        (22.9601, 72.6891),   # Ahmedabad - Bhavda Toll
        (23.2156, 72.6369),   # Gandhinagar - Sachivalaya
        (23.1625, 72.6748),   # Gandhinagar - GIFT City
        (21.1702, 72.8311),   # Surat - Athwagate
        (21.2095, 72.8634),   # Surat - Varachha
        (21.0940, 72.6167),   # Surat - Hazira Port
        (22.3119, 73.1723),   # Vadodara - Alkapuri
        (22.3600, 73.2000),   # Vadodara - Fatehgunj
        (22.3039, 70.8022),   # Rajkot - Kalawad Road
        (22.2800, 70.7700),   # Rajkot - Gondal Road
        (21.7645, 72.1519),   # Bhavnagar - ST Bus Station
        (21.8000, 72.1800),   # Bhavnagar - Waghawadi Road
        (22.4707, 70.0577),   # Jamnagar - Reliance Bypass
        (22.4900, 70.0800),   # Jamnagar - Teen Batti
        (21.1244, 70.5762),   # Junagadh - Sasan Gir
        (21.5222, 70.4579),   # Junagadh - City Center
        (21.8381, 73.7190),   # Narmada - Statue of Unity
        (23.5880, 72.3693),   # Mehsana - Highway
        (23.8493, 72.1266),   # Patan - City Gate
        (24.1742, 72.4378),   # Banaskantha - Deesa
        (22.7272, 71.6496),   # Surendranagar - City
        (21.6032, 71.2218),   # Amreli - District HQ
        (20.9467, 72.9520),   # Navsari - City
        (20.5992, 72.9342),   # Valsad - City
        (22.5645, 72.9289),   # Anand - City
        (21.6425, 69.6293),   # Porbandar - City
    ]
    cameras = []
    for index, entry in enumerate(catalogue, start=1):
        source_id = str(entry.get("id", index))
        num_clean = re.sub(r"[^0-9]", "", source_id)
        num_int = int(num_clean) if num_clean else index
        camera_id = "CAM-AHM-001" if num_int == 6 else f"CAM-SENTINEL-{num_int:02d}"
        hls_path = entry.get("hls_live_url") or f"/cam{num_int:02d}/index.m3u8"
        stream_url = hls_path if hls_path.startswith("http") else f"https://{CATALOGUE_HOST}{hls_path}"
        coord_index = (index - 1) % len(GUJARAT_COORDS)
        latitude, longitude = GUJARAT_COORDS[coord_index]
        cameras.append({
            "camera_id": camera_id,
            "department": "Traffic Police",
            "location_name": entry.get("location") or entry.get("name") or f"Sentinel Camera {source_id}",
            "latitude": latitude,
            "longitude": longitude,
            "vendor": "Sentinel Government Feed",
            "stream_url": stream_url,
            "format": "hls",
        })
    return cameras


CAMERAS = catalogue_cameras(fetch_camera_catalogue())

WATCHLIST = [
    {"type": "vehicle", "value": "GJ01AB1234", "reason": "Stolen vehicle — FIR #2024/1001"},
    {"type": "vehicle", "value": "GJ05CD5678", "reason": "Suspect in robbery case #2024/2045"},
    {"type": "vehicle", "value": "GJ18EF9012", "reason": "Expired registration, repeat offender"},
    {"type": "vehicle", "value": "MH12GH3456", "reason": "Cross-border smuggling suspect"},
    {"type": "vehicle", "value": "RJ14IJ7890", "reason": "Wanted for hit-and-run"},
    {"type": "vehicle", "value": "GJ01KL2345", "reason": "Drug trafficking suspect"},
    {"type": "vehicle", "value": "GJ09MN6789", "reason": "Stolen vehicle — FIR #2024/3312"},
    {"type": "person",  "value": "ID-PERSON-001", "reason": "Wanted criminal — lookout notice"},
    {"type": "person",  "value": "ID-PERSON-002", "reason": "Missing person alert"},
    {"type": "vehicle", "value": "GJ27OP0123", "reason": "Vehicle used in extortion case"},
    {"type": "vehicle", "value": "GJ11AB1234", "reason": "Live HLS pipeline alert verification"},
]


def seed():
    with app.app_context():
        # Admin user
        if not User.query.filter_by(username="admin").first():
            admin = User(
                username="admin",
                password_hash=bcrypt.generate_password_hash("admin123").decode("utf-8"),
                role="admin",
            )
            db.session.add(admin)
            print("Created admin user (password: admin123)")

        # Viewer user
        if not User.query.filter_by(username="viewer").first():
            viewer = User(
                username="viewer",
                password_hash=bcrypt.generate_password_hash("viewer123").decode("utf-8"),
                role="viewer",
            )
            db.session.add(viewer)
            print("Created viewer user (password: viewer123)")

        # Cameras: retain CAM-AHM-001 so existing detections and alerts remain linked.
        for c in CAMERAS:
            camera = db.session.get(Camera, c["camera_id"])
            if not camera:
                db.session.add(Camera(**c, status="offline"))
                print(f"  + Camera {c['camera_id']} — {c['location_name']}")
            else:
                for key, value in c.items():
                    setattr(camera, key, value)
                print(f"  ~ Camera {c['camera_id']} updated — {c['stream_url']}")

        desired_ids = {camera["camera_id"] for camera in CAMERAS}
        obsolete = Camera.query.filter(~Camera.camera_id.in_(desired_ids)).all()
        for camera in obsolete:
            db.session.delete(camera)
            print(f"  - Removed obsolete camera {camera.camera_id}")

        # Watchlist
        for w in WATCHLIST:
            exists = Watchlist.query.filter_by(type=w["type"], value=w["value"]).first()
            if not exists:
                db.session.add(Watchlist(**w))
                print(f"  + Watchlist [{w['type']}] {w['value']}")

        db.session.commit()
        print("\nSeed complete.")


if __name__ == "__main__":
    seed()
