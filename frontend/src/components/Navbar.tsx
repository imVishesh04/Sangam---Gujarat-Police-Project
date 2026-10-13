import React, { useEffect, useState } from 'react';
import { ActiveTab } from '../types';
import { STORAGE_KEYS } from '../config/api';
import { Search, Volume2, VolumeX, Siren, CornerDownLeft } from 'lucide-react';

interface NavbarProps {
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  unacknowledgedAlertsCount: number;
  onTriggerDemoAlert: () => void;
  onQuickSearch?: (query: string) => void;
}

const TITLES: Record<ActiveTab, { title: string; sub: string }> = {
  dashboard: { title: 'Control Room', sub: 'Live map, camera feeds and alerts' },
  cameras: { title: 'Cameras', sub: 'All registered CCTV cameras' },
  alerts: { title: 'ANPR Alerts', sub: 'Watchlist matches, newest first' },
  tracking: { title: 'Route History', sub: 'Where a vehicle was seen, camera by camera' },
  watchlist: { title: 'Watchlist', sub: 'Vehicles flagged by police units' },
  detections: { title: 'Plate Reads', sub: 'Every number plate read by the cameras' },
  analytics: { title: 'System Health', sub: 'Cameras, database and services' },
};

export const Navbar: React.FC<NavbarProps> = ({ activeTab, onTriggerDemoAlert, onQuickSearch }) => {
  const [now, setNow] = useState(new Date());
  const [muted, setMuted] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEYS.AUDIO_MUTED) === 'true'; } catch { return false; }
  });
  const [q, setQ] = useState('');

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const toggleAudio = () => {
    const v = !muted;
    setMuted(v);
    try { localStorage.setItem(STORAGE_KEYS.AUDIO_MUTED, String(v)); } catch { /* ignore */ }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (q.trim() && onQuickSearch) {
      onQuickSearch(q.trim());
      setQ('');
    }
  };

  const meta = TITLES[activeTab] || TITLES.dashboard;
  const time = now.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false });
  const date = now.toLocaleDateString('en-GB', { timeZone: 'Asia/Kolkata', weekday: 'short', day: '2-digit', month: 'short' });

  return (
    <header className="h-16 shrink-0 z-30 flex items-center gap-4 px-5 border-b border-line bg-[rgba(255,255,255,.88)] backdrop-blur-md">
      {/* Title */}
      <div className="min-w-0 w-[260px] shrink-0 hidden md:block">
        <div className="flex items-center gap-2.5">
          <h1 key={activeTab} className="fade-in text-[17px] font-semibold text-ink truncate">{meta.title}</h1>
          <span className="badge badge-ok shrink-0">
            <span className="dot dot-live live-pulse" />
            Live
          </span>
        </div>
        <p className="text-[12px] text-ink3 truncate mt-0.5">{meta.sub}</p>
      </div>

      {/* Search */}
      <form onSubmit={submit} className="flex-1 min-w-0 max-w-[520px] mx-auto relative" role="search">
        <Search className="w-4 h-4 text-ink3 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search plate, camera ID or place"
          aria-label="Search plate, camera ID or place"
          className="field !h-10 !rounded-full pl-10 pr-12"
        />
        <button
          type="submit"
          aria-label="Search"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 h-7 w-9 rounded-full flex items-center justify-center bg-raised border border-line2 text-ink2 hover:text-white hover:bg-hover transition-colors"
        >
          <CornerDownLeft className="w-3.5 h-3.5" />
        </button>
      </form>

      {/* Right */}
      <div className="flex items-center gap-2 shrink-0">
        <div className="hidden lg:flex flex-col items-end leading-tight pr-2 mr-1 border-r border-line">
          <span className="num text-[15px] font-semibold text-ink">{time}</span>
          <span className="text-[11px] font-medium text-ink3">{date} · IST</span>
        </div>
        <button
          onClick={toggleAudio}
          className={`btn btn-icon ${muted ? '' : 'btn-primary'}`}
          title={muted ? 'Alert sound is off' : 'Alert sound is on'}
          aria-pressed={!muted}
        >
          {muted ? <VolumeX /> : <Volume2 />}
        </button>
        <button onClick={onTriggerDemoAlert} className="btn btn-danger" title="Send a test alert (demo data)">
          <Siren />
          <span className="hidden sm:inline">Test alert</span>
        </button>
      </div>
    </header>
  );
};
