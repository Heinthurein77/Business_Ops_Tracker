import { useEffect, useState } from 'react';
import { api } from '../api/client';

export default function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);

  const refresh = async () => {
    try {
      const d = await api.listNotifications();
      setUnread(d.unread_count || 0);
      setItems(d.items || []);
    } catch { /* silent */ }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60000);
    return () => clearInterval(t);
  }, []);

  async function openPanel() {
    setOpen(true);
    await refresh();
  }

  async function markRead(n) {
    await api.markNotificationRead(n.id);
    refresh();
  }

  async function markAll() {
    await api.markAllNotificationsRead();
    refresh();
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : openPanel())}
        className="w-9 h-9 flex items-center justify-center rounded-lg bg-slate-900/5 dark:bg-white/5 text-slate-600 dark:text-slate-300 hover:bg-slate-900/10 dark:hover:bg-white/10 transition-colors relative"
        aria-label="Notifications"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
        </svg>
        {unread > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-2 w-80 max-h-96 overflow-y-auto z-50 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-2xl shadow-slate-900/10 p-2">
            <div className="flex items-center justify-between px-3 py-2">
              <p className="text-sm font-bold text-slate-900 dark:text-slate-100">Notifications</p>
              {unread > 0 && <button onClick={markAll} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Mark all read</button>}
            </div>
            {items.length === 0 && <p className="px-3 py-6 text-center text-xs text-slate-400">No notifications</p>}
            {items.map((n) => (
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
