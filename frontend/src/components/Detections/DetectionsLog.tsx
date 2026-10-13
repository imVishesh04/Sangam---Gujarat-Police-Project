import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Detection } from '../../types';
import { apiService } from '../../services/apiService';
import { pdfExportService } from '../../services/pdfExportService';
import {
  ScanLine,
  Search,
  Download,
  FileDown,
  Route as RouteIcon,
  Cctv,
  Gauge,
  Hand,
  Clock,
  RefreshCw,
  Pause,
  Play,
  WifiOff,
  CarFront,
} from 'lucide-react';
import { EmptyState, Plate, Stat, fmtDate, fmtTime, timeAgo } from '../ui/kit';

interface DetectionsLogProps {
  onTrackPlate?: (plate: string) => void;
  onSelectCamera?: (cameraId: string) => void;
}

/** confidence in percent, or null for a manual sighting (no OCR) */
type Read = Omit<Detection, 'confidence'> & { confidence: number | null };

const POLL_MS = 5000;
const CONF_STEPS = [
  { v: 0, label: 'All' },
  { v: 50, label: '50%+' },
  { v: 70, label: '70%+' },
  { v: 90, label: '90%+' },
];

const toPercent = (c: unknown): number | null => {
  if (c === null || c === undefined || c === '') return null;
  const n = Number(c);
  if (!Number.isFinite(n)) return null;
  return n <= 1 ? Math.round(n * 1000) / 10 : Math.round(n * 10) / 10;
};

const confTone = (c: number) => (c >= 85 ? 'var(--color-ok)' : c >= 65 ? 'var(--color-med)' : 'var(--color-high)');

