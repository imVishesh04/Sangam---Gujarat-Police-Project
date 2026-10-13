import React, { useMemo, useState } from 'react';
import { Alert } from '../../types';
import { pdfExportService } from '../../services/pdfExportService';
import { apiService } from '../../services/apiService';
import {
  BellRing,
  CheckCircle2,
  Route as RouteIcon,
  Clock,
  MapPin,
  FileText,
  Search,
  Send,
  Cctv,
  Truck,
  FileDown,
  Loader2,
  Info,
  Navigation,
  X,
} from 'lucide-react';
import { BrandLogo } from '../ui/BrandLogos';
import { EmptyState, Plate, SeverityBadge, fmtTime, sevColor, timeAgo } from '../ui/kit';

interface LiveAlertsPanelProps {
  alerts: Alert[];
  onAcknowledgeAlert: (alertId: string | number, notes?: string) => void;
  onTrackPlate?: (plate: string) => void;
  onSelectCamera?: (cameraId: string) => void;
  onTriggerDemoAlert?: () => void;
  compact?: boolean;
  /** inside another panel (Control Room dock): no outer card */
  embedded?: boolean;
}

type DispatchState = { loading: boolean; data?: any; error?: string; sent?: string };

const SEV_FILTERS = ['all', 'critical', 'high', 'medium', 'low'] as const;

