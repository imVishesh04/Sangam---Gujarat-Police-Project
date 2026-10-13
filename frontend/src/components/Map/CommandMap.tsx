import React, { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import { Camera, TrackPoint, Alert } from '../../types';
import { apiService } from '../../services/apiService';
import { Layers, Satellite, Map as MapIcon, Locate, X, Route as RouteIcon, ChevronDown, ChevronUp, Check } from 'lucide-react';
import { BrandLogo } from '../ui/BrandLogos';
import { prettyPlate } from '../ui/kit';

interface CommandMapProps {
  cameras: Camera[];
  selectedCamera: Camera | null;
  onSelectCamera: (camera: Camera, isAlert?: boolean) => void;
  activeAlerts?: Alert[];
  trackPoints?: TrackPoint[];
  searchedPlate?: string;
  className?: string;
  focusDistrict?: string;
  onClearRoute?: () => void;
  routeNote?: string;
  /** one-time "show this camera" request from another tab */
  focus?: { id: string; n: number } | null;
  onFocusDone?: () => void;
}

type RouteStop = { camera_id: string; name: string; times: string[] };
type RouteLeg = { km: number; minutes: number | null; gapMin: number | null };
type RouteInfo = {
  mode: 'loading' | 'road' | 'straight';
  km?: number;
  minutes?: number;
  stops: RouteStop[];
  legs: RouteLeg[];
  error?: string;
};

// Public OSRM demo router (OpenStreetMap roads). Used only if the backend router fails.
const OSRM_URL = 'https://router.project-osrm.org/route/v1/driving/';

function haversineKm(a: [number, number], b: [number, number]): number {
  const R = 6371;
  const dLat = ((b[0] - a[0]) * Math.PI) / 180;
  const dLon = ((b[1] - a[1]) * Math.PI) / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos((a[0] * Math.PI) / 180) * Math.cos((b[0] * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function fmtDuration(min: number): string {
  if (min < 1) return '<1 min';
  if (min >= 48 * 60) return `${Math.round(min / 1440)} days`;
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return h > 0 ? `${h} h ${m} min` : `${m} min`;
}

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string));

const fmtIST = (t: string) =>
  new Date(t).toLocaleString('en-GB', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });

// All keyless. Satellite (with road + place labels) is the default in every layout.
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/';
const BASE_LAYERS = {
  hybrid: {
    name: 'Satellite + labels',
    short: 'Satellite',
    base: `${ESRI}World_Imagery/MapServer/tile/{z}/{y}/{x}`,
    overlays: [`${ESRI}Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}`, `${ESRI}Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`],
    maxZoom: 19,
    attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics',
  },
  satellite: {
    name: 'Satellite only',
    short: 'Satellite',
    base: `${ESRI}World_Imagery/MapServer/tile/{z}/{y}/{x}`,
    overlays: [] as string[],
    maxZoom: 19,
    attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics',
  },
  streets: {
    name: 'Street map',
    short: 'Streets',
    base: `${ESRI}World_Street_Map/MapServer/tile/{z}/{y}/{x}`,
    overlays: [] as string[],
    maxZoom: 19,
    attribution: '&copy; Esri, HERE, Garmin, OpenStreetMap contributors',
  },
  osm: {
    name: 'OpenStreetMap',
    short: 'OSM',
    base: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    overlays: [] as string[],
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  },
};
type BaseKey = keyof typeof BASE_LAYERS;
const DEFAULT_BASE: BaseKey = 'hybrid';

const GUJARAT: { center: [number, number]; zoom: number } = { center: [22.6, 71.6], zoom: 7 };

const DISTRICT_COORDINATES: Record<string, { center: [number, number]; zoom: number }> = {
  Ahmedabad: { center: [23.0225, 72.5714], zoom: 12 },
  Surat: { center: [21.1702, 72.8311], zoom: 12 },
  Vadodara: { center: [22.3072, 73.1812], zoom: 12 },
  Rajkot: { center: [22.3039, 70.8022], zoom: 12 },
  Gandhinagar: { center: [23.2156, 72.6369], zoom: 13 },
  Amreli: { center: [21.6032, 71.2218], zoom: 12 },
  Anand: { center: [22.5645, 72.9289], zoom: 12 },
  Arvalli: { center: [23.6833, 73.0167], zoom: 12 },
  Banaskantha: { center: [24.1742, 72.4378], zoom: 11 },
  Bharuch: { center: [21.7051, 72.9959], zoom: 12 },
  Bhavnagar: { center: [21.7645, 72.1519], zoom: 12 },
  Botad: { center: [22.1693, 71.6671], zoom: 12 },
  'Chhota Udaipur': { center: [22.3167, 74.0167], zoom: 12 },
  Dahod: { center: [22.8376, 74.249], zoom: 12 },
  Dang: { center: [20.75, 73.7167], zoom: 12 },
  'Devbhoomi Dwarka': { center: [22.2394, 68.9678], zoom: 12 },
  'Gir Somnath': { center: [20.907, 70.37], zoom: 12 },
  Jamnagar: { center: [22.4707, 70.0577], zoom: 12 },
  Junagadh: { center: [21.5222, 70.4579], zoom: 12 },
  Kheda: { center: [22.75, 72.6833], zoom: 12 },
  Kutch: { center: [23.7337, 69.8597], zoom: 10 },
  Mahisagar: { center: [23.1, 73.55], zoom: 12 },
  Mehsana: { center: [23.588, 72.3693], zoom: 12 },
  Morbi: { center: [22.8173, 70.837], zoom: 12 },
  Narmada: { center: [21.8714, 73.4939], zoom: 12 },
  Navsari: { center: [20.9467, 72.952], zoom: 12 },
  Panchmahal: { center: [22.75, 73.5167], zoom: 12 },
  Patan: { center: [23.8493, 72.1266], zoom: 12 },
  Porbandar: { center: [21.6425, 69.6293], zoom: 12 },
  Sabarkantha: { center: [23.35, 73.0167], zoom: 12 },
  Surendranagar: { center: [22.7272, 71.6496], zoom: 12 },
  Tapi: { center: [21.15, 73.4167], zoom: 12 },
  Valsad: { center: [20.5992, 72.9342], zoom: 12 },
};

const CAM_SVG =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m16 13 5.2 3.5a.5.5 0 0 0 .8-.4V7.9a.5.5 0 0 0-.8-.4L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>';

function markerHtml(kind: 'alert' | 'selected' | 'online' | 'offline') {
  if (kind === 'alert') {
    return `<div class="sg-pin" style="width:40px;height:40px">
      <span style="position:absolute;inset:0;border-radius:999px;background:rgba(255,90,95,.35);animation:live-pulse 1.4s ease-in-out infinite"></span>
      <span style="position:absolute;inset:6px;border-radius:999px;background:#FF5A5F;border:2.5px solid #fff;display:flex;align-items:center;justify-content:center;color:#fff;font:700 15px/1 'Inter Variable',Inter,sans-serif;box-shadow:0 4px 12px rgba(0,0,0,.5)">!</span>
    </div>`;
  }
  if (kind === 'selected') {
    return `<div class="sg-pin" style="width:40px;height:40px">
      <span style="position:absolute;inset:0;border-radius:999px;background:rgba(76,141,255,.35)"></span>
      <span style="position:absolute;inset:6px;border-radius:999px;background:#4C8DFF;border:2.5px solid #fff;display:flex;align-items:center;justify-content:center;color:#fff;box-shadow:0 4px 12px rgba(0,0,0,.5)">${CAM_SVG}</span>
    </div>`;
  }
  if (kind === 'online') {
    return `<div class="sg-pin" style="width:28px;height:28px">
      <span style="position:absolute;inset:2px;border-radius:999px;background:#13A65B;border:2px solid #fff;display:flex;align-items:center;justify-content:center;color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.55)">${CAM_SVG.replace(/14/g, '12')}</span>
    </div>`;
  }
  return `<div class="sg-pin" style="width:24px;height:24px">
    <span style="position:absolute;inset:3px;border-radius:999px;background:#5B6B84;border:2px solid #E8EEF6;box-shadow:0 2px 6px rgba(0,0,0,.5)"></span>
  </div>`;
}

export const CommandMap: React.FC<CommandMapProps> = ({
  cameras,
  selectedCamera,
  onSelectCamera,
  activeAlerts = [],
  trackPoints = [],
  searchedPlate,
  className = 'h-full w-full',
  focusDistrict,
  onClearRoute,
  routeNote,
  focus = null,
  onFocusDone,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const baseGroupRef = useRef<L.LayerGroup | null>(null);
  const markersRef = useRef<L.LayerGroup | null>(null);
  const trackRef = useRef<L.LayerGroup | null>(null);
  const onSelectRef = useRef(onSelectCamera);
  onSelectRef.current = onSelectCamera;

  const [base, setBase] = useState<BaseKey>(DEFAULT_BASE);
  const [layerMenu, setLayerMenu] = useState(false);
  const [routeInfo, setRouteInfo] = useState<RouteInfo | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const routeAnimRef = useRef<number | null>(null);
  const lastFlownIdRef = useRef<string | null | undefined>(undefined);

  // ---- init ---------------------------------------------------------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, {
      center: GUJARAT.center,
      zoom: GUJARAT.zoom,
      zoomControl: false,
      attributionControl: false,
      zoomSnap: 0.5,
    });
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    L.control.attribution({ position: 'bottomleft', prefix: false }).addTo(map);
    baseGroupRef.current = L.layerGroup().addTo(map);
    markersRef.current = L.layerGroup().addTo(map);
    trackRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(containerRef.current);
    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ---- base layer (satellite by default on every mount) --------------------
  useEffect(() => {
    const map = mapRef.current;
    const group = baseGroupRef.current;
    if (!map || !group) return;
    group.clearLayers();
    const cfg = BASE_LAYERS[base];
    L.tileLayer(cfg.base, { maxZoom: cfg.maxZoom, maxNativeZoom: 18, attribution: cfg.attribution }).addTo(group);
    cfg.overlays.forEach((u) => L.tileLayer(u, { maxZoom: cfg.maxZoom, maxNativeZoom: 18, opacity: 0.95 }).addTo(group));
  }, [base]);

  // ---- district focus (only when the user picks a district) ---------------
  useEffect(() => {
    if (!mapRef.current || !focusDistrict || focusDistrict === 'All Districts') return;
    const t = DISTRICT_COORDINATES[focusDistrict];
    if (t) mapRef.current.flyTo(t.center, t.zoom, { duration: 1.1 });
  }, [focusDistrict]);

  // ---- camera markers ------------------------------------------------------
  useEffect(() => {
    const layer = markersRef.current;
    if (!mapRef.current || !layer) return;
    layer.clearLayers();

    const alertByCam = new Map<string, Alert>();
    activeAlerts.forEach((a) => { if (!a.is_acknowledged && !alertByCam.has(a.camera_id)) alertByCam.set(a.camera_id, a); });

    cameras.forEach((cam) => {
      if (cam.latitude == null || cam.longitude == null || isNaN(cam.latitude) || isNaN(cam.longitude)) return;
      const alert = alertByCam.get(cam.camera_id);
      const isSelected = selectedCamera?.camera_id === cam.camera_id;
      const online = cam.status === 'online';
      const kind = alert ? 'alert' : isSelected ? 'selected' : online ? 'online' : 'offline';
      const size = kind === 'alert' || kind === 'selected' ? 40 : kind === 'online' ? 28 : 24;

      const marker = L.marker([cam.latitude, cam.longitude], {
        icon: L.divIcon({ html: markerHtml(kind), className: 'custom-leaflet-cam-marker', iconSize: [size, size], iconAnchor: [size / 2, size / 2] }),
        zIndexOffset: kind === 'alert' ? 900 : kind === 'selected' ? 800 : online ? 200 : 0,
        riseOnHover: true,
      });
      marker.bindTooltip(`${esc(cam.location_name)}`, { direction: 'top', offset: [0, -size / 2] });

      const statusBadge = alert
        ? '<span class="badge badge-crit">Watchlist match</span>'
        : online
        ? '<span class="badge badge-ok"><span class="dot dot-live"></span>Online</span>'
        : '<span class="badge badge-off"><span class="dot dot-off"></span>Offline</span>';
      const box = document.createElement('div');
      box.style.minWidth = '260px';
      box.innerHTML = `
        <div style="padding:12px 14px;border-bottom:1px solid var(--color-line);display:flex;justify-content:space-between;align-items:center;gap:8px">
          <span style="font-weight:600;color:var(--color-accent2)">${esc(cam.camera_id)}</span>${statusBadge}
        </div>
        <div style="padding:12px 14px;display:flex;flex-direction:column;gap:10px">
          ${alert ? `<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><span class="plate"><span>${esc(prettyPlate(alert.matched_value))}</span></span><span style="color:#FFC9CB;font-size:12px;font-weight:600">${esc(alert.reason)}</span></div>` : ''}
          <div>
            <div style="font-size:14px;font-weight:600;color:var(--color-ink)">${esc(cam.location_name)}</div>
            <div style="font-size:12px;color:var(--color-ink3);margin-top:2px">${esc(cam.department || '')}</div>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;font-size:12px">
            <div><span style="color:var(--color-ink3)">City</span><br><b style="color:var(--color-ink);font-weight:600">${esc(cam.city || '—')}</b></div>
            <div><span style="color:var(--color-ink3)">Stream</span><br><b style="color:var(--color-ink);font-weight:600">${esc(cam.format || '—')}</b></div>
          </div>
          <button data-open class="btn ${alert ? 'btn-danger' : 'btn-primary'} btn-sm" style="width:100%">${alert ? 'Open alert camera' : 'Open live feed'}</button>
        </div>`;
      box.querySelector('[data-open]')?.addEventListener('click', () => {
        onSelectRef.current(cam, !!alert);
        mapRef.current?.closePopup();
      });
      marker.bindPopup(box, { closeButton: true, autoPan: true, autoPanPadding: [40, 60] });
      marker.on('click', () => onSelectRef.current(cam, !!alert));
      marker.addTo(layer);
    });
  }, [cameras, selectedCamera?.camera_id, activeAlerts]);

  // ---- fly to a camera only when the user picks a different one ------------
  // The first value seen on mount is remembered without moving the map, so
  // opening the Control Room (or switching layout) never auto-zooms.
  useEffect(() => {
    const id = selectedCamera?.camera_id ?? null;
    if (lastFlownIdRef.current === undefined) {
      lastFlownIdRef.current = id;
      return;
    }
    if (!mapRef.current || !selectedCamera || id === lastFlownIdRef.current) {
      lastFlownIdRef.current = id;
      return;
    }
    lastFlownIdRef.current = id;
    if (Number.isFinite(selectedCamera.latitude) && Number.isFinite(selectedCamera.longitude)) {
      mapRef.current.flyTo([selectedCamera.latitude, selectedCamera.longitude], Math.max(mapRef.current.getZoom(), 15), { duration: 1.1 });
    }
  }, [selectedCamera?.camera_id]);

  // ---- "Show camera" from Plate Reads / Route History / Alerts ------------
  // A gentle zoom (city-street level), done once, then the request is cleared
  // so switching layouts later does not zoom again.
  useEffect(() => {
    if (!focus || !mapRef.current) return;
    const cam = cameras.find((c) => c.camera_id === focus.id);
    if (cam && Number.isFinite(cam.latitude) && Number.isFinite(cam.longitude)) {
      lastFlownIdRef.current = cam.camera_id;
      setTimeout(() => {
        const m = mapRef.current;
        if (!m) return;
        try {
          m.invalidateSize();
          m.setView([cam.latitude, cam.longitude], 13, { animate: true });
        } catch { /* map was closed meanwhile */ }
        onFocusDone?.();
      }, 250);
      return;
    }
    onFocusDone?.();
  }, [focus?.n]);

  // ---- vehicle route -------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    const layer = trackRef.current;
    const camLayer = markersRef.current;
    if (!map || !layer) return;

    layer.clearLayers();
    if (routeAnimRef.current) cancelAnimationFrame(routeAnimRef.current);
    setRouteInfo(null);

    const valid = trackPoints.filter((p) => Number.isFinite(p.latitude) && Number.isFinite(p.longitude) && (p.latitude !== 0 || p.longitude !== 0));
    if (camLayer) {
      if (valid.length > 0 && map.hasLayer(camLayer)) map.removeLayer(camLayer);
      if (valid.length === 0 && !map.hasLayer(camLayer)) camLayer.addTo(map);
    }
    if (valid.length === 0) return;
    setPanelOpen(true);

    const stops: { pt: TrackPoint; times: string[] }[] = [];
    valid.forEach((p) => {
      const last = stops[stops.length - 1];
      if (last && last.pt.camera_id === p.camera_id) last.times.push(p.timestamp);
      else stops.push({ pt: p, times: [p.timestamp] });
    });
    const latLngs: [number, number][] = stops.map((s) => [s.pt.latitude, s.pt.longitude]);
    const panelStops: RouteStop[] = stops.map((s) => ({ camera_id: s.pt.camera_id, name: s.pt.location_name || s.pt.camera_id, times: s.times }));
    const gapMin = (i: number) => {
      const a = new Date(stops[i].times[stops[i].times.length - 1]).getTime();
      const b = new Date(stops[i + 1].times[0]).getTime();
      return Number.isFinite(a) && Number.isFinite(b) ? (b - a) / 60000 : null;
    };
    const fmtConf = (c: number | null) => (c == null ? 'Manual entry' : `${c}%`);

    stops.forEach((s, index) => {
      const isFirst = index === 0;
      const isLast = index === stops.length - 1 && stops.length > 1;
      const color = isLast ? '#FF5A5F' : isFirst ? '#2ED47A' : '#4C8DFF';
      const label = isFirst ? 'A' : isLast ? '★' : String(index + 1);
      const html = `
        <div style="position:relative;width:34px;height:44px;cursor:pointer;filter:drop-shadow(0 3px 4px rgba(0,0,0,.55))">
          <div style="position:absolute;left:3px;top:2px;width:28px;height:28px;background:${color};border:2.5px solid #fff;border-radius:50% 50% 50% 0;transform:rotate(-45deg)"></div>
          <div style="position:absolute;left:0;top:6px;width:34px;text-align:center;color:#fff;font:700 13px/20px 'Inter Variable',Inter,sans-serif">${label}</div>
        </div>`;
      const icon = L.divIcon({ html, className: 'custom-track-waypoint', iconSize: [34, 44], iconAnchor: [17, 42], popupAnchor: [0, -38] });
      const marker = L.marker([s.pt.latitude, s.pt.longitude], { icon, zIndexOffset: 1000 + index });
      const tag = isFirst ? 'Start' : isLast ? 'Last seen' : `Checkpoint ${index + 1}`;
      marker.bindTooltip(`${index + 1}. ${esc(s.pt.location_name || s.pt.camera_id)}`, { direction: 'top', offset: [0, -40] });
      marker.bindPopup(`
        <div style="padding:12px 14px;min-width:240px;display:flex;flex-direction:column;gap:8px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span style="font-weight:700;color:${color}">${tag}</span>
            <span style="font-size:12px;color:var(--color-ink3)" class="num">${index + 1} of ${stops.length}</span>
          </div>
          <div style="font-size:14px;font-weight:600;color:var(--color-ink)">${esc(s.pt.location_name || s.pt.camera_id)}</div>
          <div style="display:grid;grid-template-columns:auto 1fr;gap:4px 10px;font-size:12px">
            <span style="color:var(--color-ink3)">Plate</span><span><span class="plate"><span>${esc(prettyPlate(searchedPlate))}</span></span></span>
            <span style="color:var(--color-ink3)">Camera</span><b style="font-weight:600">${esc(s.pt.camera_id)}</b>
            <span style="color:var(--color-ink3)">Seen</span><b class="num" style="font-weight:600">${s.times.map(fmtIST).join(', ')}</b>
            <span style="color:var(--color-ink3)">OCR</span><b class="num" style="font-weight:600">${fmtConf(s.pt.confidence)}</b>
          </div>
        </div>`);
      marker.addTo(layer);
    });

    map.fitBounds(L.latLngBounds(latLngs), { paddingTopLeft: [360, 90], paddingBottomRight: [60, 60], maxZoom: 14 });
    if (stops.length < 2) {
      setRouteInfo({ mode: 'straight', km: 0, stops: panelStops, legs: [] });
      return;
    }

    const drawPath = (path: [number, number][], road: boolean) => {
      const casing = L.polyline([], { color: '#0B2A66', weight: road ? 10 : 8, opacity: 0.9, lineCap: 'round', lineJoin: 'round' }).addTo(layer);
      const line = L.polyline([], road
        ? { color: '#4C8DFF', weight: 6, opacity: 1, lineCap: 'round', lineJoin: 'round' }
        : { color: '#F6C344', weight: 4, opacity: 1, dashArray: '10, 10', lineCap: 'round' }).addTo(layer);
      const car = L.marker(path[0], {
        icon: L.divIcon({ html: '<div style="width:18px;height:18px;border-radius:50%;background:#fff;border:4px solid #4C8DFF;box-shadow:0 0 0 6px rgba(76,141,255,.3)"></div>', className: 'route-car', iconSize: [18, 18], iconAnchor: [9, 9] }),
        zIndexOffset: 2000,
        interactive: false,
      }).addTo(layer);
      const total = path.length;
      const t0 = performance.now();
      const dur = Math.min(4000, 1500 + total * 2);
      const step = (now: number) => {
        const n = Math.max(2, Math.ceil(Math.min(1, (now - t0) / dur) * total));
        const part = path.slice(0, n);
        casing.setLatLngs(part);
        line.setLatLngs(part);
        car.setLatLng(part[part.length - 1]);
        if (n < total) routeAnimRef.current = requestAnimationFrame(step);
        else layer.removeLayer(car);
      };
      routeAnimRef.current = requestAnimationFrame(step);
    };

    let cancelled = false;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 45000);
    setRouteInfo({ mode: 'loading', stops: panelStops, legs: [] });
    const pts = latLngs.slice(0, 25);
    const showRoad = (path: [number, number][], distance: number, duration: number, rlegs: any[]) => {
      drawPath(path, true);
      const legs: RouteLeg[] = latLngs.slice(1).map((p, i) => {
        const leg = rlegs?.[i];
        return leg ? { km: leg.distance / 1000, minutes: leg.duration / 60, gapMin: gapMin(i) } : { km: haversineKm(latLngs[i], p), minutes: null, gapMin: gapMin(i) };
      });
      setRouteInfo({ mode: 'road', km: distance / 1000, minutes: duration / 60, stops: panelStops, legs });
    };
    apiService
      .getRoadRoute(pts)
      .then(async (res) => {
        if (cancelled) return;
        if (res?.ok && res.geometry?.length) {
          showRoad(res.geometry, res.distance, res.duration, res.legs);
          return;
        }
        const backendErr = res?.error || 'backend router failed';
        try {
          const coords = pts.map(([lat, lon]) => `${lon},${lat}`).join(';');
          const r = await fetch(`${OSRM_URL}${coords}?overview=full&geometries=geojson`, { signal: ctrl.signal });
          const data = r.ok ? await r.json() : null;
          const route = data?.routes?.[0];
          if (cancelled) return;
          if (!route?.geometry?.coordinates?.length) throw new Error(r.ok ? 'no route' : `HTTP ${r.status}`);
          showRoad(route.geometry.coordinates.map((c: [number, number]) => [c[1], c[0]] as [number, number]), route.distance, route.duration, route.legs);
        } catch (e: any) {
          if (cancelled) return;
          drawPath(latLngs, false);
          const legs = latLngs.slice(1).map((p, i) => ({ km: haversineKm(latLngs[i], p), minutes: null, gapMin: gapMin(i) }));
          const km = legs.reduce((acc, l) => acc + l.km, 0);
          setRouteInfo({ mode: 'straight', km, stops: panelStops, legs, error: `${backendErr}; browser: ${e?.name === 'AbortError' ? 'timeout' : e?.message || 'failed'}` });
        }
      })
      .finally(() => clearTimeout(timer));

    return () => {
      cancelled = true;
      ctrl.abort();
      clearTimeout(timer);
      if (routeAnimRef.current) cancelAnimationFrame(routeAnimRef.current);
    };
  }, [trackPoints, searchedPlate]);

  const focusStop = (i: number) => {
    const map = mapRef.current;
    const layer = trackRef.current;
    if (!map || !layer) return;
    let k = 0;
    layer.eachLayer((l) => {
      if (l instanceof L.Marker && (l.options.icon as L.DivIcon)?.options?.className === 'custom-track-waypoint') {
        if (k === i) {
          map.flyTo(l.getLatLng(), Math.max(map.getZoom(), 15), { duration: 0.8 });
          l.openPopup();
        }
        k += 1;
      }
    });
  };

  const onlineCount = cameras.filter((c) => c.status === 'online').length;
  const alertCount = activeAlerts.filter((a) => !a.is_acknowledged).length;
  const showRoute = !!routeInfo && trackPoints.length > 0;

  return (
    <div className={`relative ${className} overflow-hidden bg-[#DCE3EC]`}>
      <div ref={containerRef} className="h-full w-full" />

      {/* Top-left: map status (hidden while a route panel is open, to avoid overlap) */}
      {!showRoute && (
        <div className="absolute top-3 left-3 z-[400] flex items-center gap-3 h-9 px-3 rounded-full bg-[rgba(255,255,255,.94)] backdrop-blur-md border border-line2 text-[12px] font-semibold text-ink shadow-[var(--shadow-1)]">
          <span className="flex items-center gap-1.5"><span className="dot dot-live" />Online <span className="num">{onlineCount}</span></span>
          <span className="w-px h-4 bg-line2" />
          <span className="flex items-center gap-1.5 text-ink2"><span className="dot dot-off" />Offline <span className="num">{Math.max(0, cameras.length - onlineCount)}</span></span>
          {alertCount > 0 && (
            <>
              <span className="w-px h-4 bg-line2" />
              <span className="flex items-center gap-1.5 text-crit"><span className="dot dot-crit live-pulse" />Alerts <span className="num">{alertCount}</span></span>
            </>
          )}
        </div>
      )}

      {/* Top-right: view controls */}
      <div className="absolute top-3 right-3 z-[400] flex items-center gap-2">
        <button
          onClick={() => mapRef.current?.flyTo(GUJARAT.center, GUJARAT.zoom, { duration: 1.1 })}
          title="Show all of Gujarat"
          className="h-9 px-3 rounded-full bg-[rgba(255,255,255,.94)] backdrop-blur-md border border-line2 text-[12px] font-semibold text-ink flex items-center gap-1.5 hover:bg-raised transition-colors shadow-[var(--shadow-1)]"
        >
          <Locate className="w-4 h-4" />
          <span className="hidden sm:inline">Gujarat</span>
        </button>
        <div className="relative">
          <button
            onClick={() => setLayerMenu((v) => !v)}
            aria-expanded={layerMenu}
            title="Map style"
            className="h-9 px-3 rounded-full bg-[rgba(255,255,255,.94)] backdrop-blur-md border border-line2 text-[12px] font-semibold text-ink flex items-center gap-1.5 hover:bg-raised transition-colors shadow-[var(--shadow-1)]"
          >
            <Layers className="w-4 h-4" />
            <span className="hidden sm:inline">{BASE_LAYERS[base].short}</span>
          </button>
          {layerMenu && (
            <div className="pop-in absolute right-0 top-11 w-56 panel p-1.5 z-50">
              <div className="eyebrow px-2.5 py-1.5">Map style</div>
              {(Object.keys(BASE_LAYERS) as BaseKey[]).map((k) => {
                const Icon = k === 'hybrid' || k === 'satellite' ? Satellite : MapIcon;
                return (
                  <button
                    key={k}
                    onClick={() => { setBase(k); setLayerMenu(false); }}
                    className={`w-full flex items-center gap-2.5 h-9 px-2.5 rounded-lg text-[13px] font-semibold transition-colors ${base === k ? 'bg-accentbg text-accent2' : 'text-ink2 hover:bg-raised hover:text-ink'}`}
                  >
                    {k === 'osm' ? <BrandLogo name="openstreetmap" size={16} /> : <Icon className="w-4 h-4" />}
                    <span className="flex-1 text-left">{BASE_LAYERS[k].name}</span>
                    {base === k && <Check className="w-4 h-4 text-accent2" />}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Route directions panel */}
      {showRoute && routeInfo && (
        <div className="pop-in absolute top-3 left-3 z-[450] w-[min(340px,calc(100%-24px))] max-h-[calc(100%-24px)] panel flex flex-col overflow-hidden">
          <div className="px-4 pt-3.5 pb-3 border-b border-line">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex items-center gap-3">
                <span className="icon-box icon-box-sm" style={{ color: 'var(--color-accent2)', background: 'var(--color-accentbg)', borderColor: '#B9CEF7' }}><RouteIcon /></span>
                <div className="min-w-0">
                  <div className="eyebrow">Vehicle route</div>
                  <span className="plate mt-1"><span>{prettyPlate(searchedPlate)}</span></span>
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <button onClick={() => setPanelOpen((v) => !v)} className="btn btn-ghost btn-sm btn-icon" title={panelOpen ? 'Hide steps' : 'Show steps'}>
                  {panelOpen ? <ChevronUp /> : <ChevronDown />}
                </button>
                {onClearRoute && (
                  <button onClick={onClearRoute} title="Close route" className="btn btn-ghost btn-sm btn-icon"><X /></button>
                )}
              </div>
            </div>
            <div className="mt-3">
              {routeInfo.mode === 'loading' && <div className="text-[13px] text-ink2">Finding the road route…</div>}
              {routeInfo.mode === 'road' && (
                <>
                  <div className="flex items-baseline gap-2">
                    <span className="text-[20px] font-bold text-ok num">{fmtDuration(routeInfo.minutes || 0)}</span>
                    <span className="text-[13px] font-semibold text-ink2 num">{(routeInfo.km || 0).toFixed(1)} km</span>
                  </div>
                  <div className="text-[12px] text-ink3 mt-0.5">By road through {routeInfo.stops.length} checkpoints · normal traffic</div>
                </>
              )}
              {routeInfo.mode === 'straight' && (
                <div className="text-[13px] text-ink2">
                  {routeInfo.stops.length > 1 ? <><span className="num font-semibold text-ink">{(routeInfo.km || 0).toFixed(1)} km</span> straight-line (road router not reachable)</> : 'Seen at one camera only'}
                </div>
              )}
            </div>
          </div>
          {routeNote && <div className="px-4 py-2.5 text-[12px] leading-snug font-medium text-med bg-medbg border-b border-[#F0D78A]">{routeNote}</div>}
          {panelOpen && (
            <ol className="scroll-y px-2 py-2">
              {routeInfo.stops.map((st, i) => {
                const isFirst = i === 0;
                const isLast = i === routeInfo.stops.length - 1 && routeInfo.stops.length > 1;
                const color = isLast ? '#FF5A5F' : isFirst ? '#2ED47A' : '#4C8DFF';
                const leg = routeInfo.legs[i];
                return (
                  <li key={`${st.camera_id}-${i}`}>
                    <button onClick={() => focusStop(i)} className="w-full flex items-start gap-3 text-left rounded-lg px-2 py-2 hover:bg-raised transition-colors">
                      <span className="mt-0.5 shrink-0 w-6 h-6 rounded-full text-white text-[11px] font-bold flex items-center justify-center" style={{ background: color }}>
                        {isFirst ? 'A' : isLast ? '★' : i + 1}
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-semibold text-ink truncate">{st.name}</span>
                        <span className="block text-[12px] text-ink3 num">
                          {isFirst ? 'Start · ' : isLast ? 'Last seen · ' : ''}{fmtIST(st.times[0])} · {st.camera_id}
                        </span>
                      </span>
                    </button>
                    {leg && (
                      <div className="ml-[19px] border-l-2 border-dashed border-line2 pl-5 py-1 text-[12px] text-ink2 num">
                        {leg.km.toFixed(1)} km{routeInfo.mode === 'road' ? ' by road' : ' straight-line'}
                        {leg.minutes != null && ` · ~${fmtDuration(leg.minutes)} drive`}
                        {leg.gapMin != null && leg.gapMin >= 0 && <span className="block text-ink3">seen {fmtDuration(leg.gapMin)} later</span>}
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}
    </div>
  );
};
