import React, { useEffect, useState } from 'react';
import { Camera } from '../../types';
import { apiService } from '../../services/apiService';
import { usePreview } from './LiveCameraFeed';
import { useAuth } from '../../context/AuthContext';
import { X, Loader2, VideoOff, RefreshCw, Play, Square, MapPin, Building2, Radio, ScanLine, Siren, Cctv } from 'lucide-react';
import { IconBox, fmtTime } from '../ui/kit';

interface CameraStreamModalProps {
  camera: Camera | null;
  onClose: () => void;
  onTriggerAlertForCamera?: (camera: Camera) => void;
}

type Worker = { running: boolean; status: string; last_seen: string | null };

export const CameraStreamModal: React.FC<CameraStreamModalProps> = ({ camera, onClose, onTriggerAlertForCamera }) => {
  const { role } = useAuth();
  const [retry, setRetry] = useState(0);
  const { videoRef, state, detail } = usePreview(camera, retry);
  const [worker, setWorker] = useState<Worker | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  // Poll the real ANPR worker state while the modal is open
  useEffect(() => {
    if (!camera) return;
    let alive = true;
    const poll = async () => {
      const s = await apiService.getIngestStatus(camera.camera_id);
      if (alive && s) setWorker(s);
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [camera?.camera_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!camera) return null;

  const running = worker?.running ?? !!camera.ingesting;
  const receiving = worker?.status === 'online';

  const toggleWorker = async () => {
    setBusy(true);
    setErr('');
    try {
      await apiService.toggleCameraStream(camera.camera_id, running ? 'stop' : 'start');
      const s = await apiService.getIngestStatus(camera.camera_id);
      if (s) setWorker(s);
    } catch (e: any) {
      setErr(e?.message || 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  const anprBadge = !running ? (
    <span className="badge badge-off"><span className="dot dot-off" />Stopped</span>
  ) : receiving ? (
    <span className="badge badge-ok"><span className="dot dot-live" />Reading plates</span>
  ) : (
    <span className="badge badge-med"><span className="dot dot-warn live-pulse" />Starting…</span>
  );

  const previewBadge =
    state === 'playing' ? <span className="badge badge-ok"><span className="dot dot-live" />Playing</span>
      : state === 'connecting' ? <span className="badge badge-med"><span className="dot dot-warn live-pulse" />Connecting</span>
      : <span className="badge badge-off"><span className="dot dot-off" />No preview</span>;

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-[rgba(15,23,42,.45)] backdrop-blur-sm fade-in" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label={`Camera ${camera.camera_id}`} className="pop-in panel w-full max-w-[1180px] max-h-[92vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="panel-head">
          <div className="flex items-center gap-3 min-w-0">
            <IconBox icon={Cctv} size="sm" />
            <div className="min-w-0">
              <div className="panel-title truncate">{camera.location_name}</div>
              <div className="panel-sub truncate">{camera.camera_id} · {camera.department || camera.city}</div>
            </div>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-icon" title="Close (Esc)"><X /></button>
        </div>

        <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[1fr_320px] overflow-hidden">
          {/* Video */}
          <div className="relative bg-black min-h-[300px] lg:min-h-[480px]">
            <video ref={videoRef} className="absolute inset-0 w-full h-full object-contain" autoPlay muted playsInline />
            {state !== 'playing' && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[rgba(7,11,18,.9)] text-center px-6">
                {state === 'connecting' ? (
                  <>
                    <Loader2 className="w-8 h-8 text-[#8AB6FF] animate-spin" />
                    <div className="text-[14px] font-semibold text-white">Connecting to camera…</div>
                  </>
                ) : (
                  <>
                    <IconBox icon={VideoOff} tone="crit" size="lg" />
                    <div className="text-[15px] font-semibold text-white">No live preview</div>
                    <div className="text-[13px] text-[#DCE3EC] max-w-md">{detail || 'This camera is offline or unreachable.'}</div>
                    <button onClick={() => setRetry((n) => n + 1)} className="btn btn-primary mt-1"><RefreshCw />Retry preview</button>
                  </>
                )}
              </div>
            )}
            {state === 'playing' && (
              <div className="absolute top-3 left-3 flex items-center gap-2 pointer-events-none">
                <span className="badge" style={{ background: 'rgba(7,11,18,.75)', color: '#FFD3D0', borderColor: 'rgba(255,255,255,.14)' }}>
                  <span className="dot dot-crit live-pulse" />Live
                </span>
                <span className="badge" style={{ background: 'rgba(7,11,18,.75)', color: '#E6EDF7', borderColor: 'rgba(255,255,255,.14)' }}>{camera.format}</span>
              </div>
            )}
          </div>

          {/* Side */}
          <div className="border-t lg:border-t-0 lg:border-l border-line scroll-y p-4 flex flex-col gap-4">
            <section className="rounded-xl border border-line bg-panel2 p-4 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-[13px] font-semibold text-ink"><Radio className="w-4 h-4 text-accent2" />Browser preview</span>
                {previewBadge}
              </div>
              <div className="divider" />
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-[13px] font-semibold text-ink"><ScanLine className="w-4 h-4 text-accent2" />Plate reading</span>
                {anprBadge}
              </div>
              {running && worker?.last_seen && (
                <div className="text-[12px] text-ink3">Last frame <span className="time">{fmtTime(worker.last_seen)}</span></div>
              )}
              {role === 'admin' ? (
                <button onClick={toggleWorker} disabled={busy} className={`btn w-full ${running ? 'btn-danger' : 'btn-ok'}`}>
                  {busy ? <Loader2 className="animate-spin" /> : running ? <Square /> : <Play />}
                  {running ? 'Stop plate reading' : 'Start plate reading'}
                </button>
              ) : (
                <div className="text-[12px] text-ink3">Only an admin can start or stop plate reading.</div>
              )}
              {err && <div className="text-[12px] font-semibold text-crit">{err}</div>}
            </section>

            <section className="space-y-2.5">
              <div className="eyebrow">Camera details</div>
              <dl className="grid grid-cols-[96px_1fr] gap-y-2 gap-x-3 text-[13px]">
                <dt className="text-ink3">Camera ID</dt><dd className="font-semibold text-ink">{camera.camera_id}</dd>
                <dt className="text-ink3">Stream</dt><dd className="font-semibold text-ink">{camera.format}</dd>
                <dt className="text-ink3">Source</dt><dd className="font-semibold text-ink truncate">{camera.vendor}</dd>
                <dt className="text-ink3">Location</dt>
                <dd className="font-semibold text-ink num">{Number.isFinite(camera.latitude) ? `${camera.latitude.toFixed(5)}, ${camera.longitude.toFixed(5)}` : '—'}</dd>
              </dl>
            </section>

            <section className="space-y-2">
              <div className="eyebrow">Jurisdiction</div>
              <div className="flex items-start gap-2.5 text-[13px]"><Building2 className="w-4 h-4 mt-0.5 text-ink3 shrink-0" /><span className="text-ink font-semibold">{camera.department || '—'}</span></div>
              <div className="flex items-start gap-2.5 text-[13px]"><MapPin className="w-4 h-4 mt-0.5 text-ink3 shrink-0" /><span className="text-ink2">{camera.city}, Gujarat</span></div>
            </section>

            {onTriggerAlertForCamera && (
              <button onClick={() => { onTriggerAlertForCamera(camera); onClose(); }} className="btn btn-danger w-full mt-auto" title="Uses demo data">
                <Siren />Send test alert from this camera
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
