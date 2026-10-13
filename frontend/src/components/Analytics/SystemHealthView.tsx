import React, { useEffect, useState } from 'react';
import { HealthStats, Camera } from '../../types';
import { apiService } from '../../services/apiService';
import { WhatsAppAgentCard } from './WhatsAppAgentCard';
import { Cctv, ScanLine, BellRing, Database, RefreshCw, Building2, Layers, CheckCircle2, Wifi } from 'lucide-react';
import { BrandLogo, BrandName } from '../ui/BrandLogos';
import { EmptyState, IconBox, Stat } from '../ui/kit';

const SERVICES: { brand: BrandName; title: string; sub: string; state: string }[] = [
  { brand: 'flask', title: 'API server', sub: 'Flask + eventlet', state: 'Running' },
  { brand: 'socketio', title: 'Live updates', sub: 'Socket.IO push to every dashboard', state: 'Connected' },
  { brand: 'opencv', title: 'Video decode', sub: 'OpenCV + AES-128 HLS (Sentinel grid)', state: 'Ready' },
  { brand: 'python', title: 'Plate reading', sub: 'YOLOv8 finds plates, EasyOCR reads them', state: 'Ready' },
  { brand: 'esri', title: 'Satellite map', sub: 'Esri World Imagery + labels', state: 'Online' },
  { brand: 'openstreetmap', title: 'Road routing', sub: 'OpenStreetMap roads (OSRM)', state: 'Online' },
];

