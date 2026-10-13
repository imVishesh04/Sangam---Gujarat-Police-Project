"""Tests for the WhatsApp alert agent, RTO lookup and WhatsApp providers (no network)."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db  # noqa: E402
from app.models import Alert, Camera, Detection, Watchlist, AuditLog  # noqa: E402
from app.services import alert_agent, rto_lookup, whatsapp  # noqa: E402

CAM_A, CAM_B = "CAM-AGENT-A", "CAM-AGENT-B"
PLATE = "GJ01AB1234"


class TestRtoLookup(unittest.TestCase):
    def test_decode_gujarat(self):
        d = rto_lookup.decode_plate("GJ01AB1234")
        self.assertEqual((d["state"], d["rto_code"], d["rto_office"]), ("Gujarat", "GJ-01", "Ahmedabad"))
        self.assertEqual(rto_lookup.decode_plate("GJ11AB1234")["rto_office"], "Junagadh")
        self.assertEqual(rto_lookup.decode_plate("GJ5CD5678")["rto_code"], "GJ-05")

    def test_decode_other_state_and_invalid(self):
        d = rto_lookup.decode_plate("MH12GH3456")
        self.assertEqual(d["state"], "Maharashtra")
        self.assertIsNone(d["rto_office"])
        self.assertIsNone(rto_lookup.decode_plate("PNCEA"))

    def test_mask_name(self):
        self.assertEqual(rto_lookup.mask_name("Ramesh Patel"), "R***** P****")

    def test_sample_record_is_minimised(self):
        with mock.patch.dict(os.environ, {"RTO_PROVIDER": "sample"}):
            res = rto_lookup.lookup("GJ01AB1234")
        rec = res["record"]
        self.assertIn("SAMPLE", res["source"])
        self.assertEqual(rec["make"], "Maruti Suzuki")
        self.assertNotIn("owner_name", rec)
        self.assertTrue(rec["owner_masked"].startswith("S"))

    def test_real_camera_plate_has_no_invented_record(self):
        with mock.patch.dict(os.environ, {"RTO_PROVIDER": "sample"}):
            res = rto_lookup.lookup("GJ11AB1234")
        self.assertIsNone(res["record"])
        self.assertIn("VAHAN not connected", res["source"])
        self.assertEqual(res["decode"]["rto_office"], "Junagadh")

    def test_vahan_adapter_needs_config(self):
        with mock.patch.dict(os.environ, {"RTO_PROVIDER": "vahan_http", "RTO_API_URL": "", "RTO_API_KEY": ""}):
            res = rto_lookup.lookup("GJ01AB1234")
        self.assertIsNone(res["record"])
        self.assertIn("not configured", res["source"])


class TestReasoning(unittest.TestCase):
    def _s(self, minutes_ago, cam, lat, lon):
        return {"time": datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago),
                "camera_id": cam, "location": cam, "lat": lat, "lon": lon, "confidence": 0.9}

    def test_clone_flagged_for_impossible_speed(self):
        # Ahmedabad -> Rajkot (~200 km) in 10 minutes
        c = alert_agent.clone_check([self._s(0, "RJK", 22.3039, 70.8022), self._s(10, "AMD", 23.0225, 72.5714)])
        self.assertIsNotNone(c)
        self.assertGreater(c["speed"], 1000)

    def test_no_clone_for_plausible_trip(self):
        self.assertIsNone(alert_agent.clone_check(
            [self._s(0, "RJK", 22.3039, 70.8022), self._s(180, "AMD", 23.0225, 72.5714)]))

    def test_priority(self):
        self.assertEqual(alert_agent.auto_priority("Stolen vehicle — FIR 1")[0], "HIGH")
        self.assertEqual(alert_agent.auto_priority("Expired registration")[0], "LOW")
        self.assertEqual(alert_agent.auto_priority("Suspicious vehicle")[0], "MEDIUM")
        self.assertEqual(alert_agent.auto_priority("Kidnapping suspect")[0], "CRITICAL")


class TestProviders(unittest.TestCase):
    def test_twilio_payload(self):
        env = {"WHATSAPP_PROVIDER": "twilio", "TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "tok"}
        fake = mock.Mock(status_code=201, json=lambda: {"sid": "SM1"})
        with mock.patch.dict(os.environ, env), mock.patch("requests.post", return_value=fake) as post:
            ok, info = whatsapp.send("+919812345678", "hello")
        self.assertTrue(ok)
        self.assertEqual(info, "SM1")
        args, kwargs = post.call_args
        self.assertIn("/Accounts/AC123/Messages.json", args[0])
        self.assertEqual(kwargs["data"]["To"], "whatsapp:+919812345678")
        self.assertEqual(kwargs["data"]["From"], "whatsapp:+14155238886")
        self.assertEqual(kwargs["auth"], ("AC123", "tok"))

    def test_twilio_failure_reported_not_raised(self):
        env = {"WHATSAPP_PROVIDER": "twilio", "TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "tok"}
        with mock.patch.dict(os.environ, env), mock.patch("requests.post", side_effect=ConnectionError("x")):
            ok, info = whatsapp.send("+919812345678", "hello")
        self.assertFalse(ok)
        self.assertIn("ConnectionError", info)
        self.assertNotIn("tok", info)

    def test_meta_payload(self):
        env = {"WHATSAPP_PROVIDER": "meta", "META_WA_TOKEN": "t", "META_WA_PHONE_NUMBER_ID": "555"}
        fake = mock.Mock(status_code=200, json=lambda: {"messages": [{"id": "wamid.1"}]})
        with mock.patch.dict(os.environ, env), mock.patch("requests.post", return_value=fake) as post:
            ok, info = whatsapp.send("+91 98123 45678", "hi")
        self.assertTrue(ok)
        self.assertEqual(post.call_args.kwargs["json"]["to"], "919812345678")
        self.assertIn("/555/messages", post.call_args.args[0])

    def test_mask_number(self):
        self.assertEqual(whatsapp.mask_number("+919812345678"), "+91******5678")


class TestAgentEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("config.TestingConfig")
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        cls._clean()
        db.session.remove()
        cls.ctx.pop()

    @staticmethod
    def _clean():
        Alert.query.filter(Alert.camera_id.in_([CAM_A, CAM_B])).delete(synchronize_session=False)
        Detection.query.filter(Detection.camera_id.in_([CAM_A, CAM_B])).delete(synchronize_session=False)
        Camera.query.filter(Camera.camera_id.in_([CAM_A, CAM_B])).delete(synchronize_session=False)
        Watchlist.query.filter(Watchlist.reason == "agent-test stolen").delete(synchronize_session=False)
        AuditLog.query.filter(AuditLog.username == "alert-agent").delete(synchronize_session=False)
        db.session.commit()

    def setUp(self):
        self._clean()
        alert_agent._recent_keys.clear()
        alert_agent._send_times.clear()
        db.session.add_all([
            Camera(camera_id=CAM_A, department="Test", location_name="Paldi Crossroads, Ahmedabad",
                   latitude=23.0135, longitude=72.5623, vendor="T", stream_url="x", format="file", status="online"),
            Camera(camera_id=CAM_B, department="Test", location_name="Kalawad Road, Rajkot",
                   latitude=22.2870, longitude=70.7700, vendor="T", stream_url="x", format="file", status="online"),
        ])
        db.session.commit()
        self.wl = Watchlist.query.filter_by(type="vehicle", value=PLATE).first()
        if self.wl is None:
            self.wl = Watchlist(type="vehicle", value=PLATE, reason="agent-test stolen")
            db.session.add(self.wl)
            db.session.commit()

    def _make_alert(self, cam, minutes_ago):
        t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        db.session.add(Detection(camera_id=cam, plate_number=PLATE, confidence=0.93, timestamp=t))
        a = Alert(watchlist_id=self.wl.watchlist_id, camera_id=cam, matched_value=PLATE, timestamp=t)
        db.session.add(a)
        db.session.commit()
        return a.alert_id

    def test_message_contents(self):
        self._make_alert(CAM_A, 12)
        aid = self._make_alert(CAM_B, 0)       # Ahmedabad -> Rajkot in 12 min
        sent = []
        env = {"WHATSAPP_PROVIDER": "log", "WHATSAPP_TO": "+919812345678", "RTO_PROVIDER": "sample"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(whatsapp, "send", side_effect=lambda to, body: sent.append((to, body)) or (True, "ok")):
            res = alert_agent.process_alert(aid)
        self.assertEqual(res["status"], "sent")
        body = sent[0][1]
        for expected in (PLATE, "Kalawad Road, Rajkot", "maps.google.com/?q=22.28700,70.77000",
                         "Possible cloned plate", "Gujarat · GJ-01 Ahmedabad RTO",
                         "Maruti Suzuki", "(masked)", "SAMPLE registry", "IST", "*Priority:* "):
            self.assertIn(expected, body)
        self.assertRegex(body, r"\*Priority:\* (HIGH|CRITICAL)")  # stolen + cloned plate
        self.assertNotIn("Sample Owner Alpha", body)          # raw owner name never leaves
        self.assertEqual(AuditLog.query.filter_by(action="WHATSAPP_SENT", username="alert-agent").count(), 1)

    def test_duplicate_is_skipped(self):
        aid1 = self._make_alert(CAM_A, 1)
        aid2 = self._make_alert(CAM_A, 0)
        with mock.patch.dict(os.environ, {"WHATSAPP_PROVIDER": "log", "WHATSAPP_TO": "+919812345678"}), \
                mock.patch.object(whatsapp, "send", return_value=(True, "ok")):
            self.assertEqual(alert_agent.process_alert(aid1)["status"], "sent")
            self.assertEqual(alert_agent.process_alert(aid2)["status"], "skipped")

    def test_matching_engine_hands_off_in_background(self):
        from app.services.matching_engine import check_and_alert
        det = Detection(camera_id=CAM_A, plate_number=PLATE, confidence=0.9,
                        timestamp=datetime.now(timezone.utc))
        db.session.add(det)
        db.session.commit()
        sent = []
        with mock.patch.dict(os.environ, {"WHATSAPP_PROVIDER": "log", "WHATSAPP_TO": "+919812345678",
                                          "ALERT_AGENT_ENABLED": "1"}), \
                mock.patch.object(whatsapp, "send", side_effect=lambda to, body: sent.append(body) or (True, "ok")):
            alert = check_and_alert(det)
            self.assertIsNotNone(alert)
            alert_agent._queue.join()          # wait for the background worker
        self.assertEqual(len(sent), 1)
        self.assertIn(PLATE, sent[0])


if __name__ == "__main__":
    unittest.main()


class TestBackupAndHints(unittest.TestCase):
    def test_telegram_plain_text_payload(self):
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "999"}
        fake = mock.Mock(status_code=200, json=lambda: {"ok": True})
        with mock.patch.dict(os.environ, env), mock.patch("requests.post", return_value=fake) as post:
            ok, info = whatsapp.send_telegram("🚨 *SANGAM Alert*\n_Source: SAMPLE registry_")
        self.assertTrue(ok)
        self.assertIn("/bot123:abc/sendMessage", post.call_args.args[0])
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["chat_id"], "999")
        self.assertNotIn("*", sent["text"])
        self.assertIn("Source: SAMPLE registry", sent["text"])
        self.assertNotIn("_", sent["text"])

    def test_contentsid_error_gets_hint_and_no_retry(self):
        env = {"WHATSAPP_PROVIDER": "twilio", "TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t"}
        fake = mock.Mock(status_code=400, json=lambda: {"message": "ContentSid Required"})
        with mock.patch.dict(os.environ, env), mock.patch("requests.post", return_value=fake):
            ok, info = whatsapp.send("+919812345678", "x")
        self.assertFalse(ok)
        self.assertIn("HINT", info)
        self.assertTrue(whatsapp.is_permanent_error(info))
        self.assertFalse(whatsapp.is_permanent_error("twilio error: ConnectionError"))


class TestCallMeBot(unittest.TestCase):
    def test_keys_follow_recipient_order(self):
        env = {"WHATSAPP_PROVIDER": "callmebot", "WHATSAPP_TO": "+918000000001,+919000000002",
               "CALLMEBOT_APIKEYS": "111,222"}
        fake = mock.Mock(status_code=200, text="<p><b>Message queued.</b> You will receive it in a few seconds.</p>")
        with mock.patch.dict(os.environ, env), mock.patch("requests.get", return_value=fake) as get:
            ok1, info = whatsapp.send("+919000000002", "*hi*")
        self.assertTrue(ok1)
        self.assertIn("Message queued", info)
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["apikey"], "222")
        self.assertEqual(params["phone"], "+919000000002")
        self.assertEqual(params["text"], "*hi*")

    def test_invalid_key_reported(self):
        env = {"WHATSAPP_PROVIDER": "callmebot", "WHATSAPP_TO": "+918000000001", "CALLMEBOT_APIKEYS": "999"}
        fake = mock.Mock(status_code=200, text="<p>APIKey is invalid.</p>")
        with mock.patch.dict(os.environ, env), mock.patch("requests.get", return_value=fake):
            ok, info = whatsapp.send("+918000000001", "x")
        self.assertFalse(ok)
        self.assertIn("HINT", info)

    def test_key_lookup(self):
        env = {"WHATSAPP_TO": "+918000000001,+919000000002", "CALLMEBOT_APIKEYS": "111"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(whatsapp._callmebot_key_for("+918000000001"), "111")
        env2 = {"WHATSAPP_TO": "+918000000001,+919000000002", "CALLMEBOT_APIKEYS": "111,222"}
        with mock.patch.dict(os.environ, env2):
            self.assertEqual(whatsapp._callmebot_key_for("+91 90000 00002"), "222")
        with mock.patch.dict(os.environ, {"WHATSAPP_TO": "+918000000001", "CALLMEBOT_APIKEYS": ""}):
            ok, info = whatsapp._send_callmebot("+918000000001", "x")
        self.assertFalse(ok)
        self.assertIn("CALLMEBOT_APIKEYS missing", info)


class TestWebBridge(unittest.TestCase):
    def test_send_via_bridge(self):
        env = {"WHATSAPP_PROVIDER": "webbridge", "WA_BRIDGE_TOKEN": "s3cret"}
        fake = mock.Mock(status_code=200, json=lambda: {"ok": True, "id": "ABC"})
        with mock.patch.dict(os.environ, env), mock.patch("requests.post", return_value=fake) as post:
            ok, info = whatsapp.send("+919812345678", "*alert*")
        self.assertTrue(ok)
        self.assertEqual(info, "ABC")
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:3005/send")
        self.assertEqual(post.call_args.kwargs["json"], {"to": "+919812345678", "text": "*alert*"})
        self.assertEqual(post.call_args.kwargs["headers"]["X-Bridge-Token"], "s3cret")

    def test_bridge_not_running(self):
        import requests as _rq
        with mock.patch.dict(os.environ, {"WHATSAPP_PROVIDER": "webbridge"}), \
                mock.patch("requests.post", side_effect=_rq.exceptions.ConnectionError()):
            ok, info = whatsapp.send("+919812345678", "x")
        self.assertFalse(ok)
        self.assertIn("start_whatsapp_bridge.bat", info)

    def test_bridge_not_linked_hint(self):
        fake = mock.Mock(status_code=503, json=lambda: {"error": "WhatsApp not ready (waiting_for_qr_scan)"})
        with mock.patch.dict(os.environ, {"WHATSAPP_PROVIDER": "webbridge"}), \
                mock.patch("requests.post", return_value=fake):
            ok, info = whatsapp.send("+919812345678", "x")
        self.assertFalse(ok)
        self.assertIn("scan the QR", info)
