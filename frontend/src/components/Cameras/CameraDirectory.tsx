import React, { useEffect, useMemo, useState } from 'react';
import { Camera, UserRole } from '../../types';
import { GUJARAT_DEPARTMENTS } from '../../data/mockData';
import { LiveCameraFeed } from '../Video/LiveCameraFeed';
import { pdfExportService } from '../../services/pdfExportService';
import {
  Cctv,
  Search,
  Plus,
  Upload,
  Play,
  Square,
  Trash2,
  Maximize2,
  FileDown,
  List,
  LayoutGrid,
  ScanLine,
  Wifi,
  WifiOff,
  X,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  MapPin,
  Building2,
} from 'lucide-react';
import { EmptyState, IconBox, Stat, StatusBadge } from '../ui/kit';

interface CameraDirectoryProps {
  cameras: Camera[];
  role: UserRole;
  onSelectCamera: (camera: Camera) => void;
  onToggleStatus: (camera: Camera, action: 'start' | 'stop') => Promise<void> | void;
  onBulkAction?: (action: 'start' | 'stop', ids: string[] | null) => Promise<{ ok: boolean; data: any }>;
  onAddCamera: (camera: Partial<Camera>) => Promise<void>;
  onDeleteCamera: (id: string) => Promise<void>;
  onImportCSV: (csvContent: string) => Promise<void>;
}

type StatusFilter = 'all' | 'online' | 'offline' | 'running';

const AnprBadge: React.FC<{ cam: Camera }> = ({ cam }) =>
  cam.ingesting ? (
    cam.status === 'online' ? (
      <span className="badge badge-info"><ScanLine />Reading</span>
    ) : (
      <span className="badge badge-med"><span className="dot dot-warn live-pulse" />Starting</span>
    )
  ) : (
    <span className="badge badge-off">Stopped</span>
  );

const Modal: React.FC<{ title: string; onClose: () => void; children: React.ReactNode; width?: string }> = ({ title, onClose, children, width = 'max-w-lg' }) => (
  <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-[rgba(15,23,42,.45)] backdrop-blur-sm fade-in" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
    <div role="dialog" aria-modal="true" aria-label={title} className={`pop-in panel w-full ${width} overflow-hidden`}>
      <div className="panel-head">
        <div className="panel-title">{title}</div>
        <button onClick={onClose} className="btn btn-ghost btn-sm btn-icon" title="Close"><X /></button>
      </div>
      <div className="p-5">{children}</div>
    </div>
  </div>
);

