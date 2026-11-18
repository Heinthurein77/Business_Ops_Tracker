import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { QRCodeCanvas } from 'qrcode.react';
import { useAuth, roleHomePath } from '../context/AuthContext';
import { api, ApiError } from '../api/client';

const APP_NAME = 'Business Operations & CEO Tracking System';

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [showForgot, setShowForgot] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [forgotUsername, setForgotUsername] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [submitting, setSubmitting] = useState(false);
  // M-01: second factor. needsTotp — the account has TOTP enabled and the
  // sign-in needs its code. enrollToken/enrollData — a Chairman signing in
  // with no enrolled TOTP is forced through setup before any session.
  const [totpCode, setTotpCode] = useState('');
  const [needsTotp, setNeedsTotp] = useState(false);
  const [enrollToken, setEnrollToken] = useState('');
  const [enrollData, setEnrollData] = useState(null);
  const [enrollCode, setEnrollCode] = useState('');
  const [secretCopied, setSecretCopied] = useState(false);

  // SM-1: the global 401 handler sets this flag when a session expires
  // (or is revoked on another device) mid-use. Say so plainly, and clear
  // the flag once shown. Unsaved form drafts are restored after sign-in.
  useEffect(() => {
    try {
      if (sessionStorage.getItem('bot-session-expired')) {
        setInfo('Your session expired — please sign in again. Any unsaved form will be restored from your draft.');
        sessionStorage.removeItem('bot-session-expired');
      }
    } catch { /* storage unavailable — plain login */ }
  }, []);

  async function handleLogin(e) {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const data = await login(username, password, totpCode || undefined);
      navigate(data.user.require_password_change ? '/force-password-change' : roleHomePath(data.user.role));
    } catch (err) {
      if (err instanceof ApiError && err.message === 'TOTP_REQUIRED') {
        // The password was right — the account has TOTP enabled, so ask
        // for its code and retry with it.
        setNeedsTotp(true);
        setError('Enter the 6-digit code from your authenticator app.');
      } else if (err instanceof ApiError && err.message === 'TOTP_ENROLLMENT_REQUIRED') {
        // Chairman with no enrolled TOTP: start setup with the token the
        // login attempt returned (no session exists yet).
        await startEnrollment(err.data?.totp_setup_token);
      } else {
        setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function startEnrollment(setupToken) {
    if (!setupToken) {
      setError('Two-factor setup is required, but no setup token was returned. Please try signing in again.');
      return;
    }
    setEnrollToken(setupToken);
    setError('');
    setSubmitting(true);
    try {
      const data = await api.totpEnroll(setupToken);
      setEnrollData(data);
      setInfo('Two-factor authentication is required for the Chairman account. Scan the code with your authenticator app, then confirm with a code.');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start two-factor setup. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleEnrollConfirm(e) {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await api.totpConfirm(enrollCode, enrollToken);
      setEnrollData(null);
      setEnrollToken('');
      setEnrollCode('');
      setNeedsTotp(false);
      setTotpCode('');
      setInfo('Two-factor authentication is set up — sign in again with your password and a code from your authenticator app.');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  async function copySecret() {
    try {
      await navigator.clipboard.writeText(enrollData.secret);
      setSecretCopied(true);
      setTimeout(() => setSecretCopied(false), 2000);
    } catch { /* clipboard unavailable — the secret is still shown */ }
  }

  async function handleForgot(e) {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const data = await api.forgotPassword(forgotUsername.trim());
      setInfo(
        data?.message ||
          'If this account exists, a reset request has been sent to the Chairman. You will receive a temporary password once it is approved.'
      );
      setForgotUsername('');
      setShowForgot(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 antialiased">
      <div className="min-h-screen grid lg:grid-cols-2">
        <div className="relative hidden lg:flex flex-col justify-between overflow-hidden bg-gradient-to-br from-slate-950 via-slate-900 to-brand-900 p-14">
          <div className="absolute -top-24 -left-24 w-96 h-96 bg-brand-600 rounded-full blur-[90px] opacity-45" />
          <div className="absolute bottom-0 right-0 w-[28rem] h-[28rem] bg-indigo-500 rounded-full blur-[90px] opacity-45" />

          <div className="relative z-10 flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-brand-500 to-indigo-400 flex items-center justify-center shadow-lg shadow-brand-500/30">
              <svg xmlns="http://www.w3.org/2000/svg" className="w-6 h-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.5V21h18v-7.5M3 13.5l6-6 4 4 7-7M3 13.5V9m18-2.5V6" />
              </svg>
            </div>
            <span className="text-lg font-bold tracking-tight text-white">Business Ops Command</span>
          </div>

          <div className="relative z-10 max-w-md">
            <h1 className="text-4xl font-extrabold leading-tight text-white tracking-tight">
              Executive-grade visibility across every business unit.
            </h1>
            <p className="mt-5 text-slate-400 text-base leading-relaxed">
              Daily financial performance, CEO activity, and executive well-being &mdash; consolidated in real time
              across 23 business units for Chairman-level decision making.
            </p>
            <div className="mt-10 flex items-center gap-8">
              <div>
                <p className="text-3xl font-bold text-white">23</p>
                <p className="text-xs uppercase tracking-widest text-slate-500 mt-1">Business Units</p>
              </div>
              <div className="w-px h-10 bg-slate-700" />
              <div>
                <p className="text-3xl font-bold text-white">24/7</p>
                <p className="text-xs uppercase tracking-widest text-slate-500 mt-1">Live Monitoring</p>
              </div>
              <div className="w-px h-10 bg-slate-700" />
              <div>
                <p className="text-3xl font-bold text-white">100%</p>
                <p className="text-xs uppercase tracking-widest text-slate-500 mt-1">Secure Access</p>
              </div>
            </div>
          </div>

          <p className="relative z-10 text-xs text-slate-600">&copy; {APP_NAME}. Internal use only.</p>
        </div>

        <div className="flex items-center justify-center p-6 sm:p-10 bg-slate-950 lg:bg-white lg:text-slate-900">
          <div className="w-full max-w-sm fade-in">
            <div className="lg:hidden flex items-center gap-3 mb-10 justify-center">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-brand-500 to-indigo-400 flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.5V21h18v-7.5M3 13.5l6-6 4 4 7-7M3 13.5V9m18-2.5V6" />
                </svg>
              </div>
              <span className="text-lg font-bold text-white">Business Ops Command</span>
            </div>

            {!showForgot ? (
              <div>
                <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-brand-500">abcMIB &middot; Business Ops Tracker</p>
                <h2 className="mt-2 text-[28px] font-bold tracking-tight lg:text-slate-900 text-white">Sign in to your workspace</h2>
                <p className="mt-2 text-sm lg:text-slate-500 text-slate-400">Enter your credentials to access your dashboard.</p>

                {error && (
                  <div className="mt-5 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
                )}
                {info && (
                  <div className="mt-5 rounded-lg bg-emerald-50 border border-emerald-200 px-4 py-3 text-sm text-emerald-700">{info}</div>
                )}

                {/* M-01: forced TOTP enrollment — shown instead of the
                    sign-in form once setup has started. */}
                {enrollData && (
                  <div className="mt-8 space-y-5">
                    <div className="flex justify-center rounded-lg bg-white p-4">
                      <QRCodeCanvas value={enrollData.otpauth_url} size={192} />
                    </div>
                    <p className="text-sm lg:text-slate-500 text-slate-400">
                      Scan this code with your authenticator app (Google Authenticator, Microsoft Authenticator, or
                      similar). If you cannot scan it, enter this secret manually instead:
                    </p>
                    <div className="flex items-center gap-2">
                      <code className="flex-1 break-all rounded-lg border lg:border-slate-300 border-slate-700 lg:bg-slate-50 bg-slate-900 px-3 py-2 text-sm lg:text-slate-900 text-white">{enrollData.secret}</code>
                      <button
                        type="button"
                        onClick={copySecret}
                        className="shrink-0 rounded-lg border lg:border-slate-300 border-slate-700 px-3 py-2 text-xs font-semibold lg:text-slate-700 text-slate-300"
                      >
                        {secretCopied ? 'Copied' : 'Copy'}
                      </button>
                    </div>
                    <form onSubmit={handleEnrollConfirm} className="space-y-5">
                      <div>
                        <label className="block text-sm font-medium lg:text-slate-700 text-slate-300 mb-1.5" htmlFor="login-enroll-code">Authenticator code</label>
                        <input id="login-enroll-code"
                          type="text"
                          required
                          autoComplete="one-time-code"
                          inputMode="numeric"
                          maxLength={10}
                          placeholder="6-digit code"
                          value={enrollCode}
                          onChange={(e) => setEnrollCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                          className="w-full rounded-lg border lg:border-slate-300 border-slate-700 lg:bg-white bg-slate-900 lg:text-slate-900 text-white px-4 py-2.5 text-sm tracking-[0.3em] shadow-sm focus:outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-500/15 placeholder:text-slate-500 placeholder:tracking-normal transition"
                        />
                      </div>
                      <button
                        type="submit"
                        disabled={submitting}
                        className="w-full rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all active:scale-[0.99] disabled:opacity-60"
                      >
                        {submitting ? 'Confirming…' : 'Confirm and Enable'}
                      </button>
                    </form>
                  </div>
                )}

                {!enrollData && (
                <>
                <form onSubmit={handleLogin} className="mt-8 space-y-5">
                  <div>
                    <label className="block text-sm font-medium lg:text-slate-700 text-slate-300 mb-1.5" htmlFor="login-username">Username</label>
                    <input id="login-username"
                      type="text"
                      required
                      autoComplete="username"
                      maxLength={80}
                      placeholder="Your username"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      className="w-full rounded-lg border lg:border-slate-300 border-slate-700 lg:bg-white bg-slate-900 lg:text-slate-900 text-white px-4 py-2.5 text-sm shadow-sm focus:outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-500/15 placeholder:text-slate-500 transition"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium lg:text-slate-700 text-slate-300 mb-1.5" htmlFor="login-password">Password</label>
                    <input id="login-password"
                      type="password"
                      required
                      autoComplete="current-password"
                      maxLength={128}
                      placeholder="••••••••"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="w-full rounded-lg border lg:border-slate-300 border-slate-700 lg:bg-white bg-slate-900 lg:text-slate-900 text-white px-4 py-2.5 text-sm shadow-sm focus:outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-500/15 placeholder:text-slate-500 transition"
                    />
                  </div>
                  {needsTotp && (
                    <div>
                      <label className="block text-sm font-medium lg:text-slate-700 text-slate-300 mb-1.5" htmlFor="login-totp">Authenticator code</label>
                      <input id="login-totp"
                        type="text"
                        required
                        autoComplete="one-time-code"
                        inputMode="numeric"
                        maxLength={10}
                        placeholder="6-digit code"
                        value={totpCode}
                        onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                        className="w-full rounded-lg border lg:border-slate-300 border-slate-700 lg:bg-white bg-slate-900 lg:text-slate-900 text-white px-4 py-2.5 text-sm tracking-[0.3em] shadow-sm focus:outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-500/15 placeholder:text-slate-500 placeholder:tracking-normal transition"
                      />
                      <p className="mt-1.5 text-xs lg:text-slate-500 text-slate-400">
                        Open your authenticator app and enter the current 6-digit code for this account.
                      </p>
                    </div>
                  )}
                  <button
                    type="submit"
                    disabled={submitting}
                    className="w-full rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all active:scale-[0.99] disabled:opacity-60"
                  >
                    {submitting ? 'Signing In…' : 'Sign In Securely'}
                  </button>
                </form>

                <button
                  type="button"
                  onClick={() => {
                    setShowForgot(true);
                    setError('');
                    setInfo('');
                  }}
                  className="mt-5 w-full text-center text-sm font-medium text-brand-500 hover:text-brand-400"
                >
                  Forgot Password?
                </button>
                </>
                )}

                {/* SM-1: document the all-devices logout — signing out, or
                    changing a password, ends the session on every device
                    at once (there is no per-device session list). */}
                <p className="mt-6 text-center text-xs lg:text-slate-400 text-slate-500">
                  Sessions last 8 hours. Signing out signs you out on all devices.
                </p>
              </div>
            ) : (
              <div>
                <h2 className="text-2xl font-bold tracking-tight lg:text-slate-900 text-white">Request password reset</h2>
                <p className="mt-2 text-sm lg:text-slate-500 text-slate-400">
                  Submit your username to send a reset request to the Chairman. Once it is approved you will be issued a
                  temporary password, and you'll be asked to set a new one the first time you sign in.
                </p>

                {error && (
                  <div className="mt-5 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
                )}

                <form onSubmit={handleForgot} className="mt-8 space-y-5">
                  <div>
                    <label className="block text-sm font-medium lg:text-slate-700 text-slate-300 mb-1.5" htmlFor="login-forgot-username">Username</label>
                    <input id="login-forgot-username"
                      type="text"
                      required
                      autoComplete="username"
                      maxLength={80}
                      placeholder="Your username"
                      value={forgotUsername}
                      onChange={(e) => setForgotUsername(e.target.value)}
                      className="w-full rounded-lg border lg:border-slate-300 border-slate-700 lg:bg-white bg-slate-900 lg:text-slate-900 text-white px-4 py-2.5 text-sm shadow-sm focus:outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-500/15 placeholder:text-slate-500 transition"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="w-full rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all active:scale-[0.99] disabled:opacity-60"
                  >
                    {submitting ? 'Sending…' : 'Submit Request'}
                  </button>
                </form>

                <button
                  type="button"
                  onClick={() => setShowForgot(false)}
                  className="mt-5 w-full text-center text-sm font-medium text-brand-500 hover:text-brand-400"
                >
                  Back to sign in
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
