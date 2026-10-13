import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Camera, Alert, HealthStats, TrackPoint } from '../../types';
import { CommandMap } from '../Map/CommandMap';
import { LiveCameraFeed } from '../Video/LiveCameraFeed';
import { LiveAlertsPanel } from '../Alerts/LiveAlertsPanel';
import {
  Columns2,
  Map as MapIcon,
  Columns3,
  LayoutGrid,
  ChevronDown,
  Search,
  PanelRightClose,
  PanelRightOpen,
  BellRing,
  Cctv,
  Video,
  Maximize2,
  Compass,
  ScanLine,
  Wifi,
  Route as RouteIcon,
  Check,
} from 'lucide-react';
import { EmptyState, Plate, StatusBadge, SeverityBadge, timeAgo } from '../ui/kit';

interface CommandCenterViewProps {
  cameras: Camera[];
  selectedCamera: Camera | null;
  onSelectCamera: (camera: Camera) => void;
  alerts: Alert[];
  health: HealthStats | null;
  onAcknowledgeAlert: (alertId: string | number, notes?: string) => void;
  onToggleCameraStatus: (camera: Camera, action: 'start' | 'stop') => void;
  onTrackPlate: (plate: string) => void;
  onOpenStreamModal: (camera: Camera) => void;
  onTriggerDemoAlert: () => void;
  routeOverlay?: { plate: string; points: TrackPoint[]; note?: string } | null;
  onClearRoute?: () => void;
  mapFocus?: { id: string; n: number } | null;
  onFocusDone?: () => void;
}

type Layout = 'command_map' | 'full_map' | 'split_50' | 'video_wall';
type Dock = 'alerts' | 'camera_list' | 'live_feed';

const DISTRICTS = [
  'All Districts', 'Ahmedabad', 'Amreli', 'Anand', 'Arvalli', 'Banaskantha', 'Bharuch', 'Bhavnagar', 'Botad',
  'Chhota Udaipur', 'Dahod', 'Dang', 'Devbhoomi Dwarka', 'Gandhinagar', 'Gir Somnath', 'Jamnagar', 'Junagadh',
  'Kheda', 'Kutch', 'Mahisagar', 'Mehsana', 'Morbi', 'Narmada', 'Navsari', 'Panchmahal', 'Patan', 'Porbandar',
  'Rajkot', 'Sabarkantha', 'Surat', 'Surendranagar', 'Tapi', 'Vadodara', 'Valsad',
];

const LAYOUTS: { id: Layout; label: string; icon: React.FC<{ className?: string }> }[] = [
  { id: 'command_map', label: 'Standard', icon: Columns2 },
  { id: 'full_map', label: 'Full map', icon: MapIcon },
  { id: 'split_50', label: 'Split 50/50', icon: Columns3 },
  { id: 'video_wall', label: 'Video wall', icon: LayoutGrid },
];

