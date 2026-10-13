"""
WhatsApp delivery providers for the SANGAM alert agent.

WHATSAPP_PROVIDER selects one:
  log    (default) - no network; writes the message to demo_output/whatsapp_outbox.txt
  twilio           - Twilio WhatsApp API (sandbox works with a free trial)
                     TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
                     TWILIO_WHATSAPP_FROM (default sandbox number whatsapp:+14155238886)
  webbridge        - local WhatsApp Web bridge (wa_bridge/, start_whatsapp_bridge.bat):
                     scan a QR once, alerts go out from that WhatsApp account.
                     WA_BRIDGE_URL (default http://127.0.0.1:3005), WA_BRIDGE_TOKEN (optional)
  callmebot        - CallMeBot free WhatsApp API (easiest; personal use, each
                     phone activates itself and gets its own apikey)
                     CALLMEBOT_APIKEYS = keys in the SAME ORDER as WHATSAPP_TO
  meta             - Meta WhatsApp Cloud API
                     META_WA_TOKEN, META_WA_PHONE_NUMBER_ID
                     (free-form text only reaches numbers that messaged the
                     business number in the last 24 h - Meta's rule)

Recipients: WHATSAPP_TO, comma-separated, e.g. +919812345678,+919876543210

Optional backup channel (sent IN ADDITION to WhatsApp when configured):
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID  - free, no template rules

Every message is also appended to the outbox file, so the content can be shown
even if the venue has no internet. Secrets are never logged.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_BODY = 1500  # Twilio WhatsApp limit is 1600 chars
_OUTBOX = os.path.join(os.path.dirname(__file__), "..", "..", "demo_output", "whatsapp_outbox.txt")


def recipients() -> list[str]:
    raw = os.environ.get("WHATSAPP_TO", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def mask_number(num: str) -> str:
    digits = re.sub(r"\D", "", num or "")
    return f"+{digits[:2]}******{digits[-4:]}" if len(digits) >= 8 else "***"


def provider_name() -> str:
    return os.environ.get("WHATSAPP_PROVIDER", "log").strip().lower() or "log"


def _write_outbox(to: str, body: str, status: str):
    try:
        os.makedirs(os.path.dirname(_OUTBOX), exist_ok=True)
        with open(_OUTBOX, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} | to {mask_number(to)} | {status} =====\n")
            fh.write(body + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.error("Outbox write failed: %s", exc)


def _send_twilio(to: str, body: str) -> tuple[bool, str]:
    import requests
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    sender = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886").strip()
    if not sid or not token:
        return False, "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN missing in .env"
    if not sender.startswith("whatsapp:"):
        sender = "whatsapp:" + sender
    dest = to if to.startswith("whatsapp:") else "whatsapp:" + ("+" + to.lstrip("+"))
    resp = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data={"From": sender, "To": dest, "Body": body},
        auth=(sid, token),
        timeout=10,
    )
    if resp.status_code in (200, 201):
        return True, resp.json().get("sid", "sent")
    try:
        err = resp.json().get("message", "")
    except Exception:  # noqa: BLE001
        err = resp.text[:200]
    return False, f"Twilio HTTP {resp.status_code}: {err}"


def _send_meta(to: str, body: str) -> tuple[bool, str]:
    import requests
    token = os.environ.get("META_WA_TOKEN", "").strip()
    phone_id = os.environ.get("META_WA_PHONE_NUMBER_ID", "").strip()
    if not token or not phone_id:
        return False, "META_WA_TOKEN / META_WA_PHONE_NUMBER_ID missing in .env"
    resp = requests.post(
        f"https://graph.facebook.com/{os.environ.get('META_GRAPH_VERSION', 'v23.0')}/{phone_id}/messages",
        json={"messaging_product": "whatsapp", "to": re.sub(r"\D", "", to),
              "type": "text", "text": {"preview_url": True, "body": body}},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code == 200:
        msgs = resp.json().get("messages") or [{}]
        return True, msgs[0].get("id", "sent")
    try:
        err = resp.json().get("error", {}).get("message", "")
    except Exception:  # noqa: BLE001
        err = resp.text[:200]
    return False, f"Meta HTTP {resp.status_code}: {err}"


def _send_webbridge(to: str, body: str) -> tuple[bool, str]:
    import requests
    url = os.environ.get("WA_BRIDGE_URL", "http://127.0.0.1:3005").rstrip("/")
    headers = {}
    if os.environ.get("WA_BRIDGE_TOKEN", "").strip():
        headers["X-Bridge-Token"] = os.environ["WA_BRIDGE_TOKEN"].strip()
    try:
        resp = requests.post(f"{url}/send", json={"to": to, "text": body}, headers=headers, timeout=30)
    except requests.exceptions.ConnectionError:
        return False, "WA bridge not running - double-click start_whatsapp_bridge.bat"
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {}
    if resp.status_code == 200 and data.get("ok"):
        return True, data.get("id") or "sent"
    err = data["error"] if "error" in data else str(resp.text)[:120]
    return False, f"WA bridge HTTP {resp.status_code}: {err}"


def _callmebot_key_for(to: str) -> str:
    keys = [k.strip() for k in os.environ.get("CALLMEBOT_APIKEYS", "").split(",") if k.strip()]
    nums = [re.sub(r"\D", "", r) for r in recipients()]
    digits = re.sub(r"\D", "", to)
    if digits in nums and nums.index(digits) < len(keys):
        return keys[nums.index(digits)]
    return keys[0] if len(keys) == 1 else ""


def _send_callmebot(to: str, body: str) -> tuple[bool, str]:
    import requests
    key = _callmebot_key_for(to)
    if not key:
        return False, "CALLMEBOT_APIKEYS missing in .env for this number (same order as WHATSAPP_TO)"
    resp = requests.get(
        "https://api.callmebot.com/whatsapp.php",
        params={"phone": "+" + re.sub(r"\D", "", to), "text": body, "apikey": key},
        timeout=20,
    )
    text = re.sub(r"<[^>]+>", " ", resp.text or "")
    text = re.sub(r"\s+", " ", text).strip()[:160]
    bad = any(w in text.lower() for w in ("invalid", "error", "wrong", "not activated", "blocked"))
    if resp.status_code == 200 and not bad:
        return True, text or "queued"
    return False, f"CallMeBot HTTP {resp.status_code}: {text}"


_HINTS = [
    ("ContentSid Required", "Twilio TRIAL accounts may only send Twilio's fixed templates - "
                            "custom alerts need Meta Cloud API (WHATSAPP_PROVIDER=meta) or an upgraded Twilio account"),
    ("verified recipient", "add/verify this number in Twilio (or join the sandbox) first"),
    ("63015", "this number has not joined the Twilio sandbox"),
    ("63007", "TWILIO_WHATSAPP_FROM is not a WhatsApp-enabled sender"),
    ("131047", "recipient must send any message to the business number first (24-hour window)"),
    ("131030", "number is not in the allowed recipient list of the Meta test number"),
    ("WA bridge HTTP 503", "open the bridge window and scan the QR (WhatsApp > Linked devices)"),
    ("not a WhatsApp number", "check the number in WHATSAPP_TO (+91 and 10 digits)"),
    ("CallMeBot", "check the apikey for this number; each phone must send "
                  "'I allow callmebot to send me messages' to +34 644 91 07 79 first"),
    ("190", "Meta access token expired - generate a new one in the WhatsApp > API Setup page"),
]


def hint_for(info: str) -> str:
    for needle, hint in _HINTS:
        if needle in (info or ""):
            return f"{info} -> HINT: {hint}"
    return info


def is_permanent_error(info: str) -> bool:
    """4xx-style errors will fail again on retry; network/5xx errors may not."""
    # WA bridge errors are not retried: the message may already have gone out,
    # and a retry would send the same alert twice.
    return (bool(re.search(r"HTTP 4\d\d", info or "")) or "missing in .env" in (info or "")
            or "WA bridge HTTP" in (info or ""))


def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and os.environ.get("TELEGRAM_CHAT_ID", "").strip())


def send_telegram(body: str) -> tuple[bool, str]:
    """Backup channel. Plain text (WhatsApp *bold* markers removed). Never raises."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    if not token or not chat_ids:
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env"
    text = re.sub(r"_(.+?)_", r"\1", body.replace("*", ""))
    try:
        import requests
        errors = []
        for chat in chat_ids:
            resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat, "text": text, "disable_web_page_preview": False},
                                 timeout=10)
            if resp.status_code != 200:
                try:
                    errors.append(f"Telegram HTTP {resp.status_code}: {resp.json().get('description', '')}")
                except Exception:  # noqa: BLE001
                    errors.append(f"Telegram HTTP {resp.status_code}")
        ok, info = (not errors), ("; ".join(errors) if errors else "sent")
    except Exception as exc:  # noqa: BLE001
        ok, info = False, f"telegram error: {type(exc).__name__}"
    _write_outbox("telegram", body, f"telegram {'OK' if ok else 'FAILED'} {info}")
    return ok, info


def send(to: str, body: str) -> tuple[bool, str]:
    """Send one message. Returns (ok, provider_message_id_or_error). Never raises."""
    body = body if len(body) <= MAX_BODY else body[: MAX_BODY - 20] + "\n…(truncated)"
    provider = provider_name()
    try:
        if provider == "twilio":
            ok, info = _send_twilio(to, body)
        elif provider == "meta":
            ok, info = _send_meta(to, body)
        elif provider == "webbridge":
            ok, info = _send_webbridge(to, body)
        elif provider == "callmebot":
            ok, info = _send_callmebot(to, body)
        else:
            ok, info = True, "logged (WHATSAPP_PROVIDER=log)"
    except Exception as exc:  # noqa: BLE001 - network errors must not crash the agent
        ok, info = False, f"{provider} error: {type(exc).__name__}"
    if not ok:
        info = hint_for(info)
    _write_outbox(to, body, f"{provider} {'OK' if ok else 'FAILED'} {info}")
    return ok, info
