"""
Check the WhatsApp alert setup from the terminal (backend does not need to be running).

    python send_test_whatsapp.py                 # short "connected" test message
    python send_test_whatsapp.py --latest-alert  # full message for the newest alert
    python send_test_whatsapp.py --preview       # print the newest alert message, send nothing
    python send_test_whatsapp.py --new-alert GJ11AB1234 CAM-AHM-001
        # creates a fresh manual test sighting NOW (same path as POST /detections/manual),
        # runs watchlist matching and sends the resulting alert

Uses the same settings as the server (.env): WHATSAPP_PROVIDER, WHATSAPP_TO, TWILIO_*.
"""
import os
import sys

if "--new-alert" in sys.argv:
    # this script sends the alert itself (synchronously) - stop the background
    # agent from sending a second copy when the matching engine fires
    os.environ["ALERT_AGENT_ENABLED"] = "0"

from app import create_app
from app.services import alert_agent, whatsapp


def main():
    args = set(sys.argv[1:])
    app = create_app()
    with app.app_context():
        print(f"Provider: {whatsapp.provider_name()} | telegram backup: {whatsapp.telegram_configured()} | recipients: "
              f"{[whatsapp.mask_number(r) for r in whatsapp.recipients()] or 'NONE (set WHATSAPP_TO)'}")

        if "--new-alert" in args:
            from datetime import datetime, timezone
            from app import db
            from app.models import Camera, Detection
            from app.services.matching_engine import check_and_alert
            rest = sys.argv[sys.argv.index("--new-alert") + 1:]
            if len(rest) < 2:
                print("Usage: python send_test_whatsapp.py --new-alert <PLATE> <CAMERA_ID>")
                return
            plate, cam_id = rest[0].upper(), rest[1]
            if db.session.get(Camera, cam_id) is None:
                print(f"Camera {cam_id} not found")
                return
            from app.models import Watchlist
            if not Watchlist.query.filter(Watchlist.type == "vehicle", Watchlist.value.ilike(plate)).first():
                print(f"{plate} is not on the watchlist - add it on the Watchlist page first.")
                return
            det = Detection(camera_id=cam_id, plate_number=plate, confidence=0.95,
                            timestamp=datetime.now(timezone.utc))
            db.session.add(det)
            db.session.commit()
            alert = check_and_alert(det)
            if alert is None:
                print(f"Manual sighting saved, but {plate} is not on the watchlist - no alert.")
                return
            os.environ["ALERT_AGENT_ENABLED"] = "1"
            res = alert_agent.process_alert(alert.alert_id, force=True)
            print(f"New alert #{alert.alert_id} for {plate} at {cam_id}")
            for r in res.get("results", []):
                print(f"  {r['to']}: {'SENT' if r['ok'] else 'FAILED'} - {r['info']}")
            return

        if "--latest-alert" in args or "--preview" in args:
            from app.models import Alert
            latest = Alert.query.order_by(Alert.alert_id.desc()).first()
            if latest is None:
                print("No alerts in the database yet.")
                return
            if "--preview" in args:
                ctx = alert_agent.build_context(latest.alert_id)
                print("\n" + alert_agent.compose(ctx))
                return
            res = alert_agent.process_alert(latest.alert_id, force=True)
            for r in res.get("results", []):
                print(f"  {r['to']}: {'SENT' if r['ok'] else 'FAILED'} - {r['info']}")
            return

        msg = "✅ *SANGAM test message*\nAlerts are connected."
        if not whatsapp.recipients() and not whatsapp.telegram_configured():
            print("Set WHATSAPP_TO (or TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID) in .env first")
            return
        for to in whatsapp.recipients():
            ok, info = whatsapp.send(to, msg)
            print(f"  {whatsapp.mask_number(to)}: {'SENT' if ok else 'FAILED'} - {info}")
        if whatsapp.telegram_configured():
            ok, info = whatsapp.send_telegram(msg)
            print(f"  telegram: {'SENT' if ok else 'FAILED'} - {info}")
        else:
            print("  telegram: not configured (optional backup)")


if __name__ == "__main__":
    main()