export const CommandCenterView: React.FC<CommandCenterViewProps> = ({
  cameras,
  selectedCamera,
  onSelectCamera,
  alerts,
  health,
  onAcknowledgeAlert,
  onToggleCameraStatus,
  onTrackPlate,
  onOpenStreamModal,
  onTriggerDemoAlert,
  routeOverlay = null,
  onClearRoute,
  mapFocus = null,
  onFocusDone,
}) => {
  const [layout, setLayout] = useState<Layout>('command_map');
  const [district, setDistrict] = useState('All Districts');
  const [distOpen, setDistOpen] = useState(false);
  const [distQ, setDistQ] = useState('');
  const [dockOpen, setDockOpen] = useState(true);
  const [dock, setDock] = useState<Dock>('alerts');
  const [camQ, setCamQ] = useState('');
  const distRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (distRef.current && !distRef.current.contains(e.target as Node)) { setDistOpen(false); setDistQ(''); }
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  useEffect(() => { if (mapFocus) setDistrict('All Districts'); }, [mapFocus?.n]);
  const districtCams = useMemo(() => cameras.filter((c) => district === 'All Districts' || c.city === district), [cameras, district]);
  const listCams = useMemo(() => {
    const s = camQ.trim().toLowerCase();
    return s ? districtCams.filter((c) => `${c.camera_id} ${c.location_name} ${c.city}`.toLowerCase().includes(s)) : districtCams;
  }, [districtCams, camQ]);

  const openAlerts = alerts.filter((a) => !a.is_acknowledged);
  // Always from the live camera list (refreshed every 8 s + socket events)
  const onlineCount = cameras.filter((c) => c.status === 'online').length;
  const readingCount = cameras.filter((c) => c.ingesting).length;

  const selectById = (id: string) => {
    const cam = cameras.find((c) => c.camera_id === id);
    if (cam) { onSelectCamera(cam); setDock('live_feed'); setDockOpen(true); }
  };
  const mapSelect = (cam: Camera) => {
    onSelectCamera(cam);
    if (layout === 'command_map') { setDock('live_feed'); setDockOpen(true); }
  };

  const mapProps = {
    cameras: districtCams,
    selectedCamera,
    activeAlerts: alerts,
    focusDistrict: district,
    trackPoints: routeOverlay?.points || [],
    searchedPlate: routeOverlay?.plate,
    routeNote: routeOverlay?.note,
    onClearRoute,
    focus: mapFocus,
    onFocusDone,
    className: 'h-full w-full',
  };

  const wallCams = useMemo(() => {
    const on = districtCams.filter((c) => c.status === 'online');
    return [...on, ...districtCams.filter((c) => c.status !== 'online')].slice(0, 6);
  }, [districtCams]);

  return (
    <div className="h-full w-full flex flex-col gap-3 min-h-0">
      {/* Toolbar */}
      <div className="panel shrink-0 flex flex-wrap items-center gap-3 px-3 py-2.5 !rounded-[14px] relative z-[600]">
        {/* District */}
        <div ref={distRef} className="relative">
          <button onClick={() => setDistOpen((v) => !v)} className="btn" aria-expanded={distOpen}>
            <Compass />
            <span className="max-w-[150px] truncate">{district === 'All Districts' ? 'All districts' : district}</span>
            <ChevronDown className={`transition-transform ${distOpen ? 'rotate-180' : ''}`} />
          </button>
          {distOpen && (
            <div className="pop-in absolute left-0 top-11 w-64 panel p-2 z-[700]">
              <div className="relative mb-2">
                <Search className="w-4 h-4 text-ink3 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input autoFocus value={distQ} onChange={(e) => setDistQ(e.target.value)} placeholder="Find district" className="field !h-9 pl-9" />
              </div>
              <div className="max-h-72 scroll-y space-y-0.5">
                {DISTRICTS.filter((d) => d.toLowerCase().includes(distQ.toLowerCase())).map((d) => (
                  <button
                    key={d}
                    onClick={() => { setDistrict(d); setDistOpen(false); setDistQ(''); }}
                    className={`w-full flex items-center justify-between h-9 px-3 rounded-lg text-[13px] font-semibold transition-colors ${district === d ? 'bg-accentbg text-accent2' : 'text-ink2 hover:bg-raised hover:text-ink'}`}
                  >
                    {d === 'All Districts' ? 'All districts' : d}
                    {district === d && <Check className="w-4 h-4 text-accent2" />}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* KPIs */}
        <div className="hidden lg:flex items-center gap-2">
          <span className="chip !cursor-default !h-9 !px-3.5"><Wifi className="text-ok" />Online <b className="num text-ink">{onlineCount}</b></span>
          <span className="chip !cursor-default !h-9 !px-3.5"><ScanLine className="text-accent2" />Reading <b className="num text-ink">{readingCount}</b></span>
          <span className="chip !cursor-default !h-9 !px-3.5"><BellRing className={openAlerts.length ? 'text-crit' : 'text-ink3'} />Open alerts <b className="num text-ink">{openAlerts.length}</b></span>
          {health && <span className="chip !cursor-default !h-9 !px-3.5 hidden 2xl:inline-flex">Reads today <b className="num text-ink">{health.detections_today.toLocaleString('en-IN')}</b></span>}
        </div>

        {/* Layout + dock */}
        <div className="flex items-center gap-2 ml-auto">
          <div className="segmented" role="group" aria-label="Layout">
            {LAYOUTS.map(({ id, label, icon: Icon }) => (
              <button key={id} aria-pressed={layout === id} onClick={() => setLayout(id)} title={label}>
                <Icon />
                <span className="hidden md:inline">{label}</span>
              </button>
            ))}
          </div>
          {layout === 'command_map' && (
            <button onClick={() => setDockOpen((v) => !v)} className="btn btn-icon" title={dockOpen ? 'Hide side panel' : 'Show side panel'} aria-pressed={dockOpen}>
              {dockOpen ? <PanelRightClose /> : <PanelRightOpen />}
            </button>
          )}
        </div>
      </div>

      {/* Stage */}
      <div key={layout} className="flex-1 min-h-0 relative fade-in">
        {layout === 'command_map' && (
          <div className="h-full flex gap-3 min-h-0">
            <div className="flex-1 min-w-0 panel overflow-hidden relative">
              <CommandMap {...mapProps} onSelectCamera={mapSelect} />
              {!dockOpen && openAlerts.length > 0 && (
                <button onClick={() => { setDockOpen(true); setDock('alerts'); }} className="absolute bottom-10 left-3 z-[450] btn btn-danger shadow-[var(--shadow-2)]">
                  <BellRing />{openAlerts.length} open alert{openAlerts.length > 1 ? 's' : ''}
                </button>
              )}
            </div>

            {dockOpen && (
              <aside className="w-[400px] shrink-0 panel flex flex-col min-h-0 overflow-hidden pop-in">
                <div className="px-2.5 py-2.5 border-b border-line">
                  <div className="segmented w-full" role="tablist">
                    <button className="flex-1 justify-center" aria-pressed={dock === 'alerts'} onClick={() => setDock('alerts')}>
                      <BellRing />Alerts {openAlerts.length > 0 && <span className="num min-w-[18px] h-[18px] px-1 rounded-full bg-crit text-white text-[10px] font-bold flex items-center justify-center">{openAlerts.length}</span>}
                    </button>
                    <button className="flex-1 justify-center" aria-pressed={dock === 'camera_list'} onClick={() => setDock('camera_list')}>
                      <Cctv />Cameras <span className="num text-ink3">{districtCams.length}</span>
                    </button>
                    <button className="flex-1 justify-center" aria-pressed={dock === 'live_feed'} onClick={() => setDock('live_feed')}>
                      <Video />Live feed
                    </button>
                  </div>
                </div>
                <div className="flex-1 min-h-0">
                  {dock === 'alerts' && (
                    <LiveAlertsPanel embedded alerts={alerts} onAcknowledgeAlert={onAcknowledgeAlert} onTrackPlate={onTrackPlate} onSelectCamera={selectById} onTriggerDemoAlert={onTriggerDemoAlert} />
                  )}
                  {dock === 'camera_list' && (
                    <div className="h-full flex flex-col min-h-0">
                      <div className="p-2.5 border-b border-line">
                        <div className="relative">
                          <Search className="w-4 h-4 text-ink3 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                          <input value={camQ} onChange={(e) => setCamQ(e.target.value)} placeholder="Search cameras" className="field !h-9 pl-9" />
                        </div>
                      </div>
                      <div className="flex-1 min-h-0 scroll-y p-2 space-y-1">
                        {listCams.length === 0 && <EmptyState icon={Cctv} title="No cameras" hint="Try another search or district." />}
                        {listCams.map((cam) => {
                          const sel = selectedCamera?.camera_id === cam.camera_id;
                          return (
                            <div
                              key={cam.camera_id}
                              onClick={() => { onSelectCamera(cam); setDock('live_feed'); }}
                              className={`group flex items-center gap-3 px-3 py-2.5 rounded-xl cursor-pointer border transition-colors ${sel ? 'bg-accentbg border-accent' : 'border-transparent hover:bg-raised'}`}
                            >
                              <span className={`dot ${cam.status === 'online' ? 'dot-live' : 'dot-off'}`} />
                              <div className="min-w-0 flex-1">
                                <div className="text-[13px] font-semibold text-ink truncate">{cam.location_name}</div>
                                <div className="text-[12px] text-ink3 truncate">{cam.camera_id} · {cam.city}</div>
                              </div>
                              <button onClick={(e) => { e.stopPropagation(); onOpenStreamModal(cam); }} className="btn btn-ghost btn-sm btn-icon opacity-70 group-hover:opacity-100" title="Open large view"><Maximize2 /></button>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {dock === 'live_feed' && (
                    <div className="h-full scroll-y p-2.5 space-y-2.5">
                      <div className="h-[240px]"><LiveCameraFeed camera={selectedCamera} onToggleStatus={onToggleCameraStatus} onOpenModal={onOpenStreamModal} /></div>
                      {selectedCamera && (
                        <div className="rounded-xl border border-line bg-panel2 p-3.5 space-y-3">
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="text-[14px] font-semibold text-ink">{selectedCamera.location_name}</div>
                              <div className="text-[12px] text-ink3">{selectedCamera.camera_id}</div>
                            </div>
                            <StatusBadge status={selectedCamera.status} starting={selectedCamera.ingesting} />
                          </div>
                          <dl className="grid grid-cols-[84px_1fr] gap-y-1.5 gap-x-3 text-[12px]">
                            <dt className="text-ink3">City</dt><dd className="font-semibold text-ink">{selectedCamera.city}</dd>
                            <dt className="text-ink3">Department</dt><dd className="font-semibold text-ink truncate">{selectedCamera.department}</dd>
                            <dt className="text-ink3">Stream</dt><dd className="font-semibold text-ink">{selectedCamera.format}</dd>
                          </dl>
                          <button onClick={() => onOpenStreamModal(selectedCamera)} className="btn btn-sm w-full"><Maximize2 />Large view and controls</button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </aside>
            )}
          </div>
        )}

        {layout === 'full_map' && (
          <div className="h-full panel overflow-hidden relative">
            <CommandMap {...mapProps} onSelectCamera={mapSelect} />
            {openAlerts.length > 0 && !routeOverlay && (
              <div className="pop-in absolute top-16 left-3 z-[450] w-[min(340px,calc(100%-24px))] panel p-3.5 space-y-2.5" style={{ borderLeft: '3px solid var(--color-crit)' }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="eyebrow !text-crit">Latest watchlist match</span>
                  <span className="text-[12px] text-ink3">{timeAgo(openAlerts[0].timestamp)}</span>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  <Plate value={openAlerts[0].matched_value} />
                  <SeverityBadge severity={openAlerts[0].severity} />
                </div>
                <div className="text-[13px] font-semibold text-ink truncate">{openAlerts[0].location_name}</div>
                <div className="flex items-center gap-2">
                  <button onClick={() => onTrackPlate(openAlerts[0].matched_value)} className="btn btn-sm btn-primary flex-1"><RouteIcon />Route history</button>
                  <button onClick={() => { setLayout('command_map'); setDockOpen(true); setDock('alerts'); }} className="btn btn-sm">All <span className="num">{openAlerts.length}</span></button>
                </div>
              </div>
            )}
          </div>
        )}

        {layout === 'split_50' && (
          <div className="h-full grid grid-cols-2 gap-3 min-h-0">
            <div className="panel overflow-hidden relative min-h-0">
              <CommandMap {...mapProps} onSelectCamera={(c) => onSelectCamera(c)} />
            </div>
            <div className="flex flex-col gap-3 min-h-0">
              <div className="h-[44%] min-h-[220px] shrink-0">
                <LiveCameraFeed camera={selectedCamera} onToggleStatus={onToggleCameraStatus} onOpenModal={onOpenStreamModal} />
              </div>
              <div className="flex-1 min-h-0">
                <LiveAlertsPanel compact alerts={alerts} onAcknowledgeAlert={onAcknowledgeAlert} onTrackPlate={onTrackPlate} onSelectCamera={selectById} onTriggerDemoAlert={onTriggerDemoAlert} />
              </div>
            </div>
          </div>
        )}

        {layout === 'video_wall' && (
          <div className="h-full scroll-y">
            {wallCams.length === 0 ? (
              <div className="panel h-full flex items-center justify-center"><EmptyState icon={Cctv} title="No cameras in this district" /></div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-3 auto-rows-[minmax(220px,1fr)] h-full stagger">
                {wallCams.map((cam) => (
                  <div
                    key={cam.camera_id}
                    onClick={() => onSelectCamera(cam)}
                    className={`rounded-[14px] overflow-hidden cursor-pointer transition-shadow ${selectedCamera?.camera_id === cam.camera_id ? 'ring-2 ring-accent' : ''}`}
                  >
                    <LiveCameraFeed camera={cam} onToggleStatus={onToggleCameraStatus} onOpenModal={onOpenStreamModal} compact />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
