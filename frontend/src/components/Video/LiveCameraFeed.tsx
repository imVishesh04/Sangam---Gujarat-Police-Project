import React, { useEffect, useRef, useState } from 'react';
import Hls from 'hls.js';
import { Camera } from '../../types';
import { API_CONFIG } from '../../config/api';
import { Maximize2, Cctv, Loader2, VideoOff, RefreshCw } from 'lucide-react';
import { EmptyState } from '../ui/kit';

interface LiveCameraFeedProps {
  camera: Camera | null;
  onToggleStatus?: (camera: Camera, action: 'start' | 'stop') => void;
  onOpenModal?: (camera: Camera) => void;
  compact?: boolean;
}

type Preview = 'connecting' | 'playing' | 'error';

/**
 * Browser preview of a camera through the backend HLS proxy.
 * The preview is separate from the ANPR worker: a camera can play here while
 * plate reading is stopped, and the other way round.
 */
export const usePreview = (camera: Camera | null, retryKey = 0) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [state, setState] = useState<Preview>('connecting');
  const [detail, setDetail] = useState('');
  const id = camera?.camera_id;
  const url = camera?.stream_url;

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !id || !url) return;
    setState('connecting');
    setDetail('');
    let hls: Hls | null = null;
    let alive = true;
    const onPlaying = () => alive && setState('playing');
    const onError = () => { if (alive) { setState('error'); setDetail('Video could not be played'); } };
    video.addEventListener('playing', onPlaying);
    video.addEventListener('error', onError);

    if (url.toLowerCase().startsWith('rtsp')) {
      setState('error');
      setDetail('RTSP cannot play in a browser. Plate reading still works on the server.');
    } else if (url.toLowerCase().includes('.m3u8') && Hls.isSupported()) {
      hls = new Hls({
        enableWorker: true,
        lowLatencyMode: false,
        liveSyncDurationCount: 3,
        manifestLoadingMaxRetry: 4,
        levelLoadingMaxRetry: 4,
        fragLoadingMaxRetry: 6,
        xhrSetup: (xhr) => { xhr.withCredentials = true; },
      });
      hls.loadSource(`${API_CONFIG.BASE_URL}/cameras/${encodeURIComponent(id)}/stream-proxy`);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => { video.play().catch(() => {}); });
      let recovered = false;
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (!data.fatal || !alive) return;
        // one automatic recovery attempt for media / network hiccups
        if (!recovered && data.type === Hls.ErrorTypes.MEDIA_ERROR) { recovered = true; hls?.recoverMediaError(); return; }
        if (!recovered && data.type === Hls.ErrorTypes.NETWORK_ERROR) { recovered = true; hls?.startLoad(); return; }
        const code = (data.response as any)?.code;
        setState('error');
        setDetail(
          code === 504 ? 'Camera did not answer in time (504)'
            : code === 502 ? 'Camera server is offline or unreachable (502)'
            : code === 401 ? 'Session expired. Please sign in again.'
            : 'Stream could not be loaded'
        );
      });
    } else {
      video.src = url;
      video.play().catch(() => alive && setState('error'));
    }

    return () => {
      alive = false;
      video.removeEventListener('playing', onPlaying);
      video.removeEventListener('error', onError);
      hls?.destroy();
      video.removeAttribute('src');
      video.load();
    };
    // Keyed on id + url only: status refreshes must NOT restart the player.
  }, [id, url, retryKey]);

  return { videoRef, state, detail };
};

export const LiveCameraFeed: React.FC<LiveCameraFeedProps> = ({ camera, onOpenModal, compact = false }) => {
  const [retry, setRetry] = useState(0);
  const { videoRef, state, detail } = usePreview(camera, retry);

  if (!camera) {
    return (
      <div className="h-full w-full panel flex items-center justify-center">
        <EmptyState icon={Cctv} title="No camera selected" hint="Click a camera on the map or in the list to watch it here." />
      </div>
    );
  }

  return (
    <div className="h-full w-full panel overflow-hidden flex flex-col group">
      {!compact && (
        <div className="flex items-center justify-between gap-3 px-3.5 h-11 border-b border-line shrink-0">
          <div className="min-w-0 flex items-center gap-2.5">
            <span className={`dot ${state === 'playing' ? 'dot-live' : state === 'connecting' ? 'dot-warn' : 'dot-off'}`} />
            <span className="text-[13px] font-semibold text-ink truncate" title={camera.location_name}>{camera.location_name}</span>
            <span className="text-[12px] text-ink3 shrink-0">{camera.camera_id}</span>
          </div>
          {onOpenModal && (
            <button onClick={() => onOpenModal(camera)} className="btn btn-ghost btn-sm btn-icon" title="Open large view"><Maximize2 /></button>
          )}
        </div>
      )}

      <div className="relative flex-1 min-h-0 bg-black">
        <video ref={videoRef} className="absolute inset-0 w-full h-full object-cover" autoPlay muted playsInline />

        {state !== 'playing' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-[rgba(7,11,18,.88)] text-center px-4">
            {state === 'connecting' ? (
              <>
                <Loader2 className="w-6 h-6 text-[#8AB6FF] animate-spin" />
                <span className="text-[13px] font-semibold text-white">Connecting…</span>
              </>
            ) : (
              <>
                <VideoOff className="w-6 h-6 text-[#DCE3EC]" />
                <span className="text-[13px] font-semibold text-white">No live preview</span>
                <span className="text-[12px] text-[#C9D3E0] max-w-[260px]">{detail || 'The stream could not be played'}</span>
                <button onClick={() => setRetry((n) => n + 1)} className="btn btn-sm mt-1"><RefreshCw />Retry</button>
              </>
            )}
          </div>
        )}

        {/* overlays */}
        <div className="absolute inset-x-0 top-0 p-2.5 flex items-start justify-between pointer-events-none">
          {state === 'playing' && (
            <span className="badge" style={{ background: 'rgba(7,11,18,.75)', color: '#FFD3D0', borderColor: 'rgba(255,255,255,.14)' }}>
              <span className="dot dot-crit live-pulse" /> Live
            </span>
          )}
          {compact && (
            <span className="ml-auto max-w-[70%] truncate text-[12px] font-semibold text-white px-2 py-1 rounded-md" style={{ background: 'rgba(7,11,18,.75)' }}>
              {camera.location_name}
            </span>
          )}
        </div>
        {compact && onOpenModal && (
          <button
            onClick={(e) => { e.stopPropagation(); onOpenModal(camera); }}
            className="absolute bottom-2.5 right-2.5 btn btn-sm btn-icon opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
            title="Open large view"
          >
            <Maximize2 />
          </button>
        )}
        {compact && (
          <span className="absolute bottom-2.5 left-2.5 text-[11px] font-semibold text-[#DCE3EC] px-2 py-0.5 rounded-md num" style={{ background: 'rgba(7,11,18,.75)' }}>
            {camera.camera_id}
          </span>
        )}
      </div>
    </div>
  );
};
