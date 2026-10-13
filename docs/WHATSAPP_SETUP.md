# WhatsApp alerts: setup

## Option 1 (recommended): WhatsApp Web bridge - scan a QR once

Works like WhatsApp Web. Alerts are sent from the WhatsApp account you link.
Tip: link one teammate's phone as the SENDER and put the other teammate's
number in WHATSAPP_TO, so the receiving phone actually rings/notifies.
(Messages you send to your own number land in "Message yourself" without a sound.)

1. Double-click `start_whatsapp_bridge.bat` (first time it installs packages, 1-2 min, needs internet).
2. A QR code appears in the black window.
3. On the SENDER phone: WhatsApp > Settings > Linked devices > Link a device > scan the QR.
4. Wait for `WhatsApp bridge READY`. Keep this window open during the demo.
5. `.env`:

```
ALERT_AGENT_ENABLED=1
WHATSAPP_PROVIDER=webbridge
WHATSAPP_TO=+91XXXXXXXXXX
RTO_PROVIDER=sample
```

6. Test: `python send_test_whatsapp.py` and `python send_test_whatsapp.py --latest-alert`
7. Restart the backend (`python app.py`). Every watchlist match is now sent automatically.

The login is remembered in `wa_bridge/.wa_session` - no new QR after a restart.
Never share or commit that folder (it is a logged-in WhatsApp session; it is in .gitignore).
To unlink: WhatsApp > Linked devices > log out the device, and delete `wa_bridge/.wa_session`.

Note: this uses WhatsApp Web automation, which WhatsApp does not officially support.
Fine for a low-volume demo with your own numbers; production uses the official
Meta WhatsApp Business API (provider `meta`, already supported).

## Option 2: CallMeBot
Save +34 644 91 07 79, send exactly `I allow callmebot to send me messages`, put the key in
`CALLMEBOT_APIKEYS` and set `WHATSAPP_PROVIDER=callmebot`.

## No internet at the venue?
`WHATSAPP_PROVIDER=log` - messages are still built and shown on System Health and in
`demo_output/whatsapp_outbox.txt`.
