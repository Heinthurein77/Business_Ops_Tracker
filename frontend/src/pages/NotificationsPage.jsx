import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

// PM-21: the notification archive page. The bell used to be the only
// surface and list_notifications was capped at the latest 100, so older
// alerts became unreachable once 100 newer ones arrived. This page is
// paginated (limit/offset) and linked from the bell ("View all").
//
// NOTE: the /notifications route still needs registering in App.jsx
// (one Route, any role — the endpoint is per-user); see the PM-B report.
const PAGE_SIZE = 50;

export default function NotificationsPage() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (off) => {
    setLoading(true);
    try {
      const d = await api.listNotifications(false, PAGE_SIZE, off);
      setData(d);
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load notifications');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(offset); }, [load, offset]);

  async function openNotification(n) {
    try {
      await api.markNotificationRead(n.id);
    } catch { /* opening still works even if mark-read fails */ }
    if (typeof n.link === 'string' && n.link.startsWith('/')) navigate(n.link);
    else load(offset);
  }

  const total = data?.total ?? 0;
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />
      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-xl tracking-tight">Notifications</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              {data ? `${data.unread_count} unread · ${total} total` : 'Your alerts, newest first'}
            </p>
          </div>
          <button
            type="button"
            onClick={async () => { await api.markAllNotificationsRead(); load(offset); }}
            className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline"
          >
            Mark all read
          </button>
        </header>

        {error && <div className="text-sm font-medium text-red-700 dark:text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2.5">{error}</div>}

        <section className={`${GLASS} overflow-hidden`}>
          {loading && !data && <p className="px-6 py-8 text-center text-sm text-slate-400">Loading…</p>}
          {data && data.items.length === 0 && <p className="px-6 py-8 text-center text-sm text-slate-400">No notifications</p>}
          {data && data.items.length > 0 && (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.items.map((n) => (
                <li key={n.id}>
                  <button type="button" onClick={() => openNotification(n)} className={`w-full text-left px-6 py-3.5 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors ${n.is_read ? 'opacity-60' : ''}`}>
                    <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{n.title}</p>
                    {n.message && <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{n.message}</p>}
                    <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-1">{n.created_at ? new Date(n.created_at).toLocaleString() : ''}</p>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {data && total > PAGE_SIZE && (
            <div className="flex items-center justify-between px-6 py-3 border-t border-slate-200 dark:border-slate-800">
              <button type="button" disabled={!hasPrev} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} className="text-xs font-semibold text-slate-600 dark:text-slate-300 disabled:opacity-40">← Previous</button>
              <span className="text-xs text-slate-400">Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
              <button type="button" disabled={!hasNext} onClick={() => setOffset(offset + PAGE_SIZE)} className="text-xs font-semibold text-slate-600 dark:text-slate-300 disabled:opacity-40">Next →</button>
            </div>
          )}
        </section>
        <Footer />
      </main>
    </div>
  );
}
