export type UserRole = 'admin' | 'viewer';

export interface User {
  id: string;
  username: string;
  role: UserRole;
  fullName: string;
  badgeNumber: string;
  station: string;
  department: string;
}

export type CameraStatus = 'online' | 'offline' | 'warning';

export interface Camera {
  camera_id: string;
  department: string;
  location_name: string;
  city: string;
  district: string;
  latitude: number;
  longitude: number;
  vendor: string;
  stream_url: string;
  format: 'HLS' | 'RTSP' | 'MP4' | 'WebRTC';
  status: CameraStatus;
  resolution?: string;
  fps?: number;
  last_heartbeat?: string;
  active_alerts_count?: number;
  /** true while the backend ANPR worker for this camera is running */
  ingesting?: boolean;
}

export interface Detection {
  detection_id: string | number;
  camera_id: string;
  location_name?: string;
  city?: string;
  plate_number: string;
  confidence: number;
  timestamp: string;
  image_url?: string;
  matched_watchlist?: boolean;
}

export type WatchlistType = 'vehicle' | 'person';
export type AlertSeverity = 'critical' | 'high' | 'medium' | 'low';

export interface WatchlistItem {
  watchlist_id: number | string;
  type: WatchlistType;
  value: string; // e.g. "GJ01AB1234"
  reason: string;
  added_date: string;
  severity: AlertSeverity;
  fir_number?: string;
  department: string;
  status: 'active' | 'archived';
  notes?: string;
}

export interface Alert {
  alert_id: number | string;
  id?: number | string;
  camera_id: string;
  location_name: string;
  city?: string;
  watchlist_id: number | string;
  matched_value: string;
  timestamp: string;
  reason: string;
  severity: AlertSeverity;
  severity_why?: string;
  fir_number?: string;
  confidence?: number;
  snapshot_url?: string;
  is_acknowledged?: boolean;
  acknowledged_at?: string;
  acknowledged_by?: string;
  action_taken?: string;
  dispatch_status?: 'pending' | 'dispatched' | 'intercepted' | 'resolved';
}

export interface TrackPoint {
  camera_id: string;
  location_name: string;
  city?: string;
  latitude: number;
  longitude: number;
  timestamp: string;
  detection_id: string | number;
  snapshot_url?: string;
  confidence: number | null; // percent; null = manual sighting (no OCR)
}

export interface HealthStats {
  cameras_online: number;
  cameras_offline: number;
  total_cameras: number;
  detections_today: number;
  alerts_today: number;
  active_watchlists: number;
  database_engine?: string;
  server_time?: string;
  live_statuses?: Record<string, string>;
}

export type ActiveTab = 'dashboard' | 'cameras' | 'alerts' | 'tracking' | 'watchlist' | 'detections' | 'analytics';
