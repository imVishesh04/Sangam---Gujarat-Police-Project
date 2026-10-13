import React from 'react';
import { AlertOctagon, AlertTriangle, Info, ShieldCheck, LucideIcon } from 'lucide-react';
import type { AlertSeverity } from '../../types';

/* ---------- formatting ---------------------------------------------------- */

const IST = 'Asia/Kolkata';

export const fmtTime = (iso?: string | null) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-GB', { timeZone: IST, hour12: false });
};

export const fmtDate = (iso?: string | null) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-GB', { timeZone: IST, day: '2-digit', month: 'short', year: 'numeric' });
};

export const fmtDateTime = (iso?: string | null) => (iso ? `${fmtDate(iso)}, ${fmtTime(iso)}` : '—');

export const timeAgo = (iso?: string | null) => {
  if (!iso) return '—';
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
};

/** "GJ01AB1234" -> "GJ 01 AB 1234" for readability */
export const prettyPlate = (raw?: string) => {
  const p = (raw || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  const m = p.match(/^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{1,4})$/);
  if (m) return [m[1], m[2], m[3], m[4]].filter(Boolean).join(' ');
  const bh = p.match(/^(\d{2})(BH)(\d{4})([A-Z]{1,2})$/);
  if (bh) return `${bh[1]} ${bh[2]} ${bh[3]} ${bh[4]}`;
  return p || '—';
};

/* ---------- small building blocks ---------------------------------------- */

export const Plate: React.FC<{ value?: string; size?: 'md' | 'lg'; className?: string }> = ({ value, size = 'md', className = '' }) => (
  <span className={`plate ${size === 'lg' ? 'plate-lg' : ''} ${className}`} title={value}>
    <span>{prettyPlate(value)}</span>
  </span>
);

const SEV: Record<AlertSeverity, { cls: string; label: string; icon: LucideIcon; color: string }> = {
  critical: { cls: 'badge-crit', label: 'Critical', icon: AlertOctagon, color: '#E5372A' },
  high: { cls: 'badge-high', label: 'High', icon: AlertTriangle, color: '#EE7A1A' },
  medium: { cls: 'badge-med', label: 'Medium', icon: Info, color: '#E0A100' },
  low: { cls: 'badge-low', label: 'Low', icon: ShieldCheck, color: '#14A398' },
};

export const sevColor = (s?: string) => (SEV[(s as AlertSeverity) || 'medium'] || SEV.medium).color;

export const SeverityBadge: React.FC<{ severity?: string; className?: string }> = ({ severity, className = '' }) => {
  const s = SEV[(severity as AlertSeverity) || 'medium'] || SEV.medium;
  const Icon = s.icon;
  return (
    <span className={`badge ${s.cls} ${className}`}>
      <Icon />
      {s.label}
    </span>
  );
};

export const StatusBadge: React.FC<{ status?: string; starting?: boolean; className?: string }> = ({ status, starting, className = '' }) => {
  if (starting && status !== 'online') {
    return (
      <span className={`badge badge-med ${className}`}>
        <span className="dot dot-warn live-pulse" />
        Starting
      </span>
    );
  }
  if (status === 'online') {
    return (
      <span className={`badge badge-ok ${className}`}>
        <span className="dot dot-live" />
        Online
      </span>
    );
  }
  if (status === 'warning') {
    return (
      <span className={`badge badge-med ${className}`}>
        <span className="dot dot-warn" />
        Warning
      </span>
    );
  }
  return (
    <span className={`badge badge-off ${className}`}>
      <span className="dot dot-off" />
      Offline
    </span>
  );
};

type Tone = 'accent' | 'ok' | 'crit' | 'high' | 'med' | 'low' | 'plain';
const TONE: Record<Tone, React.CSSProperties> = {
  accent: { color: 'var(--color-accent2)', background: 'var(--color-accentbg)', borderColor: '#B9CEF7' },
  ok: { color: 'var(--color-ok)', background: 'var(--color-okbg)', borderColor: '#A9DEC3' },
  crit: { color: 'var(--color-crit)', background: 'var(--color-critbg)', borderColor: '#F5C2BD' },
  high: { color: 'var(--color-high)', background: 'var(--color-highbg)', borderColor: '#F7CDAA' },
  med: { color: 'var(--color-med)', background: 'var(--color-medbg)', borderColor: '#F0D78A' },
  low: { color: 'var(--color-low)', background: 'var(--color-lowbg)', borderColor: '#A8DCD6' },
  plain: {},
};

export const IconBox: React.FC<{
  icon?: LucideIcon;
  tone?: Tone;
  size?: 'sm' | 'md' | 'lg';
  children?: React.ReactNode;
  className?: string;
}> = ({ icon: Icon, tone = 'accent', size = 'md', children, className = '' }) => (
  <span
    className={`icon-box ${size === 'sm' ? 'icon-box-sm' : size === 'lg' ? 'icon-box-lg' : ''} ${className}`}
    style={TONE[tone]}
  >
    {Icon ? <Icon /> : children}
  </span>
);

export const PanelHead: React.FC<{
  icon?: LucideIcon;
  tone?: Tone;
  title: React.ReactNode;
  sub?: React.ReactNode;
  right?: React.ReactNode;
  className?: string;
}> = ({ icon, tone = 'accent', title, sub, right, className = '' }) => (
  <div className={`panel-head ${className}`}>
    <div className="flex items-center gap-3 min-w-0">
      {icon && <IconBox icon={icon} tone={tone} size="sm" />}
      <div className="min-w-0">
        <div className="panel-title truncate">{title}</div>
        {sub && <div className="panel-sub truncate">{sub}</div>}
      </div>
    </div>
    {right && <div className="flex items-center gap-2 shrink-0">{right}</div>}
  </div>
);

export const EmptyState: React.FC<{ icon: LucideIcon; title: string; hint?: React.ReactNode; action?: React.ReactNode }> = ({ icon, title, hint, action }) => (
  <div className="empty fade-in">
    <IconBox icon={icon} tone="plain" size="lg" />
    <div className="text-[14px] font-semibold text-ink">{title}</div>
    {hint && <div className="max-w-sm text-ink3 text-[12px] leading-relaxed">{hint}</div>}
    {action}
  </div>
);

export const Stat: React.FC<{ icon: LucideIcon; tone?: Tone; value: React.ReactNode; label: string; className?: string }> = ({ icon, tone = 'accent', value, label, className = '' }) => (
  <div className={`stat ${className}`}>
    <IconBox icon={icon} tone={tone} />
    <div className="min-w-0">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  </div>
);

/** Brand mark for SANGAM (original — not a government emblem). */
export const SangamMark: React.FC<{ size?: number }> = ({ size = 36 }) => (
  <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
    <defs>
      <linearGradient id="sg-a" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#5B9BFF" />
        <stop offset="1" stopColor="#2457C5" />
      </linearGradient>
    </defs>
    <rect x="1" y="1" width="38" height="38" rx="11" fill="url(#sg-a)" />
    <path d="M20 8.5 29.5 12v7.4c0 6-4 10.6-9.5 12.1-5.5-1.5-9.5-6.1-9.5-12.1V12L20 8.5Z" fill="none" stroke="#fff" strokeWidth="2" strokeLinejoin="round" />
    <circle cx="20" cy="19.5" r="3.4" fill="#F6C344" />
    <path d="M13.5 19.5h3.1M23.4 19.5h3.1" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
  </svg>
);
