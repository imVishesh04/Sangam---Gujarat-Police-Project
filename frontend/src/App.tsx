import React, { useState, useEffect } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import { Sidebar } from './components/Sidebar';
import { Navbar } from './components/Navbar';
import { LoginScreen } from './components/Auth/LoginScreen';
import { CommandCenterView } from './components/Dashboard/CommandCenterView';
import { CameraDirectory } from './components/Cameras/CameraDirectory';
import { LiveAlertsPanel } from './components/Alerts/LiveAlertsPanel';
import { VehicleTracker } from './components/Tracking/VehicleTracker';
import { WatchlistManager } from './components/Watchlist/WatchlistManager';
import { DetectionsLog } from './components/Detections/DetectionsLog';
import { SystemHealthView } from './components/Analytics/SystemHealthView';
import { CameraStreamModal } from './components/Video/CameraStreamModal';
import {
  Camera,
  Alert,
  WatchlistItem,
  Detection,
  HealthStats,
  ActiveTab,
  TrackPoint,
} from './types';
import { apiService } from './services/apiService';
import { socketService } from './services/socketService';

const MainDashboard: React.FC = () => {
  const { user, role, logout } = useAuth();
  const [activeTab, setActiveTab] = useState<ActiveTab>('dashboard');

  // Application Data States
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedCamera, setSelectedCamera] = useState<Camera | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [health, setHealth] = useState<HealthStats | null>(null);

  // Cross-Navigation & Modal States
  const [trackingPlate, setTrackingPlate] = useState<string>('GJ-01-AB-1234');
  const [modalCamera, setModalCamera] = useState<Camera | null>(null);
  // Route drawn on the Control Room map from Route History > Show in Map
  const [mapRoute, setMapRoute] = useState<{ plate: string; points: TrackPoint[]; note?: string } | null>(null);
  // "Show camera" from another tab: the map flies to that camera once
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);
  const [mapFocus, setMapFocus] = useState<{ id: string; n: number } | null>(null);
  const showCameraOnMap = (camId: string) => {
    const cam = cameras.find((c) => c.camera_id === camId);
    if (!cam) {
      window.alert(`Camera ${camId} is not in the camera list.`);
      return;
    }
    setMapRoute(null);
    setSelectedCamera(cam);
    setMapFocus({ id: camId, n: Date.now() });
    setActiveTab('dashboard');
  };

  // Load Initial Data
  const loadAppData = async () => {
    try {
      const [camsData, alertsData, wlData, healthData] = await Promise.all([
        apiService.getCameras(),
        apiService.getAlerts(),
        apiService.getWatchlist(),
        apiService.getHealth(),
      ]);

      // No camera is preselected: the Control Room opens on the whole-State
      // satellite view and never auto-zooms to a camera.
      setCameras(camsData);
      setAlerts(alertsData);
      setWatchlist(wlData);
      setHealth(healthData);
    } catch (e) {
      console.error('Could not load dashboard data', e);
    }
  };

  useEffect(() => {
    loadAppData();

    // Subscribe to real-time SocketIO /alerts namespace
    const unsubAlerts = socketService.onNewAlert((newAlert) => {
      setAlerts((prev) => {
        if (prev.some((a) => a.alert_id === newAlert.alert_id)) {
          return prev;
        }
        return [newAlert, ...prev];
      });
    });

    // Subscribe to real-time SocketIO /cameras status changes
    const unsubCameras = socketService.onCameraStatusChange((statusPayload) => {
      setCameras((prev) =>
        prev.map((c) =>
          c.camera_id === statusPayload.camera_id
            ? { ...c, status: statusPayload.status }
            : c
        )
      );
      setSelectedCamera((prev) =>
        prev?.camera_id === statusPayload.camera_id
          ? { ...prev, status: statusPayload.status }
          : prev
      );
    });
    return () => {
      unsubAlerts?.();
      unsubCameras?.();
    };
  }, []);

  // Keep camera status + "plate reading on" flags fresh (socket events cover
  // status; this also refreshes the worker flag and catches missed events).
  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const [fresh, h] = await Promise.all([apiService.getCameras(), apiService.getHealth().catch(() => null)]);
        if (h) setHealth(h);
        if (fresh.length) {
          setCameras(fresh);
          setSelectedCamera((prev) => (prev ? fresh.find((c) => c.camera_id === prev.camera_id) || prev : prev));
        }
      } catch { /* ignore */ }
    }, 8000);
    return () => clearInterval(t);
  }, []);

  // Handlers
  const handleSelectCamera = (camera: Camera) => {
    setSelectedCamera(camera);
  };

  // Throws on failure so the caller can show the real error (no fake status).
  const handleToggleCameraStatus = async (camera: Camera, action: 'start' | 'stop') => {
    const updated = await apiService.toggleCameraStream(camera.camera_id, action);
    const merged = { ...camera, ...updated, ingesting: action === 'start' };
    setCameras((prev) => prev.map((c) => (c.camera_id === camera.camera_id ? merged : c)));
    setSelectedCamera((prev) => (prev?.camera_id === camera.camera_id ? merged : prev));
    const refresh = async () => setCameras(await apiService.getCameras());
    [2500, 8000].forEach((ms) => setTimeout(refresh, ms));
  };

  // Bulk start/stop: statuses then arrive over the socket; refresh a few times as a backup
  const handleBulkCameras = async (action: 'start' | 'stop', ids: string[] | null) => {
    const result = await apiService.bulkCameras(action, ids);
    const refresh = async () => setCameras(await apiService.getCameras());
    [1500, 6000, 15000, 30000].forEach((ms) => setTimeout(refresh, ms));
    return result;
  };

  const handleAddCamera = async (newCam: Partial<Camera>) => {
    const created = await apiService.addCamera(newCam);
    setCameras((prev) => [created, ...prev]);
  };

  const handleDeleteCamera = async (id: string) => {
    await apiService.deleteCamera(id);
    setCameras((prev) => prev.filter((c) => c.camera_id !== id));
    if (selectedCamera?.camera_id === id) {
      setSelectedCamera(cameras.find((c) => c.camera_id !== id) || null);
    }
  };

  const handleImportCSV = async (csvContent: string) => {
    await apiService.importCamerasCSV(csvContent);
    const refreshed = await apiService.getCameras();
    setCameras(refreshed);
  };

  const handleAcknowledgeAlert = async (alertId: string | number, notes?: string) => {
    try {
      const updated = await apiService.acknowledgeAlert(alertId, notes);
      setAlerts((prev) =>
        prev.map((a) => (String(a.alert_id) === String(alertId) ? updated : a))
      );
    } catch (e) {
      console.error(e);
    }
  };

  const handleAddWatchlist = async (item: Partial<WatchlistItem>) => {
    const created = await apiService.addWatchlist(item);
    setWatchlist((prev) => [created, ...prev]);
  };

  const handleDeleteWatchlist = async (id: string) => {
    await apiService.deleteWatchlist(id);
    setWatchlist((prev) => prev.filter((w) => w.watchlist_id !== id));
  };

  const handleTrackPlate = (plate: string) => {
    setTrackingPlate(plate);
    setActiveTab('tracking');
  };

  // Test alerts go through the real backend (manual sighting -> watchlist match),
  // so they appear on every dashboard via Socket.IO and are sent to WhatsApp.
  const runTestAlert = async (cameraId?: string) => {
    const { ok, data } = await apiService.triggerTestAlert(cameraId);
    if (!ok) {
      setToast({ ok: false, text: `Test alert failed: ${data?.error || 'unknown error'}` });
      return;
    }
    setToast({ ok: true, text: `Test alert #${data.alert_id}: ${data.plate} at ${data.location_name} (demo data)` });
    // Show it at once (the socket push may arrive later or be blocked)
    try {
      const fresh = await apiService.getAlerts();
      setAlerts(fresh);
    } catch { /* ignore */ }
  };

  const handleTriggerDemoAlert = () => {
    runTestAlert();
  };

  const handleTriggerAlertForCamera = (camera: Camera) => {
    runTestAlert(camera.camera_id);
  };

  const handleQuickSearch = (query: string) => {
    const cleanQ = query.trim().toUpperCase();
    // Check if query looks like a vehicle plate (e.g. contains numbers or GJ)
    if (cleanQ.includes('GJ') || /\d{2}/.test(cleanQ)) {
      setTrackingPlate(cleanQ);
      setActiveTab('tracking');
      return;
    }
    // Check if query matches a camera
    const foundCam = cameras.find(
      (c) =>
        c.camera_id.toUpperCase().includes(cleanQ) ||
        c.location_name.toUpperCase().includes(cleanQ)
    );
    if (foundCam) {
      setSelectedCamera(foundCam);
      setActiveTab('dashboard');
      return;
    }
    // Fallback: open detections search
    setActiveTab('detections');
  };

  const unacknowledgedAlertsCount = alerts.filter((a) => !a.is_acknowledged).length;

  return (
    <div className="h-screen w-screen flex bg-base text-ink overflow-hidden">
      {/* Tactical Left Sidebar Navigation */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        unacknowledgedAlertsCount={unacknowledgedAlertsCount}
      />

      {/* Main Viewport Workspace Container */}
      <div className="flex-1 flex flex-col min-w-0 h-full overflow-hidden">
        {/* Top Header Controls Bar */}
        <Navbar
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          unacknowledgedAlertsCount={unacknowledgedAlertsCount}
          onTriggerDemoAlert={handleTriggerDemoAlert}
          onQuickSearch={handleQuickSearch}
        />

        {/* Main Viewport */}
        <main key={activeTab} className="flex-1 overflow-hidden relative p-4 fade-in">
          {activeTab === 'dashboard' && (
            <CommandCenterView
              cameras={cameras}
              selectedCamera={selectedCamera}
              onSelectCamera={handleSelectCamera}
              alerts={alerts}
              health={health}
              onAcknowledgeAlert={handleAcknowledgeAlert}
              onToggleCameraStatus={(cam, action) => { handleToggleCameraStatus(cam, action).catch((e) => window.alert(e.message)); }}
              onTrackPlate={handleTrackPlate}
              onOpenStreamModal={(cam) => setModalCamera(cam)}
              onTriggerDemoAlert={handleTriggerDemoAlert}
              routeOverlay={mapRoute}
              mapFocus={mapFocus}
              onFocusDone={() => setMapFocus(null)}
              onClearRoute={() => setMapRoute(null)}
            />
          )}

          {activeTab === 'cameras' && (
            <CameraDirectory
              cameras={cameras}
              role={role}
              onSelectCamera={(cam) => {
                setSelectedCamera(cam);
                setModalCamera(cam);
              }}
              onToggleStatus={handleToggleCameraStatus}
              onBulkAction={handleBulkCameras}
              onAddCamera={handleAddCamera}
              onDeleteCamera={handleDeleteCamera}
              onImportCSV={handleImportCSV}
            />
          )}

          {activeTab === 'alerts' && (
            <div className="h-full">
              <LiveAlertsPanel
                alerts={alerts}
                onAcknowledgeAlert={handleAcknowledgeAlert}
                onTrackPlate={handleTrackPlate}
                onSelectCamera={showCameraOnMap}
                onTriggerDemoAlert={handleTriggerDemoAlert}
              />
            </div>
          )}

          {activeTab === 'tracking' && (
            <div className="h-full">
              <VehicleTracker
                initialPlate={trackingPlate}
                onShowInMap={(plate, points, note) => {
                  setSelectedCamera(null);
                  setMapRoute({ plate, points, note });
                  setActiveTab('dashboard');
                }}
                onSelectCamera={showCameraOnMap}
              />
            </div>
          )}

          {activeTab === 'watchlist' && (
            <div className="h-full">
              <WatchlistManager
                watchlist={watchlist}
                role={role}
                onAddWatchlist={handleAddWatchlist}
                onDeleteWatchlist={handleDeleteWatchlist}
                onTrackPlate={handleTrackPlate}
              />
            </div>
          )}

          {activeTab === 'detections' && (
            <div className="h-full">
              <DetectionsLog
                onTrackPlate={handleTrackPlate}
                onSelectCamera={showCameraOnMap}
              />
            </div>
          )}

          {activeTab === 'analytics' && (
            <div className="h-full">
              <SystemHealthView />
            </div>
          )}
        </main>
      </div>

      {toast && (
        <div className="fixed bottom-5 inset-x-0 z-[1100] flex justify-center pointer-events-none">
        <div role="status" className={`pop-in pointer-events-auto max-w-[560px] px-4 py-3 rounded-xl border text-[13px] font-semibold shadow-[var(--shadow-2)] ${toast.ok ? 'bg-okbg border-[#A9DEC3] text-ok' : 'bg-critbg border-[#F5C2BD] text-crit'}`}>
          {toast.text}
        </div>
        </div>
      )}

      {/* Expanded Camera PTZ Modal */}
      {modalCamera && (
        <CameraStreamModal
          camera={modalCamera}
          onClose={() => setModalCamera(null)}
          onTriggerAlertForCamera={handleTriggerAlertForCamera}
        />
      )}
    </div>
  );
};

export default function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

const AppContent: React.FC = () => {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="h-screen w-screen bg-base flex items-center justify-center">
        <div className="flex flex-col items-center gap-4 fade-in">
          <div className="h-9 w-9 rounded-full border-[3px] border-accent border-t-transparent animate-spin" />
          <span className="text-[13px] font-semibold text-ink2">Loading SANGAM…</span>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginScreen />;
  }

  return <MainDashboard />;
};
