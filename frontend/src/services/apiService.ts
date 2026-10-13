import { API_CONFIG, STORAGE_KEYS } from '../config/api';
import {
  User,
  Camera,
  Detection,
  WatchlistItem,
  Alert,
  TrackPoint,
  HealthStats,
} from '../types';
import {
  INITIAL_CAMERAS,
  INITIAL_WATCHLIST,
  INITIAL_ALERTS,
  INITIAL_DETECTIONS,
  INITIAL_HEALTH,
} from '../data/mockData';

// Local storage helpers for offline/demo robustness
function getStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (raw) return JSON.parse(raw);
  } catch (e) {
    console.warn(`Failed reading storage for ${key}`, e);
  }
  return fallback;
}

function setStored<T>(key: string, data: T): void {
  try {
    localStorage.setItem(key, JSON.stringify(data));
  } catch (e) {
    console.warn(`Failed writing storage for ${key}`, e);
  }
}

const GUJARAT_CITIES = [
  { name: 'Ahmedabad', latitude: 23.0225, longitude: 72.5714 },
  { name: 'Amreli', latitude: 21.6032, longitude: 71.2218 },
  { name: 'Anand', latitude: 22.5645, longitude: 72.9289 },
  { name: 'Bhavnagar', latitude: 21.7645, longitude: 72.1519 },
  { name: 'Gandhinagar', latitude: 23.2156, longitude: 72.6369 },
  { name: 'Jamnagar', latitude: 22.4707, longitude: 70.0577 },
  { name: 'Junagadh', latitude: 21.5222, longitude: 70.4579 },
  { name: 'Mehsana', latitude: 23.588, longitude: 72.3693 },
  { name: 'Navsari', latitude: 20.9467, longitude: 72.952 },
  { name: 'Patan', latitude: 23.8493, longitude: 72.1266 },
  { name: 'Porbandar', latitude: 21.6425, longitude: 69.6293 },
  { name: 'Rajkot', latitude: 22.3039, longitude: 70.8022 },
  { name: 'Surat', latitude: 21.1702, longitude: 72.8311 },
  { name: 'Vadodara', latitude: 22.3072, longitude: 73.1812 },
  { name: 'Valsad', latitude: 20.5992, longitude: 72.9342 },
];

function inferCity(camera: Camera): string {
  if (camera.city && camera.city !== 'Gujarat' && camera.city !== 'Statewide') return camera.city;
  const location = (camera.location_name || '').toLowerCase();
  const byName = GUJARAT_CITIES.find((city) => location.includes(city.name.toLowerCase()));
  if (byName) return byName.name;
  return GUJARAT_CITIES.reduce((closest, city) => {
    const distance = Math.hypot((camera.latitude || 0) - city.latitude, (camera.longitude || 0) - city.longitude);
    return distance < closest.distance ? { name: city.name, distance } : closest;
  }, { name: 'Gujarat', distance: Number.POSITIVE_INFINITY }).name;
}

function normalizeCamera(camera: Camera): Camera {
  const city = inferCity(camera);
  return {
    ...camera,
    city,
    district: camera.district || city,
    vendor: camera.vendor || 'Sentinel Government Feed',
    format: (camera.format || 'hls').toUpperCase() as Camera['format'],
  };
}

// Other resources retain a local fallback for offline demo mode. Camera data
// is never bootstrapped here so a backend response always remains authoritative.
if (!localStorage.getItem(STORAGE_KEYS.WATCHLIST)) {
  setStored(STORAGE_KEYS.WATCHLIST, INITIAL_WATCHLIST);
}
if (!localStorage.getItem(STORAGE_KEYS.ALERTS)) {
  setStored(STORAGE_KEYS.ALERTS, INITIAL_ALERTS);
}
if (!localStorage.getItem(STORAGE_KEYS.DETECTIONS)) {
  setStored(STORAGE_KEYS.DETECTIONS, INITIAL_DETECTIONS);
}

class ApiService {
  private baseUrl = API_CONFIG.BASE_URL;
  public isBackendOnline = false;

