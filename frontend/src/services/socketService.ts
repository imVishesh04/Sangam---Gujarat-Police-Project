import { io, Socket } from 'socket.io-client';
import { API_CONFIG, STORAGE_KEYS } from '../config/api';
import { Alert, CameraStatus, Detection } from '../types';
import { INITIAL_CAMERAS, INITIAL_WATCHLIST } from '../data/mockData';

type AlertListener = (alert: Alert) => void;
type CameraStatusListener = (payload: { camera_id: string; status: CameraStatus }) => void;
type DetectionListener = (detection: Detection) => void;

class SocketService {
  private alertsSocket: Socket | null = null;
  private camerasSocket: Socket | null = null;

  private alertListeners: Set<AlertListener> = new Set();
  private cameraStatusListeners: Set<CameraStatusListener> = new Set();
  private detectionListeners: Set<DetectionListener> = new Set();

  public isConnectedToSocket = false;
  private simulationInterval: any = null;
  public simulationActive = true;

  constructor() {
    this.initSockets();
    this.startSimulationEngine();
  }

  private initSockets() {
    try {
      const socketUrl = API_CONFIG.SOCKET_URL;
      
      // Namespace /alerts
      this.alertsSocket = io(`${socketUrl}/alerts`, {
        transports: ['websocket', 'polling'],
        reconnectionAttempts: 3,
        timeout: 3000,
        autoConnect: true,
      });

      this.alertsSocket.on('connect', () => {
        console.log('Connected to real-time /alerts namespace');
        this.isConnectedToSocket = true;
      });

      this.alertsSocket.on('new_alert', (data: Alert) => {
        this.notifyAlert(data);
      });

      this.alertsSocket.on('disconnect', () => {
        this.isConnectedToSocket = false;
      });

      // Namespace /cameras
      this.camerasSocket = io(`${socketUrl}/cameras`, {
        transports: ['websocket', 'polling'],
        reconnectionAttempts: 3,
        timeout: 3000,
        autoConnect: true,
      });

      this.camerasSocket.on('connect', () => {
        console.log('Connected to real-time /cameras namespace');
        this.isConnectedToSocket = true;
      });

      this.camerasSocket.on('status_change', (payload: { camera_id: string; status: CameraStatus }) => {
        this.notifyCameraStatus(payload);
      });
    } catch (err) {
      console.warn('Socket initialization error (running in local simulation mode):', err);
    }
  }

  // Event subscription methods
  public onNewAlert(listener: AlertListener): () => void {
    this.alertListeners.add(listener);
    return () => this.alertListeners.delete(listener);
  }

  public onCameraStatusChange(listener: CameraStatusListener): () => void {
    this.cameraStatusListeners.add(listener);
    return () => this.cameraStatusListeners.delete(listener);
  }

  public onNewDetection(listener: DetectionListener): () => void {
    this.detectionListeners.add(listener);
    return () => this.detectionListeners.delete(listener);
  }

  private notifyAlert(alert: Alert) {
    this.playAlertSound();
    this.alertListeners.forEach((fn) => {
      try {
        fn(alert);
      } catch (e) {
        console.error(e);
      }
    });
  }

  private notifyCameraStatus(payload: { camera_id: string; status: CameraStatus }) {
    this.cameraStatusListeners.forEach((fn) => {
      try {
        fn(payload);
      } catch (e) {
        console.error(e);
      }
    });
  }

  private notifyDetection(detection: Detection) {
    this.detectionListeners.forEach((fn) => {
      try {
        fn(detection);
      } catch (e) {
        console.error(e);
      }
    });
  }

  // Audio chirp feedback for real-time alerts
  private playAlertSound() {
    try {
      const isMuted = localStorage.getItem(STORAGE_KEYS.AUDIO_MUTED) === 'true';
      if (isMuted) return;

      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, ctx.currentTime); // A5
      osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.25);

