import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth, resolveHomePath } from '../context/AuthContext';
import { ApiError, api } from '../api/client';

// Mirrors the server rules: 12-72 UTF-8 bytes. The server stays authoritative
// (denylist, username check, etc.) and its `detail` is shown in the error box.
const MIN_LENGTH = 12;
const MAX_BYTES = 72;

export default function ForcePasswordChangePage() {
  const { user, refresh } = useAuth();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const newPasswordBytes = new TextEncoder().encode(newPassword).length;
  const lengthOk = newPassword.length >= MIN_LENGTH;

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    if (!currentPassword) {
      setError('Enter your current password.');
      return;
    }
    if (newPassword.length < MIN_LENGTH) {
      setError(`Password must be at least ${MIN_LENGTH} characters.`);
      return;
    }
    if (newPasswordBytes > MAX_BYTES) {
      setError(`Password is too long (maximum ${MAX_BYTES} bytes; some characters use more than one).`);
      return;
    }
    if (newPassword === currentPassword) {
      setError('Your new password must be different from your current password.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    setSubmitting(true);
    try {
      await api.forcePasswordChange(currentPassword, newPassword, confirmPassword);
      const refreshedUser = await refresh();
      navigate(resolveHomePath(refreshedUser));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-6">
      <div className="w-full max-w-md">
        <div className="bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl p-8">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            </div>
            <div>
              <h1 className="text-white font-bold text-lg tracking-tight">Password Rotation Required</h1>
              <p className="text-slate-500 text-xs">
                Signed in as <span className="text-slate-300 font-medium">{user?.username}</span>
              </p>
            </div>
          </div>

          <p className="text-sm text-slate-400 leading-relaxed mb-6">
            For security, you must set a new personal password before continuing to your dashboard. This is required
            on first login and after any password reset. Choose a password of at least {MIN_LENGTH} characters that
            is different from your current one, is not a commonly used password, and does not contain your username.
          </p>

          {error && <div className="mb-5 rounded-lg bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">{error}</div>}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Current Password</label>
              <input
                type="password"
                required
                autoComplete="current-password"
                maxLength={128}
                placeholder="Your current password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-600"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">New Password</label>
              <input
                type="password"
                required
                minLength={MIN_LENGTH}
                maxLength={MAX_BYTES}
                autoComplete="new-password"
                placeholder="At least 12 characters"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-600"
              />
              <div className="mt-1.5 flex items-center justify-between text-[11px]">
                <span className={lengthOk ? 'text-emerald-400' : 'text-slate-500'}>
                  {lengthOk ? '12+ characters ✓' : `${newPassword.length}/${MIN_LENGTH} characters minimum`}
                </span>
                <span className={`tabular-nums ${newPasswordBytes > MAX_BYTES ? 'text-amber-400' : 'text-slate-600'}`}>
                  {newPasswordBytes}/{MAX_BYTES}
                </span>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Confirm New Password</label>
              <input
                type="password"
                required
                minLength={MIN_LENGTH}
                maxLength={MAX_BYTES}
                autoComplete="new-password"
                placeholder="Re-enter password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-600"
              />
            </div>
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all disabled:opacity-60"
            >
              {submitting ? 'Updating…' : 'Update Password & Continue'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
