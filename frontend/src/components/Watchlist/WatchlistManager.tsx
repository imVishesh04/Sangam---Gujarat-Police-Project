import React, { useMemo, useState } from 'react';
import { WatchlistItem, UserRole } from '../../types';
import { GUJARAT_DEPARTMENTS } from '../../data/mockData';
import { ShieldAlert, Plus, Trash2, Search, Route as RouteIcon, Loader2, Info, CarFront, User, AlertOctagon, AlertTriangle, ShieldCheck } from 'lucide-react';
import { EmptyState, IconBox, Plate, SeverityBadge, Stat, sevColor } from '../ui/kit';

interface WatchlistManagerProps {
  watchlist: WatchlistItem[];
  role: UserRole;
  onAddWatchlist: (item: Partial<WatchlistItem>) => Promise<void>;
  onDeleteWatchlist: (id: string) => Promise<void>;
  onTrackPlate?: (plate: string) => void;
}

const EMPTY: Partial<WatchlistItem> = {
  type: 'vehicle',
  value: '',
  reason: '',
  severity: 'high',
  fir_number: '',
  department: GUJARAT_DEPARTMENTS[0],
  notes: '',
};

export const WatchlistManager: React.FC<WatchlistManagerProps> = ({ watchlist, role, onAddWatchlist, onDeleteWatchlist, onTrackPlate }) => {
  const [q, setQ] = useState('');
  const [type, setType] = useState<'all' | 'vehicle' | 'person'>('all');
  const [sev, setSev] = useState<string>('all');
  const [item, setItem] = useState<Partial<WatchlistItem>>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase().replace(/\s+/g, '');
    return watchlist.filter((w) => {
      if (type !== 'all' && w.type !== type) return false;
      if (sev !== 'all' && w.severity !== sev) return false;
      if (s && !`${w.value}|${w.reason}|${w.fir_number || ''}`.toLowerCase().replace(/\s+/g, '').includes(s)) return false;
      return true;
    });
  }, [watchlist, q, type, sev]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!item.value || !item.reason) return;
    setSaving(true);
    setErr('');
    try {
      await onAddWatchlist(item);
      setItem(EMPTY);
    } catch (ex: any) {
      setErr(ex?.message || 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  const count = (s: string) => watchlist.filter((w) => w.severity === s).length;

  return (
    <div className="h-full flex flex-col gap-3 min-h-0">
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 shrink-0 stagger">
        <Stat icon={ShieldAlert} value={<span className="num">{watchlist.length}</span>} label="Watchlist entries" />
        <Stat icon={AlertOctagon} tone="crit" value={<span className="num">{count('critical')}</span>} label="Critical" />
        <Stat icon={AlertTriangle} tone="high" value={<span className="num">{count('high')}</span>} label="High" />
        <Stat icon={ShieldCheck} tone="med" value={<span className="num">{count('medium') + count('low')}</span>} label="Medium and low" />
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_380px] gap-3">
        {/* Table */}
        <div className="panel flex flex-col min-h-0 overflow-hidden reveal reveal-1">
          <div className="flex flex-wrap items-center gap-2.5 px-4 py-3 border-b border-line">
            <div className="relative flex-1 min-w-[220px]">
              <Search className="w-4 h-4 text-ink3 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search plate, reason or FIR" className="field pl-9" aria-label="Search watchlist" />
            </div>
            <div className="segmented" role="group" aria-label="Type">
              <button aria-pressed={type === 'all'} onClick={() => setType('all')}>All</button>
              <button aria-pressed={type === 'vehicle'} onClick={() => setType('vehicle')}><CarFront />Vehicles</button>
              <button aria-pressed={type === 'person'} onClick={() => setType('person')}><User />Persons</button>
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              {['all', 'critical', 'high', 'medium', 'low'].map((s) => (
                <button key={s} className="chip" aria-pressed={sev === s} onClick={() => setSev(s)}>
                  {s !== 'all' && <span className="dot" style={{ background: sevColor(s) }} />}
                  {s === 'all' ? 'All' : s[0].toUpperCase() + s.slice(1)}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 min-h-0 scroll-y">
            {shown.length === 0 ? (
              <EmptyState icon={ShieldAlert} title="No watchlist entries found" hint="Clear the search, or add an entry with the form." />
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Plate / name</th>
                    <th>Priority</th>
                    <th>Reason</th>
                    <th className="hidden 2xl:table-cell">FIR / unit</th>
                    <th className="text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((w, i) => (
                    <tr key={w.watchlist_id ?? `wl-${i}`}>
                      <td className="whitespace-nowrap">
                        {w.type === 'vehicle' ? <Plate value={w.value} /> : <span className="inline-flex items-center gap-1.5 font-semibold text-ink"><User className="w-4 h-4 text-ink3" />{w.value}</span>}
                      </td>
                      <td className="whitespace-nowrap"><span title={(w as any).severity_why || undefined}><SeverityBadge severity={w.severity} /></span></td>
                      <td className="max-w-[320px]"><div className="font-semibold text-ink">{w.reason}</div>{w.fir_number && <div className="text-[12px] text-ink3 2xl:hidden">FIR {w.fir_number}</div>}</td>
                      <td className="hidden 2xl:table-cell">
                        <div className="font-semibold text-ink num">{w.fir_number || '—'}</div>
                        <div className="text-[12px] text-ink3 truncate max-w-[220px]">{w.department}</div>
                      </td>
                      <td className="text-right whitespace-nowrap">
                        <div className="inline-flex items-center gap-1.5">
                          {onTrackPlate && w.type === 'vehicle' && <button onClick={() => onTrackPlate(w.value)} className="btn btn-sm"><RouteIcon />Track</button>}
                          {role === 'admin' && (
                            <button
                              onClick={() => window.confirm(`Remove ${w.value} from the watchlist?`) && onDeleteWatchlist(String(w.watchlist_id ?? (w as any).id))}
                              className="btn btn-sm btn-icon btn-ghost"
                              title="Remove from watchlist"
                            >
                              <Trash2 />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="px-4 py-2.5 border-t border-line text-[12px] text-ink3 num">Showing <b className="text-ink">{shown.length}</b> of {watchlist.length}</div>
        </div>

        {/* Form */}
        <div className="flex flex-col gap-3 min-h-0 scroll-y reveal reveal-2">
          <form onSubmit={submit} className="panel p-4 space-y-3.5">
            <div className="flex items-center gap-3">
              <IconBox icon={Plus} size="sm" />
              <div>
                <div className="panel-title">Add to watchlist</div>
                <div className="panel-sub">Checked against every new plate read</div>
              </div>
            </div>
            <div>
              <label className="label">Type</label>
              <div className="segmented w-full" role="group" aria-label="Type">
                <button type="button" className="flex-1 justify-center" aria-pressed={item.type === 'vehicle'} onClick={() => setItem({ ...item, type: 'vehicle' })}><CarFront />Vehicle</button>
                <button type="button" className="flex-1 justify-center" aria-pressed={item.type === 'person'} onClick={() => setItem({ ...item, type: 'person' })}><User />Person (record)</button>
              </div>
            </div>
            <div>
              <label className="label">{item.type === 'vehicle' ? 'Vehicle number' : 'Name'} *</label>
              <input required placeholder={item.type === 'vehicle' ? 'e.g. GJ01AB1234' : 'Full name'} value={item.value} onChange={(e) => setItem({ ...item, value: item.type === 'vehicle' ? e.target.value.toUpperCase() : e.target.value })} className="field font-semibold tracking-[.04em]" />
            </div>
            <div>
              <label className="label">Reason *</label>
              <input required placeholder="e.g. Stolen vehicle" value={item.reason} onChange={(e) => setItem({ ...item, reason: e.target.value })} className="field" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">FIR / case no.</label>
                <input placeholder="Optional" value={item.fir_number} onChange={(e) => setItem({ ...item, fir_number: e.target.value })} className="field" />
              </div>
              <div>
                <label className="label">Requested by</label>
                <select value={item.department} onChange={(e) => setItem({ ...item, department: e.target.value })} className="field">
                  {GUJARAT_DEPARTMENTS.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
            </div>
            <div className="flex items-start gap-2 text-[12px] text-ink3"><Info className="w-3.5 h-3.5 mt-[1px] shrink-0" />Priority is set automatically from the reason: "kidnap", "weapon" → Critical; "stolen", "robbery" → High.</div>
            {err && <div className="text-[12px] font-semibold text-crit">{err}</div>}
            <button type="submit" disabled={saving} className="btn btn-primary w-full !h-10">
              {saving ? <Loader2 className="animate-spin" /> : <ShieldAlert />}{saving ? 'Saving…' : 'Add to watchlist'}
            </button>
          </form>
          <div className="panel p-4 flex gap-3">
            <Info className="w-4 h-4 text-accent2 shrink-0 mt-0.5" />
            <p className="text-[12px] text-ink2 leading-relaxed">
              Priority decides who is sent: <b className="text-ink">Critical</b> brings PCR, QRT and the station; <b className="text-ink">High</b> brings PCR and the station. A cloned plate or a vehicle seen at 3 cameras in 30 minutes moves the alert up automatically.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