export const CameraDirectory: React.FC<CameraDirectoryProps> = ({
  cameras,
  role,
  onSelectCamera,
  onToggleStatus,
  onBulkAction,
  onAddCamera,
  onDeleteCamera,
  onImportCSV,
}) => {
  const isAdmin = role === 'admin';
  const [activeId, setActiveId] = useState<string | null>(null);
  const [view, setView] = useState<'table' | 'grid'>('table');
  const [q, setQ] = useState('');
  const [dept, setDept] = useState('all');
  const [city, setCity] = useState('all');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [newCam, setNewCam] = useState<Partial<Camera>>({
    camera_id: '',
    department: GUJARAT_DEPARTMENTS[0],
    location_name: '',
    city: 'Ahmedabad',
    district: 'Ahmedabad',
    latitude: 23.0225,
    longitude: 72.5714,
    vendor: '',
    stream_url: '',
    format: 'HLS',
    status: 'offline',
  });

  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(() => setMsg(null), 9000);
    return () => clearTimeout(t);
  }, [msg]);

  const cities = useMemo(() => Array.from(new Set(cameras.map((c) => c.city).filter(Boolean))).sort(), [cameras]);
  const active = cameras.find((c) => c.camera_id === activeId) || null;

  // Preview the first camera (online first) so the inspector is never empty
  useEffect(() => {
    if (!activeId && cameras.length) {
      setActiveId((cameras.find((c) => c.status === 'online') || cameras[0]).camera_id);
    }
  }, [cameras, activeId]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return cameras.filter((c) => {
      if (status === 'online' && c.status !== 'online') return false;
      if (status === 'offline' && c.status === 'online') return false;
      if (status === 'running' && !c.ingesting) return false;
      if (dept !== 'all' && c.department !== dept) return false;
      if (city !== 'all' && c.city !== city) return false;
      if (s && !`${c.camera_id} ${c.location_name} ${c.department} ${c.vendor}`.toLowerCase().includes(s)) return false;
      return true;
    });
  }, [cameras, q, status, dept, city]);

  const online = cameras.filter((c) => c.status === 'online').length;
  const running = cameras.filter((c) => c.ingesting).length;

  const toggleOne = (id: string) =>
    setChecked((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  const allShown = filtered.length > 0 && filtered.every((c) => checked.has(c.camera_id));
  const quickSelect = (how: string) => {
    if (how === 'none') setChecked(new Set());
    else if (how === 'shown') setChecked(new Set(filtered.map((c) => c.camera_id)));
    else if (how === 'stopped') setChecked(new Set(filtered.filter((c) => !c.ingesting).map((c) => c.camera_id)));
    else if (how === 'running') setChecked(new Set(filtered.filter((c) => c.ingesting).map((c) => c.camera_id)));
    else setChecked(new Set(filtered.slice(0, Number(how)).map((c) => c.camera_id)));
  };

  const runOne = async (cam: Camera) => {
    const action = cam.ingesting ? 'stop' : 'start';
    setBusyIds((s) => new Set(s).add(cam.camera_id));
    try {
      await onToggleStatus(cam, action);
      setMsg({ ok: true, text: action === 'start' ? `${cam.camera_id}: starting. It turns Online when video frames arrive.` : `${cam.camera_id}: stopped.` });
    } catch (e: any) {
      setMsg({ ok: false, text: `${cam.camera_id}: ${e?.message || 'action failed'}` });
    } finally {
      setBusyIds((s) => { const n = new Set(s); n.delete(cam.camera_id); return n; });
    }
  };

  const runBulk = async (action: 'start' | 'stop', ids: string[] | null) => {
    if (!onBulkAction) return;
    const count = ids ? ids.length : cameras.length;
    if (count === 0) return;
    if (action === 'start' && count > 8 && !window.confirm(`Start ${count} cameras together?\n\nEvery running camera reads plates, which uses CPU. On a laptop, 4–8 cameras is comfortable.`)) return;
    if (action === 'stop' && !ids && !window.confirm('Stop plate reading on all cameras?')) return;
    setBulkBusy(true);
    setMsg(null);
    const { ok, data } = await onBulkAction(action, ids);
    setBulkBusy(false);
    if (!ok) {
      setMsg({ ok: false, text: data?.error || 'Request failed' });
      return;
    }
    if (action === 'start') {
      const skipped = (data.skipped || []) as { camera_id: string; reason: string }[];
      const noUrl = skipped.filter((s) => s.reason === 'no stream URL').length;
      const already = skipped.length - noUrl;
      setMsg({
        ok: true,
        text: `Starting ${data.queued?.length ?? 0} camera(s)${data.seconds_to_finish ? ` one by one (~${Math.ceil(data.seconds_to_finish)} s)` : ''}. Each turns Online when its video arrives.${already ? ` ${already} already running.` : ''}${noUrl ? ` ${noUrl} skipped (no stream URL).` : ''}`,
      });
    } else {
      setMsg({ ok: true, text: `Stopped ${data.marked_offline?.length ?? 0} camera(s).` });
      setChecked(new Set());
    }
  };

  const pick = (cam: Camera) => setActiveId(cam.camera_id);

  const submitAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCam.location_name) return;
    try {
      await onAddCamera(newCam);
      setShowAdd(false);
      setMsg({ ok: true, text: 'Camera saved.' });
    } catch (err: any) {
      setMsg({ ok: false, text: err?.message || 'Could not save camera' });
    }
  };

  const submitImport = async () => {
    if (!csvText.trim()) return;
    await onImportCSV(csvText);
    setShowImport(false);
    setCsvText('');
  };

  return (
    <div className="h-full flex flex-col gap-3 min-h-0">
      {/* Stats + actions */}
      <div className="flex flex-wrap items-stretch gap-3 shrink-0">
        <div className="grid grid-cols-3 gap-3 flex-1 min-w-[480px] stagger">
          <Stat icon={Cctv} value={<span className="num">{cameras.length}</span>} label="Registered cameras" />
          <Stat icon={Wifi} tone="ok" value={<span className="num">{online}</span>} label="Video online" />
          <Stat icon={ScanLine} tone="accent" value={<span className="num">{running}</span>} label="Plate reading on" />
        </div>
        <div className="flex items-center gap-2 ml-auto">
          <button onClick={() => pdfExportService.exportCamerasDirectory(filtered)} className="btn" title="Download list as PDF"><FileDown />PDF</button>
          {isAdmin && (
            <>
              <button onClick={() => setShowImport(true)} className="btn"><Upload />Import CSV</button>
              <button onClick={() => setShowAdd(true)} className="btn btn-primary"><Plus />Add camera</button>
            </>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_340px] 2xl:grid-cols-[minmax(0,1fr)_400px] gap-3">
        {/* List */}
        <div className="panel flex flex-col min-h-0 overflow-hidden reveal reveal-1">
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2.5 px-4 py-3 border-b border-line">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="w-4 h-4 text-ink3 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search ID, place or department" className="field pl-9" aria-label="Search cameras" />
            </div>
            <div className="segmented" role="group" aria-label="Status">
              {(['all', 'online', 'offline', 'running'] as StatusFilter[]).map((s) => (
                <button key={s} aria-pressed={status === s} onClick={() => setStatus(s)}>
                  {s === 'all' ? 'All' : s === 'online' ? 'Online' : s === 'offline' ? 'Offline' : 'Reading'}
                </button>
              ))}
            </div>
            <select value={city} onChange={(e) => setCity(e.target.value)} className="field !w-auto min-w-[130px]" aria-label="City">
              <option value="all">All cities</option>
              {cities.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select value={dept} onChange={(e) => setDept(e.target.value)} className="field !w-auto max-w-[180px]" aria-label="Department">
              <option value="all">All departments</option>
              {GUJARAT_DEPARTMENTS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            <div className="segmented" role="group" aria-label="View">
              <button aria-pressed={view === 'table'} onClick={() => setView('table')} title="Table"><List /></button>
              <button aria-pressed={view === 'grid'} onClick={() => setView('grid')} title="Cards"><LayoutGrid /></button>
            </div>
          </div>

          {/* Bulk bar */}
          {isAdmin && onBulkAction && (
            <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b border-line bg-panel2">
              <span className="eyebrow mr-1">Run cameras</span>
              <select value="" onChange={(e) => e.target.value && quickSelect(e.target.value)} className="field !h-8 !w-auto !text-[12px]" aria-label="Quick select">
                <option value="">Select…</option>
                <option value="4">First 4 shown</option>
                <option value="8">First 8 shown</option>
                <option value="16">First 16 shown</option>
                <option value="shown">All shown ({filtered.length})</option>
                <option value="stopped">Stopped (shown)</option>
                <option value="running">Reading (shown)</option>
                <option value="none">Clear selection</option>
              </select>
              <button disabled={bulkBusy || checked.size === 0} onClick={() => runBulk('start', Array.from(checked))} className="btn btn-sm btn-ok">
                {bulkBusy ? <Loader2 className="animate-spin" /> : <Play />}Start selected <span className="num">({checked.size})</span>
              </button>
              <button disabled={bulkBusy || checked.size === 0} onClick={() => runBulk('stop', Array.from(checked))} className="btn btn-sm btn-danger"><Square />Stop selected</button>
              <span className="w-px h-5 bg-line2 mx-1" />
              <button disabled={bulkBusy} onClick={() => runBulk('start', null)} className="btn btn-sm"><Play />Start all <span className="num">({cameras.length})</span></button>
              <button disabled={bulkBusy} onClick={() => runBulk('stop', null)} className="btn btn-sm"><Square />Stop all</button>
            </div>
          )}

          {msg && (
            <div className={`px-4 py-2.5 text-[12px] font-semibold border-b flex items-start gap-2 ${msg.ok ? 'bg-okbg text-ok border-[#A9DEC3]' : 'bg-critbg text-crit border-[#F5C2BD]'}`}>
              {msg.ok ? <CheckCircle2 className="w-4 h-4 shrink-0" /> : <AlertTriangle className="w-4 h-4 shrink-0" />}
              <span className="flex-1">{msg.text}</span>
              <button onClick={() => setMsg(null)} className="shrink-0 opacity-80 hover:opacity-100" title="Dismiss"><X className="w-4 h-4" /></button>
            </div>
          )}

          {/* Content */}
          <div className="flex-1 min-h-0 scroll-y">
            {filtered.length === 0 ? (
              <EmptyState icon={Cctv} title="No cameras found" hint="Clear the search or filters and try again." />
            ) : view === 'table' ? (
              <table className="table">
                <thead>
                  <tr>
                    {isAdmin && onBulkAction && (
                      <th className="w-10 !pr-0">
                        <input type="checkbox" aria-label="Select all shown" checked={allShown} onChange={() => quickSelect(allShown ? 'none' : 'shown')} className="w-4 h-4 accent-[#4C8DFF] cursor-pointer" />
                      </th>
                    )}
                    <th>Camera</th>
                    <th>Video</th>
                    <th>Plate reading</th>
                    <th className="hidden 2xl:table-cell">Department</th>
                    <th className="text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((cam) => {
                    const sel = cam.camera_id === activeId;
                    const busy = busyIds.has(cam.camera_id);
                    return (
                      <tr key={cam.camera_id} onClick={() => pick(cam)} className={`cursor-pointer ${sel ? '!bg-accentbg' : ''}`}>
                        {isAdmin && onBulkAction && (
                          <td className="!pr-0" onClick={(e) => e.stopPropagation()}>
                            <input type="checkbox" aria-label={`Select ${cam.camera_id}`} checked={checked.has(cam.camera_id)} onChange={() => toggleOne(cam.camera_id)} className="w-4 h-4 accent-[#4C8DFF] cursor-pointer" />
                          </td>
                        )}
                        <td className="max-w-[300px]">
                          <div className="font-semibold text-ink truncate">{cam.location_name}</div>
                          <div className="text-[12px] text-ink3 truncate">{cam.camera_id} · {cam.city}</div>
                        </td>
                        <td className="whitespace-nowrap"><StatusBadge status={cam.status} /></td>
                        <td className="whitespace-nowrap"><AnprBadge cam={cam} /></td>
                        <td className="hidden 2xl:table-cell max-w-[220px]"><span className="text-[12px] text-ink2 truncate block">{cam.department}</span></td>
                        <td className="text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                          <div className="inline-flex items-center gap-1.5">
                            <button onClick={() => onSelectCamera(cam)} className="btn btn-sm btn-icon" title="Open large view"><Maximize2 /></button>
                            {isAdmin && (
                              <button
                                onClick={() => runOne(cam)}
                                disabled={busy}
                                className={`btn btn-sm btn-icon ${cam.ingesting ? 'btn-danger' : 'btn-ok'}`}
                                title={cam.ingesting ? 'Stop plate reading' : 'Start plate reading'}
                              >
                                {busy ? <Loader2 className="animate-spin" /> : cam.ingesting ? <Square /> : <Play />}
                              </button>
                            )}
                            {isAdmin && (
                              <button
                                onClick={() => window.confirm(`Delete camera ${cam.camera_id}?`) && onDeleteCamera(cam.camera_id)}
                                className="btn btn-sm btn-icon btn-ghost"
                                title="Delete camera"
                              >
                                <Trash2 />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-3 p-4">
                {filtered.map((cam) => {
                  const sel = cam.camera_id === activeId;
                  return (
                    <div
                      key={cam.camera_id}
                      onClick={() => pick(cam)}
                      className={`rounded-xl border p-3.5 cursor-pointer transition-colors ${sel ? 'border-accent bg-accentbg' : 'border-line bg-panel2 hover:border-line2'}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          {isAdmin && onBulkAction && (
                            <input type="checkbox" aria-label={`Select ${cam.camera_id}`} checked={checked.has(cam.camera_id)} onClick={(e) => e.stopPropagation()} onChange={() => toggleOne(cam.camera_id)} className="w-4 h-4 accent-[#4C8DFF] cursor-pointer shrink-0" />
                          )}
                          <span className="text-[12px] font-semibold text-accent2 truncate">{cam.camera_id}</span>
                        </div>
                        <StatusBadge status={cam.status} />
                      </div>
                      <div className="mt-2 text-[14px] font-semibold text-ink truncate">{cam.location_name}</div>
                      <div className="text-[12px] text-ink3 truncate">{cam.department}</div>
                      <div className="mt-3 pt-3 border-t border-line flex items-center justify-between gap-2">
                        <AnprBadge cam={cam} />
                        <span className="text-[12px] text-ink2">{cam.city}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          <div className="px-4 py-2.5 border-t border-line text-[12px] text-ink3 num">
            Showing <b className="text-ink">{filtered.length}</b> of {cameras.length} · <span className="text-ink2">Video</span> = frames arriving at the server · <span className="text-ink2">Plate reading</span> = ANPR worker on
          </div>
        </div>

        {/* Inspector */}
        <div className="flex flex-col gap-3 min-h-0 reveal reveal-2">
          <div className="h-[260px] shrink-0">
            <LiveCameraFeed camera={active} onOpenModal={onSelectCamera} />
          </div>
          {active ? (
            <div className="panel flex-1 min-h-0 scroll-y">
              <div className="panel-head">
                <div className="flex items-center gap-3 min-w-0">
                  <IconBox icon={Cctv} size="sm" />
                  <div className="min-w-0">
                    <div className="panel-title truncate">{active.camera_id}</div>
                    <div className="panel-sub truncate">{active.vendor || 'Camera'}</div>
                  </div>
                </div>
                <StatusBadge status={active.status} />
              </div>
              <div className="p-4 space-y-4">
                <dl className="grid grid-cols-[100px_1fr] gap-y-2.5 gap-x-3 text-[13px]">
                  <dt className="text-ink3 flex items-center gap-1.5"><MapPin className="w-3.5 h-3.5" />Place</dt><dd className="font-semibold text-ink">{active.location_name}</dd>
                  <dt className="text-ink3 flex items-center gap-1.5"><Building2 className="w-3.5 h-3.5" />Dept.</dt><dd className="font-semibold text-ink">{active.department}</dd>
                  <dt className="text-ink3">City</dt><dd className="font-semibold text-ink">{active.city}, Gujarat</dd>
                  <dt className="text-ink3">GPS</dt><dd className="font-semibold text-ink num">{Number.isFinite(active.latitude) ? `${active.latitude.toFixed(5)}, ${active.longitude.toFixed(5)}` : '—'}</dd>
                  <dt className="text-ink3">Stream</dt><dd className="font-semibold text-ink">{active.format}</dd>
                  <dt className="text-ink3">Plate reading</dt><dd><AnprBadge cam={active} /></dd>
                </dl>
                <div className="flex items-center gap-2">
                  {isAdmin && (
                    <button onClick={() => runOne(active)} disabled={busyIds.has(active.camera_id)} className={`btn flex-1 ${active.ingesting ? 'btn-danger' : 'btn-ok'}`}>
                      {busyIds.has(active.camera_id) ? <Loader2 className="animate-spin" /> : active.ingesting ? <Square /> : <Play />}
                      {active.ingesting ? 'Stop plate reading' : 'Start plate reading'}
                    </button>
                  )}
                  <button onClick={() => onSelectCamera(active)} className="btn"><Maximize2 />Large view</button>
                </div>
              </div>
            </div>
          ) : (
            <div className="panel flex-1 flex items-center justify-center">
              <EmptyState icon={WifiOff} title="Select a camera" hint="Click any row to preview the camera and see its details." />
            </div>
          )}
        </div>
      </div>

      {showImport && (
        <Modal title="Import cameras from CSV" onClose={() => setShowImport(false)} width="max-w-xl">
          <p className="text-[13px] text-ink2 mb-3">One camera per line in this order:</p>
          <code className="block text-[12px] text-accent2 bg-base border border-line rounded-lg px-3 py-2 mb-3 break-all">camera_id,department,location_name,latitude,longitude,vendor,stream_url</code>
          <textarea rows={7} value={csvText} onChange={(e) => setCsvText(e.target.value)} placeholder="CAM-AHM-101,Ahmedabad Traffic Police,Paldi Crossroads,23.0135,72.5623,Vendor,https://…/index.m3u8" className="field" />
          <div className="flex justify-end gap-2 mt-4">
            <button onClick={() => setShowImport(false)} className="btn">Cancel</button>
            <button onClick={submitImport} className="btn btn-primary"><Upload />Import</button>
          </div>
        </Modal>
      )}

      {showAdd && (
        <Modal title="Add camera" onClose={() => setShowAdd(false)}>
          <form onSubmit={submitAdd} className="space-y-3.5">
            <div>
              <label className="label">Place name</label>
              <input required placeholder="e.g. Iscon Crossroads, SG Highway" value={newCam.location_name} onChange={(e) => setNewCam({ ...newCam, location_name: e.target.value })} className="field" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Camera ID (optional)</label>
                <input placeholder="Auto if empty" value={newCam.camera_id} onChange={(e) => setNewCam({ ...newCam, camera_id: e.target.value })} className="field" />
              </div>
              <div>
                <label className="label">City</label>
                <select value={newCam.city} onChange={(e) => setNewCam({ ...newCam, city: e.target.value })} className="field">
                  {['Ahmedabad', 'Gandhinagar', 'Surat', 'Vadodara', 'Rajkot'].map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="label">Department</label>
              <select value={newCam.department} onChange={(e) => setNewCam({ ...newCam, department: e.target.value })} className="field">
                {GUJARAT_DEPARTMENTS.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Stream URL (HLS .m3u8 or RTSP)</label>
              <input placeholder="https://…/index.m3u8" value={newCam.stream_url} onChange={(e) => setNewCam({ ...newCam, stream_url: e.target.value })} className="field" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Latitude</label>
                <input type="number" step="0.00001" value={newCam.latitude} onChange={(e) => setNewCam({ ...newCam, latitude: parseFloat(e.target.value) })} className="field num" />
              </div>
              <div>
                <label className="label">Longitude</label>
                <input type="number" step="0.00001" value={newCam.longitude} onChange={(e) => setNewCam({ ...newCam, longitude: parseFloat(e.target.value) })} className="field num" />
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowAdd(false)} className="btn">Cancel</button>
              <button type="submit" className="btn btn-primary">Save camera</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
};
