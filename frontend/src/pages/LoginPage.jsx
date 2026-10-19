import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
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

  async function handleLogin(e) {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const data = await login(username, password);
      navigate(data.user.require_password_change ? '/force-password-change' : roleHomePath(data.user.role));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setSubmitting(false);
    }
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
                <h2 className="text-2xl font-bold tracking-tight lg:text-slate-900 text-white">Sign in to your workspace</h2>
                <p className="mt-2 text-sm lg:text-slate-500 text-slate-400">Enter your credentials to access your dashboard.</p>

                {error && (
                  <div className="mt-5 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
                )}
                {info && (
                  <div className="mt-5 rounded-lg bg-emerald-50 border border-emerald-200 px-4 py-3 text-sm text-emerald-700">{info}</div>
                )}

                <form onSubmit={handleLogin} className="mt-8 space-y-5">
                  <div>
                    <label className="block text-sm font-medium lg:text-slate-700 text-slate-300 mb-1.5">Username</label>
                    <input
                      type="text"
                      required
                      autoComplete="username"
                      maxLength={80}
                      placeholder="Your username"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      className="w-full rounded-lg border lg:border-slate-300 border-slate-700 lg:bg-white bg-slate-900 lg:text-slate-900 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium lg:text-slate-700 text-slate-300 mb-1.5">Password</label>
                    <input
                      type="password"
                      required
                      autoComplete="current-password"
                      maxLength={128}
                      placeholder="••••••••"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="w-full rounded-lg border lg:border-slate-300 border-slate-700 lg:bg-white bg-slate-900 lg:text-slate-900 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-500"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="w-full rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all disabled:opacity-60"
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
                    <label className="block text-sm font-medium lg:text-slate-700 text-slate-300 mb-1.5">Username</label>
                    <input
                      type="text"
                      required
                      autoComplete="username"
                      maxLength={80}
                      placeholder="Your username"
                      value={forgotUsername}
                      onChange={(e) => setForgotUsername(e.target.value)}
                      className="w-full rounded-lg border lg:border-slate-300 border-slate-700 lg:bg-white bg-slate-900 lg:text-slate-900 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-500"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="w-full rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all disabled:opacity-60"
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
