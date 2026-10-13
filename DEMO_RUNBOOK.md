# Demo Recording Runbook

Use the project virtual environment before running either demo:

```powershell
.venv\Scripts\Activate.ps1
```

## 1. Own-feed demo

Record a close, front-facing vehicle video and place it under `test_videos/`.
Keep the plate visible for several seconds. Replace the placeholders below with
that video's path and exact plate text.

```powershell
python run_demo.py own --video test_videos\my_car.mp4 --plate GJ01AB1234 --duration 180
```

The runner adds the exact plate to the watchlist, starts only the own-feed
camera, and prints `VALID DETECTION` and `WATCHLIST ALERT` lines. For the
screen recording, show the command, the live console output, and then the
alerts/detections API or dashboard.

## 2. Government-feed demo

Run Camera 6 for 10 to 15 minutes. The default is 15 minutes; use `600` for
10 minutes.

```powershell
python run_demo.py government --duration 900
```

The runner connects to the active encrypted Sentinel grid (`https://cctv.corp8.cloud`),
authenticates via `/auth/login`, decrypts the AES-128-CBC HLS stream in memory through
`SentinelCapture` / `open_capture()`, and feeds frames into the ANPR pipeline with
PTS-derived timestamps. Watchlist matching alerts will trigger in real time if suspect
vehicles appear in the feed.

For the screen recording, show the startup, the `BASELINE` line, HLS/ingestion
logs, `VALID DETECTION` lines, and the final `FINISHED` summary. Keep the
terminal visible while the run continues.

## Notes

- Do not type a guessed plate for the own-feed demo. Use the exact plate that
  is visible in the recorded video.
- Stop a run with `Ctrl+C`; the runner's cleanup path signals the camera worker
  to stop.
- Existing database rows are preserved. The runner uses a separate demo camera
  for the own-feed and filters output to rows created after the run baseline.
- The demo uses `TestingConfig`, so application startup does not open any
  existing cameras; only the selected own-feed or government camera is opened.
- Normal `python app.py` startup opens all DB cameras by default for backwards
  compatibility. For production load pacing, set `INGESTION_CAMERA_IDS` to a
  comma-separated allow-list. Set `INGESTION_USE_CATALOGUE=1` together with
  `CAMERA_CATALOGUE_HOST` when the live catalogue should be the source.

## 3. Offline-safe demo modes (added for Phase 1)

Never depend on venue Wi-Fi. Save one Sentinel clip (e.g. `sentinel_cam06_clip.mp4`)
into `clips/` BEFORE travelling.

```powershell
# Replay a recorded Sentinel clip - needs no network at all
python run_demo.py recorded --video clips\sentinel_cam06_clip.mp4 --plate GJ11AB1234

# Live grid, but auto-switch to the recorded clip if login/playlist fails
python run_demo.py government --camera-id 6 --fallback-video clips\sentinel_cam06_clip.mp4 --fallback-plate GJ11AB1234
```

The `government` mode prints a `PREFLIGHT` line first. If it says `ok=False`
and a fallback clip was given, you will see `FALLBACK | ...` and the demo
continues on the clip - same dashboard, same alerts, no dead air.

## 4. ANPR accuracy layer (v4)

- Position-aware repair of look-alike characters (O/0, I/1, B/8, S/5, Z/2, G/6...)
  using the Indian plate shape `SS DD L{1,3} DDDD`; invalid state codes rejected.
- Multi-frame vote: a plate is stored only after 2 agreeing reads (or 1 read with
  confidence >= 0.85); the same plate is not re-logged for 30 s on a camera.
- EasyOCR uses the GPU automatically when CUDA is available.
- Tunables (env vars): `ANPR_MIN_VOTES` (2), `ANPR_SOLO_CONF` (0.85),
  `ANPR_VOTE_WINDOW_S` (6), `ANPR_COOLDOWN_S` (30), `ANPR_REQUIRE_STATE` (1),
  `ANPR_OCR_GPU` (auto|0). Set `ANPR_MIN_VOTES=1` to get the old
  one-read-one-detection behaviour.

## 5. Route on the map (v5)

1. One time: `python check_camera_gps.py` (report), then `python check_camera_gps.py --apply`
   to move cameras whose GPS is far from the town in their name. Positions are approximate
   town centres; set an exact spot with `python demo_data_tool.py gps <CAM> <lat> <lon>`.
2. Route History -> type a watchlist plate (e.g. GJ01AB1234) -> **Generate demo route (5-7 cameras)**.
   - Creates 5-7 manual test sightings along one journey (start -> destination, no back-and-forth).
   - Uses only cameras that are online right now; if fewer than needed are online it uses
     registered cameras and the map panel says so.
   - The last checkpoint goes through watchlist matching -> real alert + WhatsApp.
   - Every click makes a new route and replaces the previous demo sightings for that plate
     (real ANPR reads are never deleted).
3. The map opens with a directions panel: total road time/distance, every checkpoint with time,
   distance and drive time per leg, and how long after the previous camera it was seen.
   Click a step to zoom to it. Other cameras are hidden until the route is closed (x).
4. The road path is fetched by the backend from two free OpenStreetMap routers (OSRM demo, then
   routing.openstreetmap.de). A route that worked once is cached in demo_output/road_route_cache.json,
   so it still draws if the venue internet drops. If no router answers, dashed straight lines are
   shown and the panel prints the reason.
5. Map tiles: Esri street map (default), OpenStreetMap, Esri satellite - none needs an API key.

## 6. Run many cameras at once (v7)

Cameras tab -> "Run cameras" bar (admin only):
- Tick cameras, or use "Select…" (first 4 / 8 / 16 shown, all shown, offline shown, online shown),
  then **Start selected** / **Stop selected**. Filters (city, status, department) decide what "shown" means.
- **Start all** / **Stop all** for every registered camera. Starting more than 8 asks for confirmation
  (each running camera does ANPR; on a laptop 4-8 is comfortable).
- Cameras start one by one, 0.5 s apart (BULK_START_GAP_S in .env), so the gateway is not hit
  by 30 connections at the same instant. Stop also cancels cameras still waiting to start.
- A camera turns ONLINE only when its video really arrives. Cameras without a stream URL are skipped
  and the message says how many.

## 7. Severity + nearest unit dispatch (v8)

- Every alert now has a real level: LOW / MEDIUM / HIGH / CRITICAL (app/services/severity.py),
  with the reason shown on hover and in WhatsApp. Cloned plate -> at least HIGH; seen at 3+ cameras
  in 30 min -> one level up. Same rules drive the dashboard, WhatsApp and dispatch.
- Dispatch agent (app/services/dispatch.py): picks the best 3 responding units for an alert -
  unit type by severity, only "available" units, ranked by distance to where the vehicle will be in
  ~10 min (heading + speed from the last two cameras), with ETA and a reason.
- WhatsApp alert now includes "Nearest unit (suggested)" and the local station.
- Dashboard: alert card -> Assign unit -> pick a suggested unit -> Send. Only that unit is messaged
  and the alert closes as "Assigned to <unit>". Units in app/services/data/response_units.json are a
  SAMPLE (approximate area points, no phone numbers) - with no number the message goes to WHATSAPP_TO.
  Add a unit's real number in that file to message the unit directly.
- Live position API (future: 112 Janrakshak GPS feed / PCR phone app):
  POST /dispatch/units/<unit_id>/location {"lat":..,"lon":..,"status":"available|busy"}
