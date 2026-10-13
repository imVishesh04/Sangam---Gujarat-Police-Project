import React, { useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { ActiveTab } from '../types';
import {
  LayoutDashboard,
  Cctv,
  BellRing,
  Route,
  ShieldAlert,
  ScanLine,
  Activity,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  ChevronUp,
  LucideIcon,
} from 'lucide-react';
import { SangamMark } from './ui/kit';

interface SidebarProps {
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  unacknowledgedAlertsCount: number;
}

const NAV: { group: string; items: { id: ActiveTab; label: string; icon: LucideIcon }[] }[] = [
  {
    group: 'Operations',
    items: [
      { id: 'dashboard', label: 'Control Room', icon: LayoutDashboard },
      { id: 'alerts', label: 'ANPR Alerts', icon: BellRing },
      { id: 'tracking', label: 'Route History', icon: Route },
    ],
  },
  {
    group: 'Data',
    items: [
      { id: 'cameras', label: 'Cameras', icon: Cctv },
      { id: 'watchlist', label: 'Watchlist', icon: ShieldAlert },
      { id: 'detections', label: 'Plate Reads', icon: ScanLine },
    ],
  },
  {
    group: 'System',
    items: [{ id: 'analytics', label: 'System Health', icon: Activity }],
  },
];

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, setActiveTab, unacknowledgedAlertsCount }) => {
  const { user, logout, role } = useAuth();
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem('sangam.sidebar') === '1'; } catch { return false; }
  });
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try { localStorage.setItem('sangam.sidebar', collapsed ? '1' : '0'); } catch { /* ignore */ }
  }, [collapsed]);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menuOpen]);

  const name = user?.fullName || user?.username || 'Officer';
  const initials = (user?.username || 'GP').slice(0, 2).toUpperCase();

  return (
    <aside
      className={`h-full shrink-0 flex flex-col bg-panel border-r border-line z-40 transition-[width] duration-300 ease-[cubic-bezier(.2,.7,.2,1)] ${
        collapsed ? 'w-[76px]' : 'w-[248px]'
      }`}
    >
      {/* Brand */}
      <div className={`h-16 flex items-center border-b border-line ${collapsed ? 'justify-center px-0' : 'px-4 gap-3'}`}>
        <SangamMark size={36} />
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-bold tracking-[.06em] text-ink leading-none">SANGAM</div>
            <div className="text-[11px] font-medium text-ink3 mt-1 truncate">Vehicle intelligence console</div>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 scroll-y py-3 px-3 space-y-5" aria-label="Main">
        {NAV.map((g) => (
          <div key={g.group}>
            {!collapsed ? (
              <div className="eyebrow px-3 mb-2">{g.group}</div>
            ) : (
              <div className="mx-auto mb-2 h-px w-6 bg-line" />
            )}
            <ul className="space-y-1">
              {g.items.map((item) => {
                const Icon = item.icon;
                const active = activeTab === item.id;
                const badge = item.id === 'alerts' ? unacknowledgedAlertsCount : 0;
                return (
                  <li key={item.id}>
                    <button
                      onClick={() => setActiveTab(item.id)}
                      title={collapsed ? item.label : undefined}
                      aria-current={active ? 'page' : undefined}
                      className={`group relative w-full flex items-center h-10 rounded-[10px] text-[13px] font-semibold transition-colors duration-200 ${
                        collapsed ? 'justify-center' : 'gap-3 px-3'
                      } ${active ? 'bg-accentbg text-accent2' : 'text-ink2 hover:bg-raised hover:text-ink'}`}
                    >
                      {active && <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r bg-accent" />}
                      <Icon className={`w-[18px] h-[18px] shrink-0 ${active ? 'text-accent2' : 'text-ink3 group-hover:text-ink2'}`} />
                      {!collapsed && <span className="flex-1 text-left truncate">{item.label}</span>}
                      {badge > 0 &&
                        (collapsed ? (
                          <span className="absolute top-1.5 right-3 h-2.5 w-2.5 rounded-full bg-crit ring-2 ring-panel" />
                        ) : (
                          <span className="num min-w-[22px] h-[20px] px-1.5 rounded-full bg-crit text-white text-[11px] font-bold flex items-center justify-center">
                            {badge > 99 ? '99+' : badge}
                          </span>
                        ))}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* Collapse toggle */}
      <div className={`px-3 pb-2 ${collapsed ? 'flex justify-center' : ''}`}>
        <button
          onClick={() => setCollapsed((v) => !v)}
          className={`btn btn-ghost btn-sm ${collapsed ? 'btn-icon' : 'w-full justify-start'}`}
          title={collapsed ? 'Expand menu' : 'Collapse menu'}
        >
          {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>

      {/* Profile */}
      <div ref={menuRef} className="relative border-t border-line p-3">
        {menuOpen && (
          <div className="pop-in absolute bottom-full left-3 right-3 mb-2 panel overflow-hidden min-w-[200px]">
            <div className="px-4 py-3 border-b border-line">
              <div className="text-[13px] font-semibold text-ink truncate">{name}</div>
              <div className="text-[12px] text-ink3 mt-0.5 truncate">
                {user?.badgeNumber || '—'} · <span className="text-accent2 font-semibold capitalize">{role}</span>
              </div>
            </div>
            <button
              onClick={() => { setMenuOpen(false); logout(); }}
              className="w-full flex items-center gap-2.5 px-4 h-11 text-[13px] font-semibold text-crit hover:bg-critbg transition-colors"
            >
              <LogOut className="w-4 h-4" />
              Sign out
            </button>
          </div>
        )}
        <button
          onClick={() => setMenuOpen((v) => !v)}
          className={`w-full flex items-center rounded-[10px] p-1.5 hover:bg-raised transition-colors ${collapsed ? 'justify-center' : 'gap-3'}`}
          aria-expanded={menuOpen}
        >
          <span className="h-9 w-9 rounded-[10px] bg-accentbg border border-[#B9CEF7] text-accent2 text-[13px] font-bold flex items-center justify-center shrink-0">
            {initials}
          </span>
          {!collapsed && (
            <>
              <span className="min-w-0 flex-1 text-left">
                <span className="block text-[13px] font-semibold text-ink truncate">{name}</span>
                <span className="block text-[11px] font-medium text-ink3 capitalize truncate">{role}</span>
              </span>
              <ChevronUp className={`w-4 h-4 text-ink3 transition-transform ${menuOpen ? '' : 'rotate-180'}`} />
            </>
          )}
        </button>
      </div>
    </aside>
  );
};
