# SANGAM — Statewide CCTV + ANPR Command Platform

Built by **Team Root Access** (Kalinga University) for the **Gujarat Police Innovation Hackathon 2026**.

SANGAM connects many independent CCTV sources into one command-room view. It reads number plates from live camera streams, checks them against a watchlist, rates how serious each hit is, and suggests which police unit to send.

> **Demo project.** All plates, names, phone numbers and watchlist entries in this repository are sample / test data. No real person or vehicle is targeted. Not affiliated with, or endorsed by, any government body.

## What it does

- **Live cameras** — HLS streams (including encrypted ones), with automatic reconnect and honest online/offline status.
- **Plate reading (ANPR)** — YOLOv8 plate detector + EasyOCR, with multi-frame voting to fix misreads.
- **Watchlist and alerts** — four severity levels (Critical / High / Medium / Low), cloned-plate checks, repeat-sighting escalation.
- **Dispatch suggestions** — nearest unit if the vehicle is stationary, intercept-ahead if it is moving.
- **WhatsApp alerts** — through a small WhatsApp Web bridge (or Twilio / Meta / CallMeBot), with de-duplication and rate limits.
- **Command-room UI** — map (satellite default), camera directory, alerts, plate reads, vehicle tracking, system health.

## Architecture

- **Model 1 — Registry + GIS (mandatory):** every camera is registered with location and jurisdiction.
- **Model 3 — VMS Federation / middleware:** existing camera systems stay as they are; SANGAM only pulls their streams.
- Rejected: Model 2 (brittle, hard to scale) and Model 4 (central GPU cluster: too costly, heavy on bandwidth and privacy).

## Tech stack

| Part | Tools |
|---|---|
| Backend | Python, Flask, Flask-SocketIO, eventlet, SQLAlchemy (SQLite by default, PostgreSQL optional) |
| Vision | YOLOv8, EasyOCR, OpenCV |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS v4, Leaflet, hls.js |
| Alerts | Node.js + whatsapp-web.js bridge |

## Run it locally

```bash
# 1. Backend
python -m venv .venv
.venv\Scripts\activate            # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env             # then edit .env with YOUR values (never commit .env)
python seed.py                     # sample data
python app.py                      # http://localhost:5000

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev

# 3. WhatsApp bridge (optional, new terminal)
cd wa_bridge
npm install
node index.js                      # scan the QR once
```

Run the tests:

```bash
python -m pytest tests --ignore=tests/test_load_concurrency.py
```

## Configuration

Copy `.env.example` to `.env`. Real values (camera-grid login, WhatsApp numbers, tokens) live **only** in `.env`, which is git-ignored. See `docs/WHATSAPP_SETUP.md` for alert setup.

## Safety notes

- Never commit `.env`, databases, or `wa_bridge/.wa_session/` (a logged-in WhatsApp session).
- Phone numbers are masked in the UI.
- Sample data is clearly labelled as demo data.

## Team

Team Root Access — Kalinga University, Raipur.