export const SystemHealthView: React.FC = () => {
  const [health, setHealth] = useState<HealthStats | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const [h, c] = await Promise.all([apiService.getHealth(), apiService.getCameras()]);
      setHealth(h);
      setCameras(c);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  if (!health) {
    return (
      <div className="h-full grid grid-cols-2 xl:grid-cols-4 gap-3 content-start">
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="skeleton h-[74px]" />)}
      </div>
    );
  }

  const pct = health.total_cameras > 0 ? Math.round((health.cameras_online / health.total_cameras) * 100) : 0;
  const engine = (health.database_engine || 'SQLite').toString();
  const isPg = /postgres/i.test(engine);

  const byDistrict: Record<string, { total: number; online: number; reading: number }> = {};
  cameras.forEach((c) => {
    const d = c.city || 'Statewide';
    byDistrict[d] = byDistrict[d] || { total: 0, online: 0, reading: 0 };
    byDistrict[d].total += 1;
    if (c.status === 'online') byDistrict[d].online += 1;
    if (c.ingesting) byDistrict[d].reading += 1;
  });
  const districts = Object.entries(byDistrict).map(([name, v]) => ({ name, ...v })).sort((a, b) => b.total - a.total);

  return (
    <div className="h-full flex flex-col gap-3 min-h-0">
      <div className="flex items-stretch gap-3 shrink-0">
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 flex-1 stagger">
          <Stat icon={Cctv} value={<span className="num">{health.cameras_online}<span className="text-ink3 text-[15px] font-semibold"> / {health.total_cameras}</span></span>} label={`Cameras online · ${pct}%`} tone="ok" />
          <Stat icon={ScanLine} value={<span className="num">{health.detections_today.toLocaleString('en-IN')}</span>} label="Plate reads today" />
          <Stat icon={BellRing} tone="crit" value={<span className="num">{health.alerts_today}</span>} label={`Matches today · ${health.active_watchlists} on list`} />
          <div className="stat">
            <IconBox tone="accent"><BrandLogo name={isPg ? 'postgresql' : 'sqlite'} size={18} color={isPg ? undefined : '#1A4FBA'} /></IconBox>
            <div className="min-w-0">
              <div className="stat-value !text-[17px] truncate">{isPg ? 'PostgreSQL' : 'SQLite'}</div>
              <div className="stat-label flex items-center gap-1"><CheckCircle2 className="w-3.5 h-3.5 text-ok" />Database connected</div>
            </div>
          </div>
        </div>
        <button onClick={load} className="btn self-center" title="Refresh"><RefreshCw className={loading ? 'animate-spin' : ''} />Refresh</button>
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-2 gap-3">
        <div className="flex flex-col gap-3 min-h-0 scroll-y">
          <div className="panel overflow-hidden reveal reveal-1">
            <div className="panel-head">
              <div className="flex items-center gap-3">
                <IconBox icon={Database} size="sm" />
                <div>
                  <div className="panel-title">Platform services</div>
                  <div className="panel-sub">What is running behind the dashboard</div>
                </div>
              </div>
              <span className="badge badge-ok"><span className="dot dot-live" />Operational</span>
            </div>
            <ul className="divide-y divide-line">
              {SERVICES.map((s) => (
                <li key={s.title} className="flex items-center gap-3 px-4 py-3">
                  <span className="icon-box icon-box-sm" ><BrandLogo name={s.brand} size={15} /></span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-semibold text-ink">{s.title}</div>
                    <div className="text-[12px] text-ink3 truncate">{s.sub}</div>
                  </div>
                  <span className="badge badge-ok shrink-0">{s.state}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="reveal reveal-2"><WhatsAppAgentCard /></div>
        </div>

        <div className="flex flex-col gap-3 min-h-0 scroll-y">
          <div className="panel overflow-hidden flex flex-col min-h-[260px] reveal reveal-2">
            <div className="panel-head">
              <div className="flex items-center gap-3">
                <IconBox icon={Building2} size="sm" />
                <div>
                  <div className="panel-title">Cameras by district</div>
                  <div className="panel-sub">Live from the camera registry</div>
                </div>
              </div>
              <span className="badge badge-off num">{districts.length} districts</span>
            </div>
            {districts.length === 0 ? (
              <EmptyState icon={Cctv} title="No cameras registered yet" />
            ) : (
              <ul className="p-2 space-y-1">
                {districts.map((d) => {
                  const p = d.total ? Math.round((d.online / d.total) * 100) : 0;
                  return (
                    <li key={d.name} className="flex items-center gap-4 px-3 py-2.5 rounded-lg hover:bg-panel2 transition-colors">
                      <div className="w-36 min-w-0">
                        <div className="text-[13px] font-semibold text-ink truncate">{d.name}</div>
                        <div className="text-[12px] text-ink3 num">{d.total} cameras</div>
                      </div>
                      <div className="flex-1 h-2 rounded-full bg-raised overflow-hidden">
                        <div className="h-full rounded-full bg-ok transition-[width] duration-700" style={{ width: `${p}%` }} />
                      </div>
                      <div className="w-[120px] text-right text-[12px] num">
                        <span className="font-semibold text-ink"><Wifi className="w-3.5 h-3.5 inline -mt-0.5 mr-1 text-ok" />{d.online}</span>
                        <span className="text-ink3"> · {d.reading} reading</span>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="panel p-4 space-y-3 reveal reveal-3" style={{ borderColor: '#F0D78A' }}>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-3">
                <IconBox icon={Layers} tone="med" size="sm" />
                <div>
                  <div className="panel-title">Statewide rollout</div>
                  <div className="panel-sub">Roadmap — not live data</div>
                </div>
              </div>
              <span className="badge badge-med">Plan</span>
            </div>
            <div className="grid grid-cols-3 gap-2.5">
              {[
                { v: '~80,000', l: 'cameras, statewide target' },
                { v: 'Two-tier', l: 'decode at district, match centrally' },
                { v: '234 fps', l: 'measured, 10 live feeds, one laptop' },
              ].map((x) => (
                <div key={x.l} className="rounded-lg border border-line bg-panel2 p-3">
                  <div className="text-[16px] font-bold text-med num">{x.v}</div>
                  <div className="text-[12px] text-ink3 mt-0.5 leading-snug">{x.l}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
