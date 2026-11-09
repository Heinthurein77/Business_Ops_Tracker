import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';

// SM-6: after this many consecutive auth failures the 60 s interval stops
// — an expired session must not keep polling (and showing a stale badge)
// forever.
const MAX_AUTH_FAILURES = 3;

export default function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  // null = ok; 'unavailable' = refresh failed (distinct from a stale
  // count — the badge is hidden and the panel says so); 'expired' = the
  // session is gone, re-login is the fix.
  const [status, setStatus] = useState(null);
  const [actionError, setActionError] = useState('');
  const authFailures = useRef(0);

  const refresh = async () => {
    try {
      const d = await api.listNotifications();
      setUnread(d.unread_count || 0);
      setItems(d.items || []);
      setStatus(null);
      authFailures.current = 0;
      return true;
    } catch (err) {
      // SM-6: never present a stale count as current. An expired session
      // leaves a badge that is hours old (or "0" while HO sent something
      // urgent); show the unavailable state instead.
      if (err instanceof ApiError && err.status === 401) {
        authFailures.current += 1;
        setStatus('expired');
        setUnread(0);
      } else {
        setStatus('unavailable');
      }
      return false;
    }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(async () => {
      // Stop polling after repeated auth failures — the session is gone
      // and the global 401 handler is already sending the user to login.
      if (authFailures.current >= MAX_AUTH_FAILURES) {
        clearInterval(t);
        return;
      }
      await refresh();
    }, 60000);
    return () => clearInterval(t);
  }, []);

  async function openPanel() {
    setOpen(true);
    setActionError('');
    await refresh();
  }

  async function markRead(n) {
    setActionError('');
    try {
      await api.markNotificationRead(n.id);
      await refresh();
    } catch (err) {
      // SM-6: a failed mark-read used to look identical to a successful
      // one (error swallowed, then a refresh that also swallowed). Surface it.
      setActionError(err instanceof ApiError ? err.message : 'Could not mark as read — please try again');
    }
  }

  async function markAll() {
    setActionError('');
    try {
      await api.markAllNotificationsRead();
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not mark all as read — please try again');
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : openPanel())}
        className="w-9 h-9 flex items-center justify-center rounded-lg bg-white/10 text-slate-200 hover:bg-white/15 hover:text-white transition-colors relative"
        aria-label="Notifications"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
        </svg>
        {status === null && unread > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
        {status !== null && (
          <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-amber-500 text-white text-[10px] font-bold flex items-center justify-center" title="Notifications unavailable">
            !
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-2 w-80 max-h-96 overflow-y-auto z-50 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-2xl shadow-slate-900/10 p-2">
            <div className="flex items-center justify-between px-3 py-2">
              <p className="text-sm font-bold text-slate-900 dark:text-slate-100">Notifications</p>
              {status === null && unread > 0 && <button onClick={markAll} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Mark all read</button>}
            </div>
            {status === 'expired' && (
              <p className="px-3 py-4 text-center text-xs font-medium text-amber-700 dark:text-amber-400">
                Notifications unavailable — your session expired. Please sign in again.
              </p>
            )}
            {status === 'unavailable' && (
              <p className="px-3 py-4 text-center text-xs font-medium text-amber-700 dark:text-amber-400">
                Notifications unavailable — could not load. Check your connection and try again.
              </p>
            )}
            {actionError && <p className="px-3 py-2 text-xs font-medium text-red-600 dark:text-red-400">{actionError}</p>}
            {status === null && items.length === 0 && <p className="px-3 py-6 text-center text-xs text-slate-400">No notifications</p>}
            {status === null && items.map((n) => (
              <button key={n.id} onClick={() => markRead(n)} className={`w-full text-left px-3 py-2.5 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors ${n.is_read ? 'opacity-60' : ''}`}>
                <p className="text-[13px] font-semibold text-slate-800 dark:text-slate-200">{n.title}</p>
                {n.message && <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{n.message}</p>}
                <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">{n.created_at ? new Date(n.created_at).toLocaleString() : ''}</p>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
