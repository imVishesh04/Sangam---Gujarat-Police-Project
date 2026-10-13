import React, { useEffect, useState } from 'react';
import { Send, RefreshCw, ChevronDown } from 'lucide-react';
import { apiService } from '../../services/apiService';
import { useAuth } from '../../context/AuthContext';
import { BrandLogo } from '../ui/BrandLogos';

const mask = (v: string) => {
  const d = String(v || '').replace(/\D/g, '');
  return d.length >= 4 ? `•••• ${d.slice(-4)}` : '••••';
};

const PROVIDER: Record<string, string> = {
  twilio: 'Twilio WhatsApp',
  meta: 'Meta WhatsApp Cloud',
  callmebot: 'WhatsApp (CallMeBot)',
  webbridge: 'WhatsApp (linked device)',
  log: 'Log only (no sending)',
};

// Status + test controls for the WhatsApp alert agent (backend: /notifications/*)
export const WhatsAppAgentCard: React.FC = () => {
  const { role } = useAuth();
  const [status, setStatus] = useState<any | null>(null);
  const [recent, setRecent] = useState<any[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const refresh = async () => {
    const [s, r] = await Promise.all([apiService.getNotificationStatus(), apiService.getRecentNotifications()]);
    setStatus(s);
    setRecent(r);
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  }, []);

  const send = async (mode: 'ping' | 'latest_alert') => {
    setBusy(mode);
    setResult(null);
    const { ok, data } = await apiService.sendNotificationTest(mode);
    if (data?.error) setResult({ ok: false, text: `Failed: ${data.error}` });
    else {
      const rs: any[] = data?.results || [];
      const good = rs.filter((r) => r.ok).length;
      setResult({ ok: ok && good === rs.length, text: rs.length ? `${good} of ${rs.length} sent${good < rs.length ? ` · ${rs.filter((r) => !r.ok).map((r) => r.info).join('; ')}` : ''}` : ok ? 'Done' : 'Failed' });
    }
    setBusy(null);
    refresh();
  };

  const on = status?.enabled && status?.provider !== 'log';
  const last = recent[0];

  return (
    <div className="panel overflow-hidden">
      <div className="panel-head">
        <div className="flex items-center gap-3">
          <span className="icon-box icon-box-sm" style={{ background: 'var(--color-okbg)', borderColor: '#A9DEC3' }}><BrandLogo name="whatsapp" size={16} /></span>
          <div>
            <div className="panel-title">WhatsApp alert agent</div>
            <div className="panel-sub">Sends every watchlist match to officers</div>
          </div>
        </div>
        <span className={`badge ${on ? 'badge-ok' : 'badge-off'}`}>
          {on && <span className="dot dot-live" />}
          {!status ? 'Unknown' : !status.enabled ? 'Off' : status.provider === 'log' ? 'Log only' : 'On'}
        </span>
      </div>

      <div className="p-4 space-y-3.5">
        {status ? (
          <dl className="grid grid-cols-[120px_1fr] gap-y-2 gap-x-3 text-[13px]">
            <dt className="text-ink3">Channel</dt><dd className="font-semibold text-ink">{PROVIDER[status.provider] || status.provider}</dd>
            <dt className="text-ink3">Sends to</dt>
            <dd className="font-semibold text-ink num">{status.recipients?.length ? status.recipients.map(mask).join(', ') : 'Not set'}</dd>
            <dt className="text-ink3">Vehicle registry</dt>
            <dd className="font-semibold text-ink">{status.rto_provider === 'vahan_http' ? 'VAHAN API' : status.rto_provider === 'sample' ? 'Sample data + RTO code' : 'RTO code only'}</dd>
            <dt className="text-ink3">Backup channel</dt>
            <dd className="font-semibold text-ink flex items-center gap-1.5"><BrandLogo name="telegram" size={14} />{status.telegram ? 'Telegram on' : 'Telegram off'}</dd>
            <dt className="text-ink3">Sent / failed</dt>
            <dd className="font-semibold text-ink num">{status.sent} / {status.failed} <span className="text-ink3 font-medium">· {status.skipped} skipped</span></dd>
          </dl>
        ) : (
          <p className="text-[13px] text-ink2">Agent status not available.</p>
        )}

        {role === 'admin' && (
          <div className="flex flex-wrap gap-2">
            <button onClick={() => send('ping')} disabled={!!busy} className="btn btn-sm btn-ok">
              {busy === 'ping' ? <RefreshCw className="animate-spin" /> : <Send />}Send test message
            </button>
            <button onClick={() => send('latest_alert')} disabled={!!busy} className="btn btn-sm">
              {busy === 'latest_alert' ? <RefreshCw className="animate-spin" /> : <Send />}Re-send latest alert
            </button>
          </div>
        )}
        {result && (
          <div className={`text-[12px] font-semibold rounded-lg px-3 py-2 border ${result.ok ? 'bg-okbg border-[#A9DEC3] text-ok' : 'bg-critbg border-[#F5C2BD] text-crit'}`}>{result.text}</div>
        )}

        {last && (
          <details className="group rounded-lg border border-line bg-panel2">
            <summary className="cursor-pointer list-none flex items-center justify-between gap-2 px-3 py-2.5 text-[12px] font-semibold text-ink">
              <span className="truncate">Last message · alert #{last.alert_id} · <span className="num">{last.time}</span></span>
              <ChevronDown className="w-4 h-4 text-ink3 transition-transform group-open:rotate-180" />
            </summary>
            <pre className="px-3 pb-3 whitespace-pre-wrap font-sans text-[12px] leading-relaxed text-ink2 max-h-56 overflow-y-auto">{last.body}</pre>
          </details>
        )}
      </div>
    </div>
  );
};