export const DetectionsLog: React.FC<DetectionsLogProps> = ({ onTrackPlate, onSelectCamera }) => {
  const [reads, setReads] = useState<Read[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [minConf, setMinConf] = useState(0);
  const [showManual, setShowManual] = useState(true);
  const [live, setLive] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [, tick] = useState(0);
  const knownIds = useRef<Set<string>>(new Set());
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const data = await apiService.getDetections({ per_page: 200 });
      const mapped: Read[] = data.map((d) => ({ ...d, confidence: toPercent(d.confidence) }));
      const first = knownIds.current.size === 0;
      const fresh = new Set<string>();
      mapped.forEach((d) => {
        const id = String(d.detection_id);
        if (!first && !knownIds.current.has(id)) fresh.add(id);
        knownIds.current.add(id);
      });
      setFreshIds(fresh);
      setReads(mapped);
      setError(null);
      setUpdatedAt(new Date());
    } catch (e: any) {
      setError(e?.message || 'Server not reachable');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!live) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [live, load]);

  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase().replace(/\s+/g, '');
    return reads.filter((d) => {
      if (d.confidence === null) {
        if (!showManual) return false;
      } else if (d.confidence < minConf) return false;
      if (s) {
        const hay = `${d.plate_number}|${d.location_name || ''}|${d.camera_id}`.toLowerCase().replace(/\s+/g, '');
        if (!hay.includes(s)) return false;
      }
      return true;
    });
  }, [reads, q, minConf, showManual]);

  const ocrReads = reads.filter((d) => d.confidence !== null);
  const avg = ocrReads.length ? (ocrReads.reduce((a, d) => a + (d.confidence as number), 0) / ocrReads.length).toFixed(1) : '—';
  const manualCount = reads.length - ocrReads.length;
  const lastRead = reads[0]?.timestamp;

  const exportCSV = () => {
    const head = 'detection_id,plate_number,ocr_confidence_percent,source,camera_id,location_name,timestamp\n';
    const rows = filtered
      .map((d) => `"${d.detection_id}","${d.plate_number}",${d.confidence ?? ''},"${d.confidence === null ? 'manual' : 'ocr'}","${d.camera_id}","${(d.location_name || '').replace(/"/g, "'")}","${d.timestamp}"`)
      .join('\n');
    const url = URL.createObjectURL(new Blob([head + rows], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `SANGAM_plate_reads_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="h-full flex flex-col gap-3 min-h-0">
      {/* Stats */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 shrink-0 stagger">
        <Stat icon={ScanLine} value={<span className="num">{reads.length}</span>} label="Latest reads loaded" />
        <Stat icon={Gauge} tone="ok" value={<span className="num">{avg}{avg !== '—' ? '%' : ''}</span>} label="Average OCR confidence" />
        <Stat icon={Hand} tone="med" value={<span className="num">{manualCount}</span>} label="Manual entries (no OCR)" />
        <Stat icon={Clock} tone="low" value={<span className="time">{lastRead ? fmtTime(lastRead) : '—'}</span>} label={lastRead ? `Last read · ${timeAgo(lastRead)}` : 'Last read'} />
      </div>

      {/* Table panel */}
      <div className="panel flex-1 min-h-0 flex flex-col overflow-hidden reveal reveal-2">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-line">
          <div className="relative flex-1 min-w-[220px] max-w-[380px]">
            <Search className="w-4 h-4 text-ink3 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search plate, place or camera ID" className="field pl-9" aria-label="Search plate reads" />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[12px] font-semibold text-ink3">OCR</span>
            <div className="segmented" role="group" aria-label="Minimum OCR confidence">
              {CONF_STEPS.map((s) => (
                <button key={s.v} aria-pressed={minConf === s.v} onClick={() => setMinConf(s.v)}>{s.label}</button>
              ))}
            </div>
          </div>
          <button className="chip" aria-pressed={showManual} onClick={() => setShowManual((v) => !v)}>
            <Hand /> Manual entries
          </button>
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-[12px] text-ink3 hidden lg:inline num">
              {error ? 'Not updated' : updatedAt ? `Updated ${timeAgo(updatedAt.toISOString())}` : ''}
            </span>
            <button onClick={() => setLive((v) => !v)} className={`btn btn-sm ${live ? 'btn-ok' : ''}`} title={live ? 'Auto refresh every 5 s (click to pause)' : 'Auto refresh paused'}>
              {live ? <Pause /> : <Play />}
              {live ? 'Live' : 'Paused'}
            </button>
            <button onClick={load} className="btn btn-sm btn-icon" title="Refresh now"><RefreshCw /></button>
            <button onClick={() => pdfExportService.exportDetectionsLog(filtered as unknown as Detection[])} className="btn btn-sm" title="Download as PDF"><FileDown />PDF</button>
            <button onClick={exportCSV} className="btn btn-sm" title="Download as CSV"><Download />CSV</button>
          </div>
        </div>

        {error && reads.length > 0 && (
          <div className="px-4 py-2 text-[12px] font-semibold text-crit bg-critbg border-b border-[#F5C2BD] flex items-center gap-2">
            <WifiOff className="w-4 h-4" /> {error}. Showing the last loaded reads.
          </div>
        )}

        {/* Body */}
        <div className="flex-1 min-h-0 scroll-y">
          {loading ? (
            <div className="p-4 space-y-2">
              {Array.from({ length: 8 }).map((_, i) => <div key={i} className="skeleton h-11" />)}
            </div>
          ) : error && reads.length === 0 ? (
            <EmptyState icon={WifiOff} title="Plate reads could not be loaded" hint={`${error}. Check that the backend is running, then refresh.`} action={<button className="btn btn-sm" onClick={load}><RefreshCw />Try again</button>} />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={CarFront}
              title={reads.length === 0 ? 'No plates read yet' : 'No reads match these filters'}
              hint={reads.length === 0 ? 'Start cameras from the Cameras page. New reads appear here automatically.' : 'Change the search or lower the OCR filter.'}
            />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Number plate</th>
                  <th>Camera and place</th>
                  <th>OCR confidence</th>
                  <th>Time (IST)</th>
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((d) => {
                  const fresh = freshIds.has(String(d.detection_id));
                  return (
                    <tr key={d.detection_id} className={fresh ? 'alert-flash' : ''}>
                      <td className="whitespace-nowrap"><Plate value={d.plate_number} /></td>
                      <td>
                        <div className="font-semibold text-ink truncate max-w-[280px]">{d.location_name || 'Unknown place'}</div>
                        <div className="text-[12px] text-ink3">{d.camera_id}</div>
                      </td>
                      <td className="whitespace-nowrap">
                        {d.confidence === null ? (
                          <span className="badge badge-med" title="Entered by an officer, not read by OCR"><Hand />Manual</span>
                        ) : (
                          <div className="flex items-center gap-2.5">
                            <div className="w-20 h-1.5 rounded-full bg-raised overflow-hidden">
                              <div className="h-full rounded-full" style={{ width: `${Math.min(100, d.confidence)}%`, background: confTone(d.confidence) }} />
                            </div>
                            <span className="num font-semibold text-ink">{d.confidence.toFixed(1)}%</span>
                          </div>
                        )}
                      </td>
                      <td className="whitespace-nowrap">
                        <div className="time">{fmtTime(d.timestamp)}</div>
                        <div className="text-[12px] text-ink3">{fmtDate(d.timestamp)}</div>
                      </td>
                      <td className="text-right whitespace-nowrap">
                        <div className="inline-flex items-center gap-1.5">
                          {onTrackPlate && <button onClick={() => onTrackPlate(d.plate_number)} className="btn btn-sm" title="Show route history"><RouteIcon />Route</button>}
                          {onSelectCamera && <button onClick={() => onSelectCamera(d.camera_id)} className="btn btn-sm btn-icon" title="Show camera on map"><Cctv /></button>}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <div className="px-4 py-2.5 border-t border-line text-[12px] text-ink3 flex items-center justify-between gap-3">
          <span className="num">Showing <b className="text-ink">{filtered.length}</b> of {reads.length}</span>
          <span className="hidden md:inline">YOLOv8 finds the plate, EasyOCR reads it, and only valid Indian plate formats are saved.</span>
        </div>
      </div>
    </div>
  );
};
