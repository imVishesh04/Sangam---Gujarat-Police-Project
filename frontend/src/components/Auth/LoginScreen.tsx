import React, { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { User, KeyRound, Eye, EyeOff, ArrowRight, Loader2, ShieldCheck, Eye as EyeIcon, AlertTriangle, Cctv, ScanLine, BellRing, Route as RouteIcon } from 'lucide-react';
import { SangamMark } from '../ui/kit';

const FEATURES = [
  { icon: Cctv, title: 'One view of every camera', text: 'Police, city and highway CCTV on one satellite map.' },
  { icon: ScanLine, title: 'Reads number plates', text: 'Each plate is read and checked against the watchlist.' },
  { icon: BellRing, title: 'Alerts in seconds', text: 'Matches reach the control room and WhatsApp at once.' },
  { icon: RouteIcon, title: 'Route of a vehicle', text: 'See every camera that saw a vehicle, in time order.' },
];

export const LoginScreen: React.FC = () => {
  const { login, demoLogin, isSigningIn } = useAuth();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('admin123');
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await login({ username, password });
    } catch (err: any) {
      setError(err?.message || 'Sign-in failed. Check your username and password.');
    }
  };

  const demo = async (role: 'admin' | 'viewer') => {
    setError(null);
    try {
      await demoLogin(role);
    } catch (err: any) {
      setError(err?.message || 'Sign-in failed.');
    }
  };

  return (
    <div className="min-h-screen w-full bg-panel grid grid-cols-1 lg:grid-cols-[1.1fr_1fr] overflow-hidden">
      {/* Left: brand */}
      <section className="relative hidden lg:flex flex-col justify-between p-12 overflow-hidden border-r border-line">
        <div className="absolute inset-0 pointer-events-none" style={{ background: 'radial-gradient(900px 520px at 10% 0%, rgba(29,91,214,.14), transparent 60%), radial-gradient(700px 420px at 95% 100%, rgba(11,122,69,.08), transparent 60%), linear-gradient(180deg, #F7F9FD, #EEF3FA)' }} />
        <div className="absolute inset-0 pointer-events-none opacity-[.35]" style={{ backgroundImage: 'linear-gradient(#DCE4F0 1px, transparent 1px), linear-gradient(90deg, #DCE4F0 1px, transparent 1px)', backgroundSize: '44px 44px' }} />

        <div className="relative flex items-center gap-3 reveal">
          <SangamMark size={44} />
          <div>
            <div className="text-[20px] font-bold tracking-[.08em] text-ink">SANGAM</div>
            <div className="text-[13px] text-ink3">Vehicle intelligence console</div>
          </div>
        </div>

        <div className="relative max-w-xl">
          <h1 className="text-[40px] leading-[1.12] font-bold text-ink reveal reveal-1">
            Every camera in Gujarat,<br />
            <span className="text-accent">one control room.</span>
          </h1>
          <p className="mt-4 text-[16px] leading-relaxed text-ink2 reveal reveal-2">
            SANGAM brings CCTV from many departments onto one map, reads number plates live and alerts officers the moment a watchlist vehicle is seen.
          </p>
          <div className="mt-10 grid grid-cols-2 gap-3 stagger">
            {FEATURES.map(({ icon: Icon, title, text }) => (
              <div key={title} className="rounded-2xl border border-line bg-white shadow-[var(--shadow-1)] p-4">
                <span className="icon-box icon-box-sm mb-3" style={{ color: 'var(--color-accent2)', background: 'var(--color-accentbg)', borderColor: '#B9CEF7' }}><Icon /></span>
                <div className="text-[14px] font-semibold text-ink">{title}</div>
                <div className="text-[13px] text-ink3 mt-1 leading-snug">{text}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative text-[12px] text-ink3">Gujarat Police Innovation Hackathon 2026 · Team Root Access · Prototype</div>
      </section>

      {/* Right: form */}
      <section className="flex items-center justify-center p-6 md:p-10">
        <div className="w-full max-w-[400px]">
          <div className="lg:hidden flex items-center gap-3 mb-8">
            <SangamMark size={40} />
            <div>
              <div className="text-[18px] font-bold tracking-[.08em] text-ink">SANGAM</div>
              <div className="text-[12px] text-ink3">Vehicle intelligence console</div>
            </div>
          </div>

          <div className="pop-in">
            <h2 className="text-[26px] font-bold text-ink">Sign in</h2>
            <p className="text-[14px] text-ink2 mt-1">For authorised police personnel</p>
          </div>

          {error && (
            <div className="mt-6 rounded-xl border border-[#F5C2BD] bg-critbg px-4 py-3 text-[13px] font-semibold text-crit flex items-start gap-2.5 pop-in">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />{error}
            </div>
          )}

          <form onSubmit={submit} className="mt-6 space-y-4 reveal reveal-1">
            <div>
              <label className="label" htmlFor="u">Username</label>
              <div className="relative">
                <User className="w-4 h-4 text-ink3 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input id="u" required autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} className="field !h-11 pl-10" />
              </div>
            </div>
            <div>
              <label className="label" htmlFor="p">Password</label>
              <div className="relative">
                <KeyRound className="w-4 h-4 text-ink3 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input id="p" required type={show ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className="field !h-11 pl-10 pr-11" />
                <button type="button" onClick={() => setShow((v) => !v)} className="absolute right-2 top-1/2 -translate-y-1/2 btn btn-ghost btn-sm btn-icon" aria-label={show ? 'Hide password' : 'Show password'}>
                  {show ? <EyeOff /> : <Eye />}
                </button>
              </div>
            </div>
            <button type="submit" disabled={isSigningIn} className="btn btn-primary w-full !h-11 !text-[14px]">
              {isSigningIn ? <Loader2 className="animate-spin" /> : null}
              {isSigningIn ? 'Signing in…' : 'Sign in'}
              {!isSigningIn && <ArrowRight />}
            </button>
          </form>

          <div className="mt-8 reveal reveal-2">
            <div className="flex items-center gap-3 mb-3">
              <span className="h-px flex-1 bg-line" />
              <span className="eyebrow">Demo accounts</span>
              <span className="h-px flex-1 bg-line" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <button type="button" onClick={() => demo('admin')} className="group text-left rounded-xl border border-line bg-panel p-3.5 hover:border-accent hover:bg-accentbg transition-colors">
                <ShieldCheck className="w-5 h-5 text-accent2 mb-2" />
                <div className="text-[13px] font-semibold text-ink">Admin</div>
                <div className="text-[12px] text-ink3">Full access</div>
              </button>
              <button type="button" onClick={() => demo('viewer')} className="group text-left rounded-xl border border-line bg-panel p-3.5 hover:border-accent hover:bg-accentbg transition-colors">
                <EyeIcon className="w-5 h-5 text-accent2 mb-2" />
                <div className="text-[13px] font-semibold text-ink">Viewer</div>
                <div className="text-[12px] text-ink3">Read only</div>
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
};
