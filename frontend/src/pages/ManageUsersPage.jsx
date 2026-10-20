import { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const EMPTY_FORM = { role: 'CEO', business_unit_id: '', username: '', full_name: '' };

export default function ManageUsersPage() {
  const [users, setUsers] = useState(null);
  const [businessUnits, setBusinessUnits] = useState([]);
  const [search, setSearch] = useState('');
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState(null); // { text, tone: 'success' | 'error', secret?, expiresInHours? }
  const [secretRevealed, setSecretRevealed] = useState(false);
  const [copyState, setCopyState] = useState(null); // null | 'copied' | 'failed'
  const copyTimer = useRef(null);
  const [busyUserId, setBusyUserId] = useState(null);
  const [resets, setResets] = useState(null); // { requests, pending_count }
  const [resetsError, setResetsError] = useState('');
  const [busyResetId, setBusyResetId] = useState(null);

  function showBanner(banner) {
    setSecretRevealed(false);
    setCopyState(null);
    setBanner(banner);
  }

  async function copySecret(secret) {
    clearTimeout(copyTimer.current);
    try {
      await navigator.clipboard.writeText(secret);
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
    copyTimer.current = setTimeout(() => setCopyState(null), 2000);
  }

  function loadUsers() {
    return api.listUsers().then((d) => setUsers(d.users));
  }

  function loadResets() {
    return api
      .listPasswordResets()
      .then((d) => {
        setResets(d);
        setResetsError('');
      })
      .catch((err) => {
        setResetsError(err instanceof ApiError ? err.message : 'Failed to load password requests');
      });
  }

  useEffect(() => {
    loadUsers();
    api.adminOperations().then((d) => {
      setBusinessUnits(d.operations.map((op) => ({ id: op.bu_id, code: op.bu_code, name: op.bu_name })));
    });
  }, []);

  useEffect(() => {
    loadResets();
    return () => clearTimeout(copyTimer.current);
  }, []);

  const pendingResets = useMemo(() => (resets?.requests || []).filter((r) => r.status === 'Pending'), [resets]);
  const resolvedResets = useMemo(() => (resets?.requests || []).filter((r) => r.status !== 'Pending'), [resets]);

  const filtered = useMemo(() => {
    if (!users) return [];
    const query = search.trim().toLowerCase();
    if (!query) return users;
    return users.filter((u) =>
      `${u.username} ${u.full_name || ''} ${u.role} ${u.business_unit_name || ''}`.toLowerCase().includes(query)
    );
  }, [users, search]);

  async function handleCreate(e) {
    e.preventDefault();
    setFormError('');
    if (!form.username.trim() || !form.full_name.trim()) {
      setFormError('Username and full name are required.');
      return;
    }
    if (form.role === 'CEO' && !form.business_unit_id) {
      setFormError('Pick a business unit for this CEO.');
      return;
    }
    setSaving(true);
    try {
      const res = await api.createUser({
        username: form.username.trim(),
        full_name: form.full_name.trim(),
        role: form.role,
        business_unit_id: form.role === 'CEO' ? Number(form.business_unit_id) : null,
      });
      setForm(EMPTY_FORM);
      setShowAddForm(false);
      await loadUsers();
      showBanner({
        text: `Account "${form.username.trim()}" created.`,
        secret: res.temporary_password,
        expiresInHours: res.expires_in_hours,
        tone: 'success',
      });
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Failed to create account');
    } finally {
      setSaving(false);
    }
  }

  async function handleResetPassword(u) {
    setBusyUserId(u.id);
    setBanner(null);
    try {
      const res = await api.resetUserPassword(u.id);
      await loadUsers();
      showBanner({
        text: `${u.username}'s password was reset.`,
        secret: res.temporary_password,
        expiresInHours: res.expires_in_hours,
        tone: 'success',
      });
      loadResets(); // a direct reset may resolve that user's pending request
    } catch (err) {
      showBanner({ text: err instanceof ApiError ? err.message : 'Failed to reset password', tone: 'error' });
    } finally {
      setBusyUserId(null);
    }
  }

  // Reloads the request list and the users table (approval flips "must set password").
  // Never throws, so a refresh failure can't replace a success banner holding a one-time secret.
  function refreshAfterResetAction() {
    return Promise.all([loadResets(), loadUsers().catch(() => {})]);
  }

  async function handleApproveReset(r) {
    setBusyResetId(r.id);
    setBanner(null);
    try {
      const res = await api.approvePasswordReset(r.id);
      showBanner({
        text: `Password reset approved for ${r.username}.`,
        secret: res.temporary_password,
        expiresInHours: res.expires_in_hours,
        tone: 'success',
      });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      showBanner({ text: err instanceof ApiError ? err.message : 'Failed to approve password request', tone: 'error' });
    } finally {
      await refreshAfterResetAction();
      setBusyResetId(null);
    }
  }

  async function handleRejectReset(r) {
    setBusyResetId(r.id);
    setBanner(null);
    try {
      await api.rejectPasswordReset(r.id);
      showBanner({ text: `Password request from ${r.username} was rejected.`, tone: 'success' });
    } catch (err) {
      showBanner({ text: err instanceof ApiError ? err.message : 'Failed to reject password request', tone: 'error' });
    } finally {
      await refreshAfterResetAction();
      setBusyResetId(null);
    }
  }

  async function handleToggleActive(u) {
    setBusyUserId(u.id);
    setBanner(null);
    try {
      await api.setUserActive(u.id, !u.is_active);
      await loadUsers();
      showBanner({ text: `${u.username} was ${u.is_active ? 'deactivated' : 'reactivated'}.`, tone: 'success' });
    } catch (err) {
      showBanner({ text: err instanceof ApiError ? err.message : 'Failed to update account status', tone: 'error' });
    } finally {
      setBusyUserId(null);
    }
  }

  if (!users) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Chairman Only</p>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-xl">User Management</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{users.length} account{users.length !== 1 ? 's' : ''}</p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="relative w-full sm:w-64">
              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M17 10a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search users…"
                className="w-full text-sm pl-9 pr-3 py-2.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              />
            </div>
            <button
              type="button"
              onClick={() => { setShowAddForm((v) => !v); setFormError(''); }}
              className="text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 px-4 py-2.5 rounded-lg shadow-sm shadow-brand-600/30 transition-colors whitespace-nowrap"
            >
              {showAddForm ? 'Cancel' : '+ Add User'}
            </button>
          </div>
        </div>

        {banner && (
          <div
            className={`rounded-xl px-5 py-3.5 text-sm flex items-start justify-between gap-3 ${
              banner.tone === 'success'
                ? 'bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/30 text-emerald-700 dark:text-emerald-400'
                : 'bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 text-red-700 dark:text-red-400'
            }`}
          >
            <div className="min-w-0">
              <span>{banner.text}</span>
              {banner.secret && (
                <div className="mt-2 flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-medium opacity-80">Temporary password:</span>
                  <code className="font-mono text-xs font-bold bg-black/5 dark:bg-white/10 px-2 py-1 rounded-lg tracking-wide">
                    {secretRevealed ? banner.secret : '•'.repeat(banner.secret.length)}
                  </code>
                  <button
                    type="button"
                    onClick={() => setSecretRevealed((v) => !v)}
                    className="text-xs font-semibold underline underline-offset-2 opacity-80 hover:opacity-100"
                  >
                    {secretRevealed ? 'Hide' : 'Show'}
                  </button>
                  <button
                    type="button"
                    onClick={() => copySecret(banner.secret)}
                    className="text-xs font-semibold underline underline-offset-2 opacity-80 hover:opacity-100"
                  >
                    {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy'}
                  </button>
                  {banner.expiresInHours != null && (
                    <span className="text-[11px] font-semibold opacity-80">
                      Expires in {banner.expiresInHours} hour{banner.expiresInHours === 1 ? '' : 's'}.
                    </span>
                  )}
                  <span className="text-[11px] opacity-70">Share this with the account holder now — it won't be shown again after you leave this page.</span>
                </div>
              )}
            </div>
            <button type="button" onClick={() => setBanner(null)} className="opacity-60 hover:opacity-100 flex-shrink-0">
              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
          </div>
        )}

        {showAddForm && (
          <section className={`${GLASS} p-6 fade-in`}>
            <h2 className="font-bold text-slate-900 dark:text-slate-100 text-base mb-1">Add New User</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-5">
              A random temporary password is generated and shown once — share it with the account holder; it expires and must be changed at first sign-in.
            </p>
            <form onSubmit={handleCreate} className="grid sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1.5">Role</label>
                <select
                  value={form.role}
                  onChange={(e) => setForm((f) => ({ ...f, role: e.target.value, business_unit_id: '' }))}
                  className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                >
                  <option value="CEO">CEO</option>
                  <option value="HO">Head Office Staff</option>
                  <option value="Accountant">Accountant</option>
                </select>
              </div>
              {form.role === 'CEO' && (
                <div>
                  <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1.5">Business Unit</label>
                  <select
                    value={form.business_unit_id}
                    onChange={(e) => setForm((f) => ({ ...f, business_unit_id: e.target.value }))}
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                  >
                    <option value="">Select a business unit…</option>
                    {businessUnits.map((bu) => (
                      <option key={bu.id} value={bu.id}>{bu.name}</option>
                    ))}
                  </select>
                </div>
              )}
              <div>
                <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1.5">Username</label>
                <input
                  type="text"
                  value={form.username}
                  onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
                  placeholder="e.g. ceo_bu24"
                  className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1.5">Full Name</label>
                <input
                  type="text"
                  value={form.full_name}
                  onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))}
                  placeholder="e.g. Daw Hla Hla"
                  className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                />
              </div>
              {formError && <p className="sm:col-span-2 text-sm text-red-600 dark:text-red-400">{formError}</p>}
              <div className="sm:col-span-2 flex justify-end">
                <button
                  type="submit"
                  disabled={saving}
                  className="text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 px-5 py-2.5 rounded-lg shadow-sm shadow-brand-600/30 transition-colors disabled:opacity-60"
                >
                  {saving ? 'Creating…' : 'Create Account'}
                </button>
              </div>
            </form>
          </section>
        )}

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 pb-4">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-bold text-slate-900 dark:text-slate-100 text-base">Password Requests</h2>
              {resets && (
                <span
                  className={`text-[11px] font-semibold px-2.5 py-0.5 rounded-full border ${
                    (resets.pending_count ?? pendingResets.length) > 0
                      ? 'text-amber-700 dark:text-amber-400 bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30'
                      : 'text-slate-500 dark:text-slate-400 bg-slate-200/60 dark:bg-slate-700/60 border-slate-300 dark:border-slate-600'
                  }`}
                >
                  {resets.pending_count ?? pendingResets.length} pending
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
              Users who asked for a reset from the sign-in page. Approving issues a random temporary password, shown once.
            </p>
          </div>

          {resetsError && (
            <div className="mx-6 mb-6 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 px-4 py-3 text-sm text-red-700 dark:text-red-400 flex items-center justify-between gap-3 flex-wrap">
              <span>{resetsError}</span>
              <button
                type="button"
                onClick={loadResets}
                className="text-xs font-semibold underline underline-offset-2 opacity-80 hover:opacity-100"
              >
                Retry
              </button>
            </div>
          )}

          {!resetsError && !resets && (
            <p className="px-6 pb-6 text-sm text-slate-500 dark:text-slate-400">Loading requests…</p>
          )}

          {resets && pendingResets.length === 0 && (
            <div className="text-center py-8 border-t border-slate-200/70 dark:border-slate-800">
              <svg xmlns="http://www.w3.org/2000/svg" className="w-7 h-7 text-slate-300 dark:text-slate-600 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p className="text-sm font-semibold text-slate-500 dark:text-slate-400">No pending password requests</p>
            </div>
          )}

          {pendingResets.length > 0 && (
            <div className="overflow-x-auto border-t border-slate-200/70 dark:border-slate-800">
              <table className="w-full min-w-max text-sm">
                <thead className="bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="px-6 py-3 text-left">User</th>
                    <th className="px-6 py-3 text-left">Business Unit</th>
                    <th className="px-6 py-3 text-left">Requested</th>
                    <th className="px-6 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {pendingResets.map((r) => (
                    <tr key={r.id} className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors">
                      <td className="px-6 py-3.5">
                        <p className="font-semibold text-slate-800 dark:text-slate-200">{r.full_name || '—'}</p>
                        <p className="text-xs text-slate-500 dark:text-slate-400">{r.username}</p>
                      </td>
                      <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{r.business_unit_name || '—'}</td>
                      <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400 whitespace-nowrap">{r.requested_display}</td>
                      <td className="px-6 py-3.5">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            disabled={busyResetId !== null}
                            onClick={() => handleApproveReset(r)}
                            className="text-xs font-semibold text-white bg-brand-600 hover:bg-brand-700 px-3 py-1.5 rounded-lg shadow-sm shadow-brand-600/30 transition-colors disabled:opacity-50 whitespace-nowrap"
                          >
                            {busyResetId === r.id ? 'Working…' : 'Approve'}
                          </button>
                          <button
                            type="button"
                            disabled={busyResetId !== null}
                            onClick={() => handleRejectReset(r)}
                            className="text-xs font-semibold text-red-700 dark:text-red-400 bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 dark:border-red-500/30 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 whitespace-nowrap"
                          >
                            Reject
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {resolvedResets.length > 0 && (
            <details className="border-t border-slate-200/70 dark:border-slate-800">
              <summary className="px-6 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 cursor-pointer select-none">
                Recently resolved ({resolvedResets.length})
              </summary>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {resolvedResets.map((r) => (
                  <li key={r.id} className="px-6 py-2.5 flex items-center justify-between gap-3 flex-wrap text-xs">
                    <div className="min-w-0">
                      <span className="font-semibold text-slate-700 dark:text-slate-300">{r.full_name || r.username}</span>
                      <span className="ml-2 text-slate-500 dark:text-slate-400">{r.username}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-slate-400 dark:text-slate-500">{r.requested_display}</span>
                      <span
                        className={`text-[11px] font-semibold px-2.5 py-0.5 rounded-full border ${
                          r.status === 'Approved'
                            ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30'
                            : 'text-red-600 dark:text-red-400 bg-red-500/10 border-red-500/20 dark:border-red-500/30'
                        }`}
                      >
                        {r.status}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <p className="lg:hidden px-6 pt-4 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">User</th>
                  <th className="px-6 py-3 text-left">Role</th>
                  <th className="px-6 py-3 text-left">Business Unit</th>
                  <th className="px-6 py-3 text-center">Status</th>
                  <th className="px-6 py-3 text-left">Last Login</th>
                  <th className="px-6 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filtered.map((u) => (
                  <tr key={u.id} className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors">
                    <td className="px-6 py-3.5">
                      <p className="font-semibold text-slate-800 dark:text-slate-200">{u.full_name || '—'}</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">{u.username}</p>
                    </td>
                    <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{u.role === 'HO' ? 'Head Office' : u.role}</td>
                    <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{u.business_unit_name || '—'}</td>
                    <td className="px-6 py-3.5 text-center">
                      <div className="flex flex-col items-center gap-1">
                        {u.is_active ? (
                          <span className="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 px-2.5 py-0.5 rounded-full">Active</span>
                        ) : (
                          <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 bg-slate-200/60 dark:bg-slate-700/60 border border-slate-300 dark:border-slate-600 px-2.5 py-0.5 rounded-full">Inactive</span>
                        )}
                        {u.require_password_change && (
                          <span className="text-[10px] text-amber-600 dark:text-amber-400">Must set password</span>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400">{u.last_login_display || 'Never'}</td>
                    <td className="px-6 py-3.5">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          disabled={busyUserId === u.id}
                          onClick={() => handleResetPassword(u)}
                          className="text-xs font-semibold text-amber-700 dark:text-amber-400 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/20 dark:border-amber-500/30 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 whitespace-nowrap"
                        >
                          Reset Password
                        </button>
                        <button
                          type="button"
                          disabled={busyUserId === u.id}
                          onClick={() => handleToggleActive(u)}
                          className={`text-xs font-semibold px-3 py-1.5 rounded-lg border transition-colors disabled:opacity-50 whitespace-nowrap ${
                            u.is_active
                              ? 'text-red-700 dark:text-red-400 bg-red-500/10 hover:bg-red-500/20 border-red-500/20 dark:border-red-500/30'
                              : 'text-emerald-700 dark:text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border-emerald-500/20 dark:border-emerald-500/30'
                          }`}
                        >
                          {u.is_active ? 'Deactivate' : 'Reactivate'}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {filtered.length === 0 && <p className="text-center text-sm text-slate-500 dark:text-slate-400 py-14">No matching user found.</p>}
        </section>
        <Footer />
      </main>
    </div>
  );
}
