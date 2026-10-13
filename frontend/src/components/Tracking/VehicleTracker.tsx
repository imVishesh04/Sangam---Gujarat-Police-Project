import React, { useEffect, useState } from 'react';
import { TrackPoint } from '../../types';
import { apiService } from '../../services/apiService';
import { pdfExportService } from '../../services/pdfExportService';
import {
  Search,
  FileDown,
  Route as RouteIcon,
  Map as MapIcon,
  Wand2,
  MapPin,
  Gauge,
  Flag,
  FileJson,
  Cctv,
  Hand,
  Loader2,
  SearchX,
  CheckCircle2,
  AlertTriangle,
  ShieldAlert,
} from 'lucide-react';
import { EmptyState, IconBox, Plate, Stat, fmtDate, fmtTime, prettyPlate } from '../ui/kit';

interface VehicleTrackerProps {
  initialPlate?: string;
  onSelectCamera?: (cameraId: string) => void;
  onShowInMap?: (plate: string, points: TrackPoint[], note?: string) => void;
}

const fmtConf = (c: number | null | undefined) => (c == null ? 'manual entry' : `${c}%`);

export const VehicleTracker: React.FC<VehicleTrackerProps> = ({ initialPlate = '', onSelectCamera, onShowInMap }) => {
  const [input, setInput] = useState(initialPlate || 'GJ01AB1234');
  const [plate, setPlate] = useState(initialPlate || 'GJ01AB1234');
  const [points, setPoints] = useState<TrackPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoInfo, setDemoInfo] = useState<{ ok: boolean; text: string } | null>(null);
  const [presets, setPresets] = useState<{ plate: string; label: string }[]>([]);

  useEffect(() => {
    apiService
      .getWatchlist()
      .then((items) => setPresets(items.filter((w) => w.type === 'vehicle' && w.value).slice(0, 6).map((w) => ({ plate: w.value, label: w.reason || 'Watchlist vehicle' }))))
      .catch(() => {});
  }, []);

  const search = async (target?: string) => {
    const p = (target || input).trim().toUpperCase().replace(/[\s-]/g, '');
    if (!p) return;
    setLoading(true);
    setPlate(p);
    setInput(p);
    setDemoInfo(null);
    try {
      setPoints(await apiService.getVehicleTrack(p));
    } catch {
      setPoints([]);
    } finally {
      setLoading(false);
    }
  };

  const demoRoute = async () => {
    const p = input.trim().toUpperCase().replace(/[\s-]/g, '');
    if (!p) return;
    setDemoBusy(true);
    setDemoInfo(null);
    const { ok, data } = await apiService.generateDemoRoute(p);
    if (!ok) {
      setDemoInfo({ ok: false, text: data?.error || 'Could not create the demo route' });
      setDemoBusy(false);
      return;
    }
    const n = data.stops?.length ?? 0;
    const onlineNow = data.online_cameras_available ?? 0;
    const cams = !data.used_offline_cameras
      ? `all ${n} are online cameras`
      : onlineNow === 0
      ? 'no camera is online right now, so registered cameras were used'
      : `only ${onlineNow} cameras are online, so registered cameras were used`;
    const note = `Demo trip: ${n} manual test sightings (not ANPR reads) — ${cams}.${data.alert_id ? ` Alert #${data.alert_id} raised at the last checkpoint.` : ''}`;
    setDemoInfo({ ok: true, text: note });
    setPlate(p);
    const res = await apiService.getVehicleTrack(p);
    setPoints(res);
    setDemoBusy(false);
    if (onShowInMap && res.length > 0) onShowInMap(p, res, note);
  };

  useEffect(() => { search(initialPlate || 'GJ01AB1234'); /* eslint-disable-next-line */ }, [initialPlate]);

  const exportJSON = () => {
    const dossier = {
      report: 'SANGAM vehicle route history',
      classification: 'Prototype export — verify before official use',
      generated_at: new Date().toISOString(),
      plate,
      checkpoints: points.map((p, i) => ({ sequence: i + 1, camera: p.camera_id, location: p.location_name, city: p.city, timestamp: p.timestamp, ocr: fmtConf(p.confidence), gps: { lat: p.latitude, lng: p.longitude } })),
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(dossier, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `SANGAM_route_${plate}_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const scored = points.filter((p) => p.confidence != null);
  const avg = scored.length ? `${(scored.reduce((a, p) => a + (p.confidence as number), 0) / scored.length).toFixed(1)}%` : points.length ? 'Manual' : '—';
  const first = points[0];
  const last = points[points.length - 1];
  const cameraCount = new Set(points.map((p) => p.camera_id)).size;

  return (
    <div className="h-full grid grid-cols-1 xl:grid-cols-[360px_minmax(0,1fr)] gap-3 min-h-0">
      {/* Left: search */}
      <div className="flex flex-col gap-3 min-h-0 scroll-y">
        <div className="panel p-4 space-y-3 reveal">
          <div className="flex items-center gap-3">
            <IconBox icon={RouteIcon} size="sm" />
            <div>
              <div className="panel-title">Find a vehicle</div>
              <div className="panel-sub">Every camera that read this plate, in order</div>
            </div>
          </div>
          <form onSubmit={(e) => { e.preventDefault(); search(); }} className="space-y-2.5">
            <div className="relative">
              <Search className="w-4 h-4 text-ink3 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                value={input}
                onChange={(e) => setInput(e.target.value.toUpperCase())}
                placeholder="e.g. GJ01AB1234"
                aria-label="Vehicle number"
                className="field !h-11 pl-9 !text-[15px] font-semibold tracking-[.06em] num uppercase"
              />
            </div>
            <button type="submit" disabled={loading} className="btn btn-primary w-full !h-10">
              {loading ? <Loader2 className="animate-spin" /> : <RouteIcon />}{loading ? 'Searching…' : 'Show route'}
            </button>
            <button type="button" onClick={demoRoute} disabled={demoBusy} className="btn w-full" title="Creates 5–7 manual test sightings at registered cameras and shows the trip on the map">
              {demoBusy ? <Loader2 className="animate-spin" /> : <Wand2 />}{demoBusy ? 'Creating route…' : 'Demo route (5–7 cameras)'}
            </button>
            {demoInfo && (
              <div className={`text-[12px] leading-snug rounded-lg px-3 py-2 border flex gap-2 ${demoInfo.ok ? 'bg-okbg border-[#A9DEC3] text-ok' : 'bg-critbg border-[#F5C2BD] text-crit'}`}>
                {demoInfo.ok ? <CheckCircle2 className="w-4 h-4 shrink-0 mt-[1px]" /> : <AlertTriangle className="w-4 h-4 shrink-0 mt-[1px]" />}
                <span>{demoInfo.text}</span>
              </div>
            )}
          </form>
        </div>

        {presets.length > 0 && (
          <div className="panel p-2 reveal reveal-1">
            <div className="eyebrow px-2.5 pt-2 pb-2">From the watchlist</div>
            <div className="space-y-1">
              {presets.map((s) => {
                const on = plate.replace(/[\s-]/g, '') === s.plate.replace(/[\s-]/g, '');
                return (
                  <button
                    key={s.plate}
                    onClick={() => search(s.plate)}
                    className={`w-full text-left flex items-center gap-3 px-2.5 py-2 rounded-lg border transition-colors ${on ? 'bg-accentbg border-accent' : 'border-transparent hover:bg-raised'}`}
                  >
                    <ShieldAlert className="w-4 h-4 text-ink3 shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span className="block"><Plate value={s.plate} /></span>
                      <span className="block text-[12px] text-ink3 truncate mt-1">{s.label}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Right: result */}
      <div className="flex flex-col gap-3 min-h-0">
        {points.length > 0 && (
          <div className="grid grid-cols-2 2xl:grid-cols-4 gap-3 shrink-0 stagger">
            <Stat icon={Cctv} value={<span className="num">{cameraCount}</span>} label={`Cameras · ${points.length} sightings`} />
            <Stat icon={Gauge} tone="ok" value={<span className="num">{avg}</span>} label="Average OCR confidence" />
            <Stat icon={Flag} tone="low" value={<span className="time">{fmtTime(first?.timestamp)}</span>} label={`First · ${first?.location_name || '—'}`} />
            <Stat icon={MapPin} tone="crit" value={<span className="time">{fmtTime(last?.timestamp)}</span>} label={`Last · ${last?.location_name || '—'}`} />
          </div>
        )}

        <div className="panel flex-1 min-h-0 flex flex-col overflow-hidden reveal reveal-2">
          <div className="panel-head">
            <div className="flex items-center gap-3 min-w-0">
              <Plate value={plate} size="lg" />
              <div className="min-w-0 hidden 2xl:block">
                <div className="panel-title">Sightings in time order</div>
                <div className="panel-sub">{points.length ? `${points.length} sightings` : 'No sightings yet'}</div>
              </div>
            </div>
            {points.length > 0 && (
              <div className="flex items-center gap-2 justify-end shrink-0">
                {onShowInMap && <button onClick={() => onShowInMap(plate, points)} className="btn btn-sm btn-primary"><MapIcon />Show on map</button>}
                <button onClick={() => pdfExportService.exportTrajectoryDossier(plate, points)} className="btn btn-sm"><FileDown />PDF</button>
                <button onClick={exportJSON} className="btn btn-sm btn-icon" title="Download as JSON"><FileJson /></button>
              </div>
            )}
          </div>

          <div className="flex-1 min-h-0 scroll-y p-4">
            {loading ? (
              <div className="space-y-3">{Array.from({ length: 5 }).map((_, i) => <div key={i} className="skeleton h-20" />)}</div>
            ) : points.length === 0 ? (
              <EmptyState icon={SearchX} title={`No sightings for ${prettyPlate(plate)}`} hint="Enter another vehicle number, or create a demo route to see how tracking works." />
            ) : (
              <ol className="relative">
                {points.map((p, i) => {
                  const isFirst = i === 0;
                  const isLast = i === points.length - 1;
                  const color = isLast ? 'var(--color-crit)' : isFirst ? 'var(--color-ok)' : 'var(--color-accent)';
                  return (
                    <li key={p.detection_id} className="relative pl-12 pb-3 last:pb-0">
                      {!isLast && <span className="absolute left-[15px] top-8 bottom-0 w-[2px] bg-line2" />}
                      <span className="absolute left-0 top-2 w-8 h-8 rounded-full flex items-center justify-center text-[12px] font-bold text-white border-[3px] border-panel num" style={{ background: color }}>
                        {i + 1}
                      </span>
                      <div className={`rounded-xl border p-3.5 transition-colors border-line bg-panel2 hover:border-line2`}>
                        <div className="flex items-start justify-between gap-3 flex-wrap">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-[14px] font-semibold text-ink">{p.location_name}</span>
                              {isFirst && <span className="badge badge-ok">First seen</span>}
                              {isLast && points.length > 1 && <span className="badge badge-crit">Last seen</span>}
                            </div>
                            <div className="text-[12px] text-ink3 mt-0.5">{p.city ? `${p.city} · ` : ''}{p.camera_id}</div>
                          </div>
                          <div className="text-right">
                            <div className="time text-[14px]">{fmtTime(p.timestamp)}</div>
                            <div className="text-[12px] text-ink3">{fmtDate(p.timestamp)}</div>
                          </div>
                        </div>
                        <div className="mt-3 flex items-center justify-between gap-2 flex-wrap">
                          {p.confidence == null ? (
                            <span className="badge badge-med"><Hand />Manual entry</span>
                          ) : (
                            <span className="badge badge-off num">OCR {p.confidence}%</span>
                          )}
                          {onSelectCamera && (
                            <button onClick={() => onSelectCamera(p.camera_id)} className="btn btn-sm"><Cctv />Show camera</button>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
