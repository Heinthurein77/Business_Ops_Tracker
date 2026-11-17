import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { QRCodeCanvas } from 'qrcode.react';
import { useAuth, roleHomePath } from '../context/AuthContext';
import { api, ApiError } from '../api/client';

// M-01: two-factor authentication setup for a signed-in user. The
// Chairman is forced through this flow at sign-in instead (see
// LoginPage), but any account may enroll here optionally, and may remove
// its own enrollment by proving possession with a current code.
export default function TotpSetupPage() {
  const { user, refresh, logout } = useAuth();
  const navigate = useNavigate();
  const [enrollData, setEnrollData] = useState(null);
  const [enrollCode, setEnrollCode] = useState('');
  const [disableCode, setDisableCode] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [busy, setBusy] = useState(false);
  const [secretCopied, setSecretCopied] = useState(false);

  async function startEnrollment() {
    setError('');
    setBusy(true);
    try {
      setEnrollData(await api.totpEnroll());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start two-factor setup.');
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      await api.totpConfirm(enrollCode);
      setEnrollData(null);
      setEnrollCode('');
      await refresh();
      setInfo('Two-factor authentication is enabled for your account. You will be asked for a code at every sign-in.');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  async function handleDisable(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      await api.totpDisable(disableCode);
      // Disabling revokes every session, including this one — sign out
      // rather than leave a dead session behind.
      await logout();
      navigate('/login');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  async function copySecret() {
    try {
      await navigator.clipboard.writeText(enrollData.secret);
      setSecretCopied(true);
      setTimeout(() => setSecretCopied(false), 2000);
    } catch { /* clipboard unavailable — the secret is still shown */ }
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 antialiased flex items-center justify-center p-6">
      <div className="w-full max-w-md fade-in">
        <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-brand-500">abcMIB &middot; Business Ops Tracker</p>
        <h2 className="mt-2 text-[28px] font-bold tracking-tight text-white">Two-factor authentication</h2>
        <p className="mt-2 text-sm text-slate-400">
          {user?.role === 'Chairman'
            ? 'Your account requires an authenticator app at every sign-in.'
            : 'Add an authenticator app as a second step at sign-in. Optional for your account — required for the Chairman.'}
        </p>

        {error && (
          <div className="mt-5 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
        )}
        {info && (
          <div className="mt-5 rounded-lg bg-emerald-50 border border-emerald-200 px-4 py-3 text-sm text-emerald-700">{info}</div>
        )}

        {user?.totp_enabled ? (
          <div className="mt-8 space-y-5">
            <p className="text-sm text-slate-400">
              Two-factor authentication is <span className="font-semibold text-emerald-400">enabled</span> for your
              account. To remove it, enter a current code from your authenticator app — you will be signed out
              afterwards, and the Chairman must set it up again before the next sign-in.
            </p>
            <form onSubmit={handleDisable} className="space-y-5">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1.5">Authenticator code</label>
                <input
                  type="text"
                  required
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  maxLength={10}
                  placeholder="6-digit code"
                  value={disableCode}
                  onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  className="w-full rounded-lg border border-slate-700 bg-slate-900 text-white px-4 py-2.5 text-sm tracking-[0.3em] shadow-sm focus:outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-500/15 placeholder:text-slate-500 placeholder:tracking-normal transition"
                />
              </div>
              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-lg bg-red-600 hover:bg-red-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-red-600/30 transition-all active:scale-[0.99] disabled:opacity-60"
              >
                {busy ? 'Removing…' : 'Remove Two-Factor Authentication'}
              </button>
            </form>
          </div>
        ) : enrollData ? (
          <div className="mt-8 space-y-5">
            <div className="flex justify-center rounded-lg bg-white p-4">
              <QRCodeCanvas value={enrollData.otpauth_url} size={192} />
            </div>
            <p className="text-sm text-slate-400">
              Scan this code with your authenticator app. If you cannot scan it, enter this secret manually instead:
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white">{enrollData.secret}</code>
              <button
                type="button"
                onClick={copySecret}
                className="shrink-0 rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-300"
              >
                {secretCopied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <form onSubmit={handleConfirm} className="space-y-5">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1.5">Authenticator code</label>
                <input
                  type="text"
                  required
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  maxLength={10}
                  placeholder="6-digit code"
                  value={enrollCode}
                  onChange={(e) => setEnrollCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  className="w-full rounded-lg border border-slate-700 bg-slate-900 text-white px-4 py-2.5 text-sm tracking-[0.3em] shadow-sm focus:outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-500/15 placeholder:text-slate-500 placeholder:tracking-normal transition"
                />
              </div>
              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all active:scale-[0.99] disabled:opacity-60"
              >
                {busy ? 'Confirming…' : 'Confirm and Enable'}
              </button>
            </form>
          </div>
        ) : (
          <div className="mt-8 space-y-5">
            <p className="text-sm text-slate-400">
              Two-factor authentication is <span className="font-semibold text-slate-200">not enabled</span> for your
              account. Setting it up takes a minute: scan a code with your authenticator app and confirm with a code.
            </p>
            <button
              type="button"
              onClick={startEnrollment}
              disabled={busy}
              className="w-full rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all active:scale-[0.99] disabled:opacity-60"
            >
              {busy ? 'Starting…' : 'Set Up Two-Factor Authentication'}
            </button>
          </div>
        )}

        <button
          type="button"
          onClick={() => navigate(user ? roleHomePath(user.role) : '/login')}
          className="mt-6 w-full text-center text-sm font-medium text-brand-500 hover:text-brand-400"
        >
          Back to dashboard
        </button>
      </div>
    </div>
  );
}
