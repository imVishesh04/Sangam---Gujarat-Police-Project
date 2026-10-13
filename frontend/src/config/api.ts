// API and Real-Time Socket.IO Configuration
// Configurable base URL for Gujarat CCTV Backend (Flask REST API + SocketIO)

export const API_CONFIG = {
  BASE_URL: (import.meta as any).env?.VITE_API_BASE_URL || 'http://localhost:5000',
  SOCKET_URL: (import.meta as any).env?.VITE_SOCKET_URL || 'http://localhost:5000',
  ENABLE_SIMULATION_FALLBACK: false, // Seamlessly streams realistic events when backend server is offline
  DEFAULT_MAP_CENTER: [22.8, 71.8] as [number, number], // Gujarat State Geographic Center
  DEFAULT_MAP_ZOOM: 8,
};

export const STORAGE_KEYS = {
  AUTH_USER: 'guj_cctv_auth_user',
  CAMERAS: 'guj_cctv_cameras_v1',
  WATCHLIST: 'guj_cctv_watchlist_v1',
  ALERTS: 'guj_cctv_alerts_v1',
  DETECTIONS: 'guj_cctv_detections_v1',
  AUDIO_MUTED: 'guj_cctv_audio_muted',
};