  private async fetchWithTimeout(endpoint: string, options: RequestInit = {}, timeoutMs = 10000): Promise<Response> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, {
        ...options,
        signal: controller.signal,
        credentials: 'include', // Session cookie support
        headers: {
          'Content-Type': 'application/json',
          ...(options.headers || {}),
        },
      });
      clearTimeout(timeoutId);
      this.isBackendOnline = true;
      return response;
    } catch (err) {
      clearTimeout(timeoutId);
      this.isBackendOnline = false;
      throw err;
    }
  }

  // =================== AUTH ===================
  async login(credentials: { username: string; password: string }): Promise<User> {
    // Sign-in is decided ONLY by the backend. (The old version created a local
    // admin user whenever the request failed - including on a wrong password.)
    let res: Response;
    try {
      res = await this.fetchWithTimeout('/auth/login', {
        method: 'POST',
        body: JSON.stringify(credentials),
      });
    } catch (e) {
      throw new Error('Cannot reach the SANGAM server. Check that the backend (python app.py) is running.');
    }
    if (res.status === 429) {
      throw new Error('Too many sign-in attempts. Wait a minute and try again.');
    }
    if (!res.ok) {
      throw new Error('Incorrect username or password.');
    }
    const data = await res.json();
    const user: User = {
      id: data.id || 'usr-101',
      username: data.username,
      role: data.role === 'admin' ? 'admin' : 'viewer',
      fullName: data.role === 'admin' ? 'Admin (demo account)' : 'Viewer (demo account)',
      badgeNumber: data.role === 'admin' ? 'admin' : 'viewer',
      station: 'Gujarat Police State Command Center (Gandhinagar)',
      department: 'Gujarat State Police HQ',
    };
    setStored(STORAGE_KEYS.AUTH_USER, user);
    return user;
  }

  async logout(): Promise<void> {
    try {
      await this.fetchWithTimeout('/auth/logout', { method: 'POST' });
    } catch (e) {
      // ignore
    }
    localStorage.removeItem(STORAGE_KEYS.AUTH_USER);
  }

  async getMe(): Promise<User | null> {
    try {
      const res = await this.fetchWithTimeout('/auth/me');
      if (res.ok) {
        const data = await res.json();
        return data;
      }
      // Server explicitly said we're not authenticated (401) — trust that,
      // don't resurrect a possibly-stale cached user from a previous session.
      return null;
    } catch (e) {
      // Request itself failed (network/CORS/timeout) — fail safe to
      // logged-out rather than trusting stale local data.
      return null;
    }
  }

  // =================== CAMERAS ===================
  async getCameras(): Promise<Camera[]> {
    try {
      const res = await this.fetchWithTimeout('/cameras/');
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data) && data.length > 0) {
          const normalized = data.map(normalizeCamera);
          setStored(STORAGE_KEYS.CAMERAS, normalized);
          return normalized;
        }
      }
    } catch (e) {
      // fallback
    }
    return getStored<Camera[]>(STORAGE_KEYS.CAMERAS, INITIAL_CAMERAS)
      .map(normalizeCamera)
      .map((camera) => ({ ...camera, status: 'offline' }));
  }

  async getCamerasNearby(
    lat: number,
    lng: number,
    radiusKm: number = 5.0
  ): Promise<{ engine: string; center: { latitude: number; longitude: number }; radius_km: number; count: number; cameras: (Camera & { distance_km: number })[] }> {
    try {
      const res = await this.fetchWithTimeout(`/cameras/nearby?lat=${lat}&lng=${lng}&radius_km=${radiusKm}`);
      if (res.ok) {
        const data = await res.json();
        return {
          ...data,
          cameras: (data.cameras || []).map((c: any) => ({
            ...normalizeCamera(c),
            distance_km: c.distance_km,
          })),
        };
      }
    } catch (e) {
      console.warn('Failed to fetch nearby cameras', e);
    }
    return {
      engine: 'offline_fallback',
      center: { latitude: lat, longitude: lng },
      radius_km: radiusKm,
      count: 0,
      cameras: [],
    };
  }

  async addCamera(camera: Partial<Camera>): Promise<Camera> {
    try {
      const res = await this.fetchWithTimeout('/cameras/', {
        method: 'POST',
        body: JSON.stringify(camera),
      });
      if (res.ok) {
        return await res.json();
      }
    } catch (e) {
      // fallback
    }

    const current = getStored<Camera[]>(STORAGE_KEYS.CAMERAS, INITIAL_CAMERAS);
    const newCam: Camera = {
      camera_id: camera.camera_id || `CAM-GUJ-${Math.floor(100 + Math.random() * 900)}`,
      department: camera.department || 'Gujarat State Police (State HQ)',
      location_name: camera.location_name || 'New Junction Checkpost',
      city: camera.city || 'Ahmedabad',
      district: camera.district || 'Ahmedabad',
      latitude: camera.latitude || 23.0225,
      longitude: camera.longitude || 72.5714,
      vendor: camera.vendor || 'Hikvision ANPR 4K',
      stream_url: camera.stream_url || 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4',
      format: camera.format || 'HLS',
      status: camera.status || 'offline',
      resolution: camera.resolution || '1920x1080',
      fps: 30,
      last_heartbeat: new Date().toISOString(),
      active_alerts_count: 0,
    };
    const updated = [newCam, ...current];
    setStored(STORAGE_KEYS.CAMERAS, updated);
    return newCam;
  }

  async updateCamera(id: string, updates: Partial<Camera>): Promise<Camera> {
    try {
      const res = await this.fetchWithTimeout(`/cameras/${id}`, {
        method: 'PUT',
        body: JSON.stringify(updates),
      });
      if (res.ok) return await res.json();
    } catch (e) {
      // fallback
    }

    const current = getStored<Camera[]>(STORAGE_KEYS.CAMERAS, INITIAL_CAMERAS);
    const index = current.findIndex((c) => c.camera_id === id);
    if (index !== -1) {
      current[index] = { ...current[index], ...updates };
      setStored(STORAGE_KEYS.CAMERAS, current);
      return current[index];
    }
    throw new Error('Camera not found');
  }

  async deleteCamera(id: string): Promise<boolean> {
    try {
      const res = await this.fetchWithTimeout(`/cameras/${id}`, { method: 'DELETE' });
      if (res.ok) return true;
    } catch (e) {
      // fallback
    }
    const current = getStored<Camera[]>(STORAGE_KEYS.CAMERAS, INITIAL_CAMERAS);
    const filtered = current.filter((c) => c.camera_id !== id);
    setStored(STORAGE_KEYS.CAMERAS, filtered);
    return true;
  }

  async importCamerasCSV(csvContent: string): Promise<{ imported: number; errors: number }> {
    try {
      const res = await this.fetchWithTimeout('/cameras/import', {
        method: 'POST',
        body: JSON.stringify({ csv_data: csvContent }),
      });
      if (res.ok) {
        const result = await res.json();
        await this.getCameras();
        return {
          imported: result.inserted ?? result.imported ?? 0,
          errors: Array.isArray(result.errors) ? result.errors.length : 0,
        };
      }
    } catch (e) {
      // fallback
    }

    // Parse CSV lines locally
    const lines = csvContent.trim().split('\n');
    let imported = 0;
    const current = getStored<Camera[]>(STORAGE_KEYS.CAMERAS, INITIAL_CAMERAS);
    const addedList: Camera[] = [];

    // Header row skip
    const rows = lines.length > 0 && lines[0].toLowerCase().includes('camera_id') ? lines.slice(1) : lines;
    for (const row of rows) {
      const cols = row.split(',').map((c) => c.trim());
      if (cols.length >= 3) {
        const [camId, dept, loc, latStr, lngStr, vendor, url] = cols;
        const newCam: Camera = {
          camera_id: camId || `CAM-IMP-${Math.floor(1000 + Math.random() * 9000)}`,
          department: dept || 'Gujarat State Police (State HQ)',
          location_name: loc || 'Imported Checkpoint',
          city: 'Statewide',
          district: 'Gujarat',
          latitude: parseFloat(latStr) || 23.0 + Math.random() * 0.5,
          longitude: parseFloat(lngStr) || 72.5 + Math.random() * 0.5,
          vendor: vendor || 'Hikvision ANPR',
          stream_url: url || 'https://live.corp8.cloud/live/stream/6/index.m3u8',
          format: 'HLS',
          status: 'offline',
          resolution: '1920x1080',
          fps: 30,
          last_heartbeat: new Date().toISOString(),
          active_alerts_count: 0,
        };
        addedList.push(newCam);
        imported++;
      }
    }
    setStored(STORAGE_KEYS.CAMERAS, [...addedList, ...current]);
    return { imported, errors: 0 };
  }

  // Dispatch agent: best responding units for an alert, and one-click assignment
  async getDispatchSuggestion(alertId: string | number): Promise<{ ok: boolean; data: any }> {
    try {
      const res = await this.fetchWithTimeout(`/dispatch/suggest/${alertId}`);
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, data };
    } catch (e) {
      return { ok: false, data: { error: 'Server not reachable' } };
    }
  }

  async assignUnit(alertId: string | number, unitId: string): Promise<{ ok: boolean; data: any }> {
    try {
      const res = await this.fetchWithTimeout(`/dispatch/assign/${alertId}`, {
        method: 'POST',
        body: JSON.stringify({ unit_id: unitId }),
      }, 40000);
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, data };
    } catch (e) {
      return { ok: false, data: { error: 'Server not reachable' } };
    }
  }

  // Start/stop many cameras at once. ids = exact cameras, or null + scope.
  async bulkCameras(action: 'start' | 'stop', ids: string[] | null, scope: 'all' | 'offline' | 'online' = 'all'): Promise<{ ok: boolean; data: any }> {
    try {
      const res = await this.fetchWithTimeout('/cameras/bulk', {
        method: 'POST',
        body: JSON.stringify(ids ? { action, camera_ids: ids } : { action, scope }),
      }, 20000);
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, data };
    } catch (e) {
      return { ok: false, data: { error: 'Server not reachable' } };
    }
  }

  /** Live ANPR worker state for one camera: running = worker alive, status = frames arriving. */
  async getIngestStatus(id: string): Promise<{ running: boolean; status: string; last_seen: string | null } | null> {
    try {
      const res = await this.fetchWithTimeout(`/cameras/${encodeURIComponent(id)}/status`, {}, 8000);
      if (res.ok) return await res.json();
    } catch { /* ignore */ }
    return null;
  }

  /** Start/stop the backend ANPR worker. Throws on failure (no fake local status). */
  async toggleCameraStream(id: string, action: 'start' | 'stop'): Promise<Camera> {
    const res = await this.fetchWithTimeout(`/cameras/${encodeURIComponent(id)}/${action}`, { method: 'POST' }, 15000);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.error || `Could not ${action} camera (HTTP ${res.status})`);
    return normalizeCamera(data);
  }

  // =================== DETECTIONS ===================
  /** Real plate reads from the backend. Throws when the server is unreachable (no mock data). */
  async getDetections(filters?: { camera_id?: string; plate_number?: string; per_page?: number }): Promise<Detection[]> {
    const params = new URLSearchParams();
    if (filters?.camera_id) params.append('camera_id', filters.camera_id);
    if (filters?.plate_number) params.append('plate', filters.plate_number);
    params.append('per_page', String(filters?.per_page ?? 200));
    const res = await this.fetchWithTimeout(`/detections/?${params.toString()}`);
    if (!res.ok) throw new Error(`Plate reads could not be loaded (HTTP ${res.status})`);
    const wrapper = await res.json();
    return Array.isArray(wrapper) ? wrapper : wrapper.detections || [];
  }


  // =================== WATCHLIST ===================
  async getWatchlist(): Promise<WatchlistItem[]> {
    try {
      const res = await this.fetchWithTimeout('/watchlist/');
      if (res.ok) {
        const data = await res.json();
        setStored(STORAGE_KEYS.WATCHLIST, data);
        return data;
      }
    } catch (e) {
      // fallback
    }
    return getStored<WatchlistItem[]>(STORAGE_KEYS.WATCHLIST, INITIAL_WATCHLIST);
  }

  async addWatchlist(item: Partial<WatchlistItem>): Promise<WatchlistItem> {
    try {
      const res = await this.fetchWithTimeout('/watchlist/', {
        method: 'POST',
        body: JSON.stringify(item),
      });
      if (res.ok) return await res.json();
    } catch (e) {
      // fallback
    }

    const current = getStored<WatchlistItem[]>(STORAGE_KEYS.WATCHLIST, INITIAL_WATCHLIST);
    const newItem: WatchlistItem = {
      watchlist_id: item.watchlist_id || `WL-${Math.floor(100 + Math.random() * 900)}`,
      type: item.type || 'vehicle',
      value: (item.value || '').toUpperCase().trim(),
      reason: item.reason || 'Flagged for surveillance inquiry',
      added_date: new Date().toISOString().split('T')[0],
      severity: item.severity || 'high',
      fir_number: item.fir_number || 'FIR-2026/POLICE-HQ',
      department: item.department || 'Gujarat State Police (State HQ)',
      status: 'active',
      notes: item.notes || '',
    };
    const updated = [newItem, ...current];
    setStored(STORAGE_KEYS.WATCHLIST, updated);
    return newItem;
  }

  async deleteWatchlist(id: string): Promise<boolean> {
    try {
      const res = await this.fetchWithTimeout(`/watchlist/${id}`, { method: 'DELETE' });
      if (res.ok) return true;
    } catch (e) {
      // fallback
    }
    const current = getStored<WatchlistItem[]>(STORAGE_KEYS.WATCHLIST, INITIAL_WATCHLIST);
    const filtered = current.filter((w) => w.watchlist_id !== id);
    setStored(STORAGE_KEYS.WATCHLIST, filtered);
    return true;
  }

  // =================== ALERTS ===================
  async getAlerts(): Promise<Alert[]> {
    try {
      const res = await this.fetchWithTimeout('/alerts/');
      if (res.ok) {
        // Backend returns a paginated wrapper: { total, page, per_page, alerts }
        const wrapper = await res.json();
        const data: Alert[] = wrapper.alerts;
        const deduped = this.deduplicateAlerts(data);
        setStored(STORAGE_KEYS.ALERTS, deduped);
        return deduped;
      }
    } catch (e) {
      // fallback
    }
    const raw = getStored<Alert[]>(STORAGE_KEYS.ALERTS, INITIAL_ALERTS);
    const deduped = this.deduplicateAlerts(raw);
    setStored(STORAGE_KEYS.ALERTS, deduped);
    return deduped;
  }

  private deduplicateAlerts(alertList: Alert[]): Alert[] {
    const seen = new Set<string>();
    const result: Alert[] = [];
    for (const a of alertList) {
      if (!a || a.alert_id === undefined || a.alert_id === null) continue;
      const key = String(a.alert_id);
      if (!seen.has(key)) {
        seen.add(key);
        result.push(a);
      }
    }
    return result;
  }

  async acknowledgeAlert(alertId: string | number, officerNotes?: string): Promise<Alert> {
    try {
      const res = await this.fetchWithTimeout(`/alerts/${encodeURIComponent(String(alertId))}/acknowledge`, {
        method: 'PUT',
        body: JSON.stringify({ notes: officerNotes }),
      });
      if (res.ok) return await res.json();
    } catch (e) {
      // fallback
    }

    const current = getStored<Alert[]>(STORAGE_KEYS.ALERTS, INITIAL_ALERTS);
    const index = current.findIndex((a) => String(a.alert_id) === String(alertId));
    if (index !== -1) {
      current[index] = {
        ...current[index],
        is_acknowledged: true,
        acknowledged_at: new Date().toISOString(),
        acknowledged_by: 'Duty Officer',
        action_taken: officerNotes || 'PCR Interceptor unit alerted',
        dispatch_status: 'dispatched',
      };
      setStored(STORAGE_KEYS.ALERTS, current);
      return current[index];
    }
    throw new Error('Alert not found');
  }

  // =================== VEHICLE TRACKING ===================
  async getVehicleTrack(plateNumber: string): Promise<TrackPoint[]> {
    const cleanPlate = plateNumber.trim().toUpperCase();
    try {
      const res = await this.fetchWithTimeout(`/track/${encodeURIComponent(cleanPlate)}`);
      if (res.ok) {
        // Backend returns a wrapper: { plate_number, total_sightings, route }
        const wrapper = await res.json();
        return (wrapper.route || []).map((p: any) => ({
          ...p,
          confidence:
            p.confidence == null ? null : p.confidence <= 1 ? +(p.confidence * 100).toFixed(1) : +p.confidence,
        }));
      }
    } catch (e) {
      // fall through
    }
    // No invented routes: if the backend can't answer, show no sightings.
    return [];
  }

  // Road route through checkpoints, fetched by the backend (two OSM routers + disk cache)
  async getRoadRoute(points: [number, number][]): Promise<any> {
    try {
      const res = await this.fetchWithTimeout('/track/road-route', {
        method: 'POST',
        body: JSON.stringify({ points }),
      }, 35000);
      return await res.json().catch(() => ({ ok: false, error: `HTTP ${res.status}` }));
    } catch (e) {
      return { ok: false, error: 'backend did not answer' };
    }
  }

  // Demo: 5-7 manual test sightings at real registered cameras (online ones first).
  // The last one goes through watchlist matching -> real alert + WhatsApp.
  async generateDemoRoute(plateNumber: string, stops?: number): Promise<{ ok: boolean; data: any }> {
    try {
      const res = await this.fetchWithTimeout(`/track/demo/${encodeURIComponent(plateNumber.trim().toUpperCase())}`, {
        method: 'POST',
        body: JSON.stringify(stops ? { stops } : {}),
      });
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, data };
    } catch (e) {
      return { ok: false, data: { error: 'Server not reachable' } };
    }
  }

  // =================== HEALTH & STATS ===================
  // =================== WHATSAPP ALERT AGENT ===================
  async getNotificationStatus(): Promise<any | null> {
    try {
      const res = await this.fetchWithTimeout('/notifications/status');
      return res.ok ? await res.json() : null;
    } catch (e) {
      return null;
    }
  }

  async getRecentNotifications(): Promise<any[]> {
    try {
      const res = await this.fetchWithTimeout('/notifications/recent');
      return res.ok ? await res.json() : [];
    } catch (e) {
      return [];
    }
  }

  async sendNotificationTest(mode: 'ping' | 'latest_alert'): Promise<{ ok: boolean; data: any }> {
    try {
      const res = await this.fetchWithTimeout('/notifications/test', {
        method: 'POST',
        body: JSON.stringify({ mode }),
      }, 30000);
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, data };
    } catch (e) {
      return { ok: false, data: { error: 'Server not reachable' } };
    }
  }

  async triggerTestAlert(cameraId?: string): Promise<{ ok: boolean; data: any }> {
    try {
      const res = await this.fetchWithTimeout('/notifications/test-alert', {
        method: 'POST',
        body: JSON.stringify(cameraId ? { camera_id: cameraId } : {}),
      });
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, data };
    } catch (e) {
      return { ok: false, data: { error: 'Server not reachable' } };
    }
  }

  async getHealth(): Promise<HealthStats> {
    try {
      const res = await this.fetchWithTimeout('/health');
      if (res.ok) {
        const data = await res.json();
        const liveOnline = data.cameras?.live_online ?? data.cameras?.db_online ?? 0;
        const total = data.cameras?.db_total ?? 0;
        const offline = data.cameras?.live_offline ?? data.cameras?.db_offline ?? Math.max(0, total - liveOnline);
        return {
          cameras_online: liveOnline,
          cameras_offline: offline,
          total_cameras: total,
          detections_today: data.detections_today ?? 0,
          alerts_today: data.alerts_today ?? 0,
          active_watchlists: data.active_watchlists ?? 0,
          database_engine: 'SQLite 3 (Local Core DB)',
          server_time: data.timestamp,
          live_statuses: data.cameras?.live_statuses ?? {},
        };
      }
    } catch (e) {
      // fallback
    }

    const cameras = getStored<Camera[]>(STORAGE_KEYS.CAMERAS, INITIAL_CAMERAS);
    const online = cameras.filter((c) => c.status === 'online').length;
    const offline = cameras.length - online;
    const alerts = getStored<Alert[]>(STORAGE_KEYS.ALERTS, INITIAL_ALERTS);
    const watchlist = getStored<WatchlistItem[]>(STORAGE_KEYS.WATCHLIST, INITIAL_WATCHLIST);

    return {
      cameras_online: online,
      cameras_offline: offline,
      total_cameras: cameras.length,
      detections_today: 0,
      alerts_today: alerts.length,
      active_watchlists: watchlist.length,
      database_engine: 'SQLite 3 (Local Core DB)',
    };
  }
}

export const apiService = new ApiService();