export const LiveAlertsPanel: React.FC<LiveAlertsPanelProps> = ({
  alerts,
  onAcknowledgeAlert,
  onTrackPlate,
  onSelectCamera,
  compact = false,
  embedded = false,
}) => {
  const [sev, setSev] = useState<(typeof SEV_FILTERS)[number]>('all');
  const [q, setQ] = useState('');
  const [tab, setTab] = useState<'open' | 'all'>('open');
  const [openId, setOpenId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [dispatch, setDispatch] = useState<Record<string, DispatchState>>({});
  const [assigning, setAssigning] = useState<string | null>(null);

  const deduped = useMemo(() => {
    const seen = new Set<string>();
    return alerts.filter((a) => {
      if (!a || a.alert_id === undefined || a.alert_id === null) return false;
      const k = String(a.alert_id);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }, [alerts]);

  const openCount = deduped.filter((a) => !a.is_acknowledged).length;

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase().replace(/\s+/g, '');
    return deduped.filter((a) => {
      if (tab === 'open' && a.is_acknowledged) return false;
      if (sev !== 'all' && a.severity !== sev) return false;
      if (s) {
        const hay = `${a.matched_value}|${a.location_name || ''}|${a.reason || ''}|${a.fir_number || ''}`.toLowerCase().replace(/\s+/g, '');
        if (!hay.includes(s)) return false;
      }
      return true;
    });
  }, [deduped, tab, sev, q]);

  const toggleDispatch = async (alertId: string | number) => {
    const key = String(alertId);
    if (openId === key) { setOpenId(null); return; }
    setOpenId(key);
    setNote('');
    setDispatch((p) => ({ ...p, [key]: { loading: true } }));
    const { ok, data } = await apiService.getDispatchSuggestion(alertId);
    setDispatch((p) => ({ ...p, [key]: ok ? { loading: false, data } : { loading: false, error: data?.error || 'Could not load units' } }));
  };

  const assign = async (alertId: string | number, unitId: string) => {
    const key = String(alertId);
    setAssigning(unitId);
    const { ok, data } = await apiService.assignUnit(alertId, unitId);
    setAssigning(null);
    if (!ok) {
      setDispatch((p) => ({ ...p, [key]: { ...p[key], loading: false, error: data?.error || 'Assignment failed' } }));
      return;
    }
    const sent = (data.results || []).filter((r: any) => r.ok).length;
    setDispatch((p) => ({ ...p, [key]: { ...p[key], loading: false, sent: `${data.unit?.name} assigned · WhatsApp to ${data.routed_to} (${sent}/${(data.results || []).length} sent)` } }));
    setTimeout(() => onAcknowledgeAlert(alertId, data.action_taken), 3000);
  };

  const acknowledge = (alertId: string | number) => {
    onAcknowledgeAlert(alertId, note.trim() || 'Acknowledged by control room');
    setOpenId(null);
    setNote('');
  };

  const narrow = embedded || compact;

  return (
    <div className={`h-full w-full flex flex-col overflow-hidden ${embedded ? '' : 'panel'}`}>
      {/* Header */}
      <div className={`flex flex-wrap items-center gap-2.5 border-b border-line ${narrow ? 'px-3 py-2.5' : 'px-4 py-3'}`}>
        {!narrow && (
          <div className="flex items-center gap-3 mr-2">
            <span className="icon-box icon-box-sm" style={{ color: 'var(--color-crit)', background: 'var(--color-critbg)', borderColor: '#F5C2BD' }}><BellRing /></span>
            <div>
              <div className="panel-title">Watchlist alerts</div>
              <div className="panel-sub">Plates that matched the watchlist, live</div>
            </div>
          </div>
        )}
        <div className="segmented" role="group" aria-label="Alert list">
          <button aria-pressed={tab === 'open'} onClick={() => setTab('open')}>Open <span className="num">{openCount}</span></button>
          <button aria-pressed={tab === 'all'} onClick={() => setTab('all')}>All <span className="num">{deduped.length}</span></button>
        </div>
        <div className={`relative ${narrow ? 'flex-1 min-w-[140px]' : 'w-[260px]'}`}>
          <Search className="w-4 h-4 text-ink3 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Plate, FIR or place" className="field !h-8 pl-9 !text-[12px]" aria-label="Search alerts" />
        </div>
        {!narrow && (
          <>
            <div className="flex items-center gap-1.5 flex-wrap">
              {SEV_FILTERS.map((s) => (
                <button key={s} className="chip" aria-pressed={sev === s} onClick={() => setSev(s)}>
                  {s !== 'all' && <span className="dot" style={{ background: sevColor(s) }} />}
                  {s === 'all' ? 'All priorities' : s[0].toUpperCase() + s.slice(1)}
                </button>
              ))}
            </div>
            <button onClick={() => pdfExportService.exportAlertsLog(shown)} className="btn btn-sm ml-auto" title="Download alerts as PDF"><FileDown />PDF</button>
          </>
        )}
        {narrow && (
          <select value={sev} onChange={(e) => setSev(e.target.value as any)} className="field !h-8 !w-auto !text-[12px]" aria-label="Priority">
            <option value="all">All</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        )}
      </div>

      {/* Feed */}
      <div className={`flex-1 min-h-0 scroll-y ${narrow ? 'p-2.5' : 'p-4'}`}>
        {shown.length === 0 ? (
          <EmptyState icon={CheckCircle2} title={tab === 'open' ? 'No open alerts' : 'No alerts'} hint="New watchlist matches appear here the moment a camera reads them." />
        ) : (
          <div className={`grid gap-2.5 items-start ${narrow ? 'grid-cols-1' : 'grid-cols-1 2xl:grid-cols-2'}`}>
            {shown.map((a) => {
              const open = !a.is_acknowledged;
              const key = String(a.alert_id);
              const drawer = openId === key && open;
              const info = dispatch[key];
              return (
                <article
                  key={key}
                  className={`relative rounded-xl border bg-panel2 overflow-hidden transition-colors ${open ? 'border-line2' : 'border-line opacity-90'}`}
                >
                  <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: open ? sevColor(a.severity) : 'var(--color-line2)' }} />
                  <div className="p-3.5 pl-4 space-y-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2 flex-wrap min-w-0">
                        <Plate value={a.matched_value} />
                        <span title={a.severity_why ? `Why: ${a.severity_why}` : undefined}><SeverityBadge severity={a.severity} /></span>
                        {a.confidence != null && a.confidence > 0 && <span className="badge badge-off num">OCR {a.confidence}%</span>}
                      </div>
                      <div className="text-right shrink-0">
                        <div className="time text-[13px]">{fmtTime(a.timestamp)}</div>
                        <div className="text-[11px] text-ink3 flex items-center gap-1 justify-end"><Clock className="w-3 h-3" />{timeAgo(a.timestamp)}</div>
                      </div>
                    </div>

                    <div>
                      <div className="text-[13px] font-semibold text-ink leading-snug">{a.reason}</div>
                      {a.severity_why && <div className="text-[12px] text-ink3 mt-0.5 flex items-start gap-1"><Info className="w-3.5 h-3.5 mt-[1px] shrink-0" />{a.severity_why}</div>}
                      {a.fir_number && <div className="text-[12px] font-semibold text-med mt-1 flex items-center gap-1.5"><FileText className="w-3.5 h-3.5" />FIR {a.fir_number}</div>}
                    </div>

                    <div className="flex items-center justify-between gap-2 rounded-lg bg-base border border-line px-3 py-2">
                      <div className="flex items-center gap-2 min-w-0 text-[12px]">
                        <MapPin className="w-3.5 h-3.5 text-accent2 shrink-0" />
                        <span className="font-semibold text-ink truncate">{a.location_name}</span>
                        <span className="text-ink3 shrink-0">{a.camera_id}</span>
                      </div>
                      {onSelectCamera && (
                        <button onClick={() => onSelectCamera(a.camera_id)} className="btn btn-ghost btn-sm shrink-0 !h-7" title="Show camera"><Cctv />Camera</button>
                      )}
                    </div>

                    <div className="flex items-center justify-between gap-2 flex-wrap pt-0.5">
                      {onTrackPlate && <button onClick={() => onTrackPlate(a.matched_value)} className="btn btn-sm"><RouteIcon />Route history</button>}
                      {open ? (
                        <button onClick={() => toggleDispatch(a.alert_id)} className={`btn btn-sm ${drawer ? '' : 'btn-primary'}`} aria-expanded={drawer}>
                          {drawer ? <X /> : <Truck />}{drawer ? 'Close' : 'Assign unit'}
                        </button>
                      ) : (
                        <span className="badge badge-ok"><CheckCircle2 />{a.acknowledged_by ? `Closed · ${a.acknowledged_by}` : 'Closed'}</span>
                      )}
                    </div>

                    {drawer && (
                      <div className="pop-in rounded-xl border border-line2 bg-base p-3 space-y-2.5">
                        <div className="eyebrow">Nearest responding units</div>
                        {!info || info.loading ? (
                          <div className="flex items-center gap-2 text-[12px] text-ink2"><Loader2 className="w-4 h-4 animate-spin" />Finding the nearest free unit…</div>
                        ) : info.sent ? (
                          <div className="text-[12px] font-semibold text-ok bg-okbg border border-[#A9DEC3] rounded-lg px-3 py-2 flex items-center gap-2">
                            <BrandLogo name="whatsapp" size={15} />{info.sent}
                          </div>
                        ) : info.error && !info.data ? (
                          <div className="text-[12px] font-semibold text-crit">{info.error}</div>
                        ) : (
                          <div className="space-y-2">
                            <div className="text-[12px] text-ink2">
                              <b className="text-ink capitalize">{info.data.severity}</b> · {info.data.severity_why}
                              {info.data.prediction && <> · heading <b className="text-ink">{info.data.prediction.heading}</b> ~<span className="num">{info.data.prediction.speed_kmh}</span> km/h</>}
                            </div>
                            {(info.data.units || []).length === 0 && <div className="text-[12px] text-crit">{info.data.error || 'No unit available'}</div>}
                            {(info.data.units || []).map((u: any, i: number) => (
                              <div key={u.unit_id} className={`flex items-center gap-3 rounded-lg border px-3 py-2 ${i === 0 ? 'border-accent bg-accentbg' : 'border-line bg-panel2'}`}>
                                <span className="icon-box icon-box-sm" style={i === 0 ? { color: '#fff', background: 'var(--color-accent)', borderColor: 'var(--color-accent)' } : {}}><Navigation /></span>
                                <div className="min-w-0 flex-1">
                                  <div className="text-[13px] font-semibold text-ink truncate">
                                    {i === 0 && <span className="text-[10px] font-bold tracking-wider text-accent2 mr-1.5">BEST</span>}{u.name}
                                  </div>
                                  <div className="text-[12px] text-ink3 truncate num">{u.distance_km} km · ~{u.eta_min} min · {u.why}</div>
                                </div>
                                <button disabled={assigning !== null} onClick={() => assign(a.alert_id, u.unit_id)} className="btn btn-sm btn-primary shrink-0">
                                  {assigning === u.unit_id ? <Loader2 className="animate-spin" /> : <BrandLogo name="whatsapp" size={14} color="#fff" />}
                                  {assigning === u.unit_id ? 'Sending' : 'Send'}
                                </button>
                              </div>
                            ))}
                            {info.data.station && <div className="text-[12px] text-ink2">Local station: <b className="text-ink">{info.data.station.name}</b> · <span className="num">{info.data.station.distance_km}</span> km</div>}
                            {info.data.note && <div className="text-[12px] text-med">{info.data.note}</div>}
                            {info.error && <div className="text-[12px] text-crit">{info.error}</div>}
                          </div>
                        )}
                        <div className="flex items-center gap-2 pt-1">
                          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Or close with a note" className="field !h-8 !text-[12px]" />
                          <button onClick={() => acknowledge(a.alert_id)} className="btn btn-sm btn-ok shrink-0"><Send />Close</button>
                        </div>
                      </div>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
