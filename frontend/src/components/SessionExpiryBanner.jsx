import { useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';

/** SM-1: session-expiry warning. The session is a fixed 8 h token with no
 * sliding refresh, so a user working late gets a bare 401 mid-submit.
 * From 15 minutes before expiry (7 h 45 m after login) this banner warns
 * them to save their work; the daily-report drafts are
 * already persisted to localStorage, so re-login restores the form.
 * Rendered once in App.jsx, above every page. */
export default function SessionExpiryBanner() {
  const { sessionExpiresAt, user } = useAuth();
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!sessionExpiresAt || !user) return undefined;
    const t = setInterval(() => setTick((n) => n + 1), 30000);
    return () => clearInterval(t);
  }, [sessionExpiresAt, user]);

  if (!sessionExpiresAt || !user) return null;
  const remainingMin = Math.ceil((new Date(sessionExpiresAt).getTime() - Date.now()) / 60000);
  if (remainingMin > 15 || remainingMin <= 0) return null;
  return (
    <div className="bg-amber-500/10 border-b border-amber-500/20 text-amber-700 dark:text-amber-400 text-sm font-medium px-4 py-2 text-center">
      Your session expires in {remainingMin} min — save your work. You will be asked to sign in again afterwards.
      Signing out signs you out on all devices.
    </div>
  );
}