      gain.gain.setValueAtTime(0.15, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start();
      osc.stop(ctx.currentTime + 0.3);
    } catch (e) {
      // Audio context might be restricted before user interaction
    }
  }

  // Manual Trigger for Live Presentation Demo
  public triggerManualDemoAlert(plate?: string, customReason?: string) {
    const rawCams = localStorage.getItem(STORAGE_KEYS.CAMERAS);
    const cameras = rawCams ? JSON.parse(rawCams) : INITIAL_CAMERAS;
    const randomCam = cameras[Math.floor(Math.random() * cameras.length)] || INITIAL_CAMERAS[0];

    const rawWL = localStorage.getItem(STORAGE_KEYS.WATCHLIST);
    const watchlist = rawWL ? JSON.parse(rawWL) : INITIAL_WATCHLIST;
    const sampleWl = watchlist[Math.floor(Math.random() * watchlist.length)] || INITIAL_WATCHLIST[0];

    const matchedPlate = plate || sampleWl.value;
    const uniqueSuffix = `${Date.now()}-${Math.random().toString(36).substring(2, 8).toUpperCase()}`;
    const newAlert: Alert = {
      alert_id: `ALT-LIVE-${uniqueSuffix}`,
      camera_id: randomCam.camera_id,
      location_name: randomCam.location_name,
      city: randomCam.city,
      watchlist_id: sampleWl.watchlist_id,
      matched_value: matchedPlate,
      timestamp: new Date().toISOString(),
      reason: customReason || sampleWl.reason,
      severity: sampleWl.severity || 'critical',
      fir_number: sampleWl.fir_number || 'FIR-2026/URGENT',
      confidence: +(97.0 + Math.random() * 2.8).toFixed(1),
      is_acknowledged: false,
      dispatch_status: 'pending',
    };

    // Save to storage with deduplication
    const rawAlerts = localStorage.getItem(STORAGE_KEYS.ALERTS);
    const alerts: Alert[] = rawAlerts ? JSON.parse(rawAlerts) : [];
    const dedupedAlerts = [newAlert, ...alerts.filter((a) => a.alert_id !== newAlert.alert_id)];
    localStorage.setItem(STORAGE_KEYS.ALERTS, JSON.stringify(dedupedAlerts));

    this.notifyAlert(newAlert);
  }

  // Background stream simulation for demo when backend is not streaming
  private startSimulationEngine() {
    if (!API_CONFIG.ENABLE_SIMULATION_FALLBACK) return;

    let tick = 0;
    this.simulationInterval = setInterval(() => {
      if (!this.simulationActive) return;
      tick++;

      // Every ~18 seconds: simulate a live incoming vehicle detection
      if (tick % 3 === 0) {
        const plates = ['GJ-01-WQ-8910', 'GJ-27-MN-3312', 'GJ-05-AB-7711', 'GJ-18-BB-2026', 'GJ-06-XZ-5501'];
        const randomPlate = plates[Math.floor(Math.random() * plates.length)];
        const cam = INITIAL_CAMERAS[Math.floor(Math.random() * INITIAL_CAMERAS.length)];
        const uniqueDetId = `DET-LIV-${Date.now()}-${Math.random().toString(36).substring(2, 8).toUpperCase()}`;
        
        const detection: Detection = {
          detection_id: uniqueDetId,
          camera_id: cam.camera_id,
          location_name: cam.location_name,
          plate_number: randomPlate,
          confidence: +(95 + Math.random() * 4.9).toFixed(1),
          timestamp: new Date().toISOString(),
          matched_watchlist: false,
        };
        this.notifyDetection(detection);
      }

      // Every ~45 seconds: auto-simulate a watchlist match alert
      if (tick % 8 === 0) {
        this.triggerManualDemoAlert();
      }
    }, 6000);
  }

  public destroy() {
    if (this.simulationInterval) clearInterval(this.simulationInterval);
    if (this.alertsSocket) this.alertsSocket.disconnect();
    if (this.camerasSocket) this.camerasSocket.disconnect();
  }
}

export const socketService = new SocketService();
