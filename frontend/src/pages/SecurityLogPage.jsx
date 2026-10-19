import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const ACTION_BADGE_CLASS = {
  LOGIN_SUCCESS: 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400',
  LOGIN_FAILED: 'bg-red-500/10 border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400',
  LOGOUT: 'bg-slate-500/10 border-slate-500/20 dark:border-slate-500/30 text-slate-500 dark:text-slate-400',
  PASSWORD_CHANGED: 'bg-brand-500/10 border-brand-500/20 dark:border-brand-500/30 text-brand-600 dark:text-brand-400',
  PASSWORD_CHANGE_FAILED: 'bg-red-500/10 border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400',
  PASSWORD_RESET_AUTO_APPLIED: 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400',
  PASSWORD_RESET_REQUESTED: 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400',
  PASSWORD_RESET_APPROVED: 'bg-brand-500/10 border-brand-500/20 dark:border-brand-500/30 text-brand-600 dark:text-brand-400',
  PASSWORD_RESET_REJECTED: 'bg-red-500/10 border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400',
  CHAIRMAN_RECOVERY_SCRIPT: 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400',
  USER_CREATED: 'bg-brand-500/10 border-brand-500/20 dark:border-brand-500/30 text-brand-600 dark:text-brand-400',
  USER_PASSWORD_RESET_BY_ADMIN: 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400',
  USER_ACTIVATED: 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400',
  USER_DEACTIVATED: 'bg-red-500/10 border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400',
};
const DEFAULT_BADGE_CLASS = 'bg-slate-500/10 border-slate-500/20 dark:border-slate-500/30 text-slate-500 dark:text-slate-400';

const EVENT_FILTERS = [
  { key: 'all', label: 'All Events' },
  { key: 'signin', label: 'Sign-In Activity', actions: ['LOGIN_SUCCESS', 'LOGIN_FAILED', 'LOGOUT'] },
  { key: 'password', label: 'Password Events', actions: ['PASSWORD_CHANGED', 'PASSWORD_CHANGE_FAILED', 'PASSWORD_RESET_AUTO_APPLIED', 'PASSWORD_RESET_REQUESTED', 'PASSWORD_RESET_APPROVED', 'PASSWORD_RESET_REJECTED', 'CHAIRMAN_RECOVERY_SCRIPT', 'USER_PASSWORD_RESET_BY_ADMIN'] },
  { key: 'accounts', label: 'Account Management', actions: ['USER_CREATED', 'USER_ACTIVATED', 'USER_DEACTIVATED'] },
];

export default function SecurityLogPage() {
  const [data, setData] = useState(null);
  const [search, setSearch] = useState('');
  const [eventFilter, setEventFilter] = useState('all');

  useEffect(() => {
    api.securityLog().then(setData);
  }, []);

  const filtered = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    const activeActions = EVENT_FILTERS.find((f) => f.key === eventFilter)?.actions;
    return data.logs.filter((log) => {
      const matchesSearch = !query || `${log.username} ${log.detail} ${log.ip_address}`.toLowerCase().includes(query);
      const matchesEvent = !activeActions || activeActions.includes(log.action);
      return matchesSearch && matchesEvent;
    });
  }, [data, search, eventFilter]);

  function clearFilters() {
    setSearch('');
    setEventFilter('all');
  }

  const hasActiveFilters = search.trim() !== '' || eventFilter !== 'all';

  if (!data) {
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
        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Chairman Only</p>
              <h1 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Security Log</h1>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {hasActiveFilters ? (
                  <>{filtered.length} of {data.logs.length} events match your filters</>
                ) : (
                  <>Sign-ins, password changes, and account management &middot; most recent {data.logs.length} events</>
                )}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="text"
                placeholder="Search user, detail, IP..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 px-3.5 py-2 text-sm w-full sm:w-56 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              />
              <select
                value={eventFilter}
                onChange={(e) => setEventFilter(e.target.value)}
                className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 px-3.5 py-2.5 text-sm w-full sm:w-52 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              >
                {EVENT_FILTERS.map((f) => (
                  <option key={f.key} value={f.key}>{f.label}</option>
                ))}
              </select>
              {hasActiveFilters && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="text-xs font-semibold text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 px-2.5 py-2 rounded-lg transition-colors whitespace-nowrap"
                >
                  Clear filters
                </button>
              )}
            </div>
          </div>
          <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="sticky top-0 z-[5] bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Time</th>
                  <th className="px-6 py-3 text-left">User</th>
                  <th className="px-6 py-3 text-left">Event</th>
                  <th className="px-6 py-3 text-left">Detail</th>
                  <th className="px-6 py-3 text-left">IP Address</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filtered.map((log, i) => (
                  <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors">
                    <td className="px-6 py-3.5 font-medium tabular-nums text-slate-700 dark:text-slate-300 whitespace-nowrap">{log.timestamp_display}</td>
                    <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{log.username}</td>
                    <td className="px-6 py-3.5">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border whitespace-nowrap ${ACTION_BADGE_CLASS[log.action] || DEFAULT_BADGE_CLASS}`}>
                        {log.action_label}
                      </span>
                    </td>
                    <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400">{log.detail}</td>
                    <td className="px-6 py-3.5 text-slate-400 dark:text-slate-500 tabular-nums">{log.ip_address}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filtered.length === 0 && (
              <div className="text-center py-14">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-8 h-8 text-slate-300 dark:text-slate-600 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                </svg>
                <p className="text-sm font-semibold text-slate-500 dark:text-slate-400">No events match this filter</p>
                <p className="text-xs text-slate-400 dark:text-slate-500 mt-1">Try a different search term</p>
              </div>
            )}
          </div>
        </section>
        <Footer />
      </main>
    </div>
  );
}
