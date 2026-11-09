import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from '../api/client';

const AuthContext = createContext(null);

export function roleHomePath(role) {
  if (role === 'CEO') return '/ceo';
  if (role === 'Accountant') return '/accounting';
  if (role === 'Chairman' || role === 'HO') return '/admin';
  return '/login';
}

// Mirrors the backend's resolve_redirect_path() (src/auth.py) so the
// frontend never disagrees with the API about where a user should land:
// pending password rotation first, then an incomplete CEO profile, then
// the user's normal role home.
export function resolveHomePath(user) {
  if (!user) return '/login';
  if (user.require_password_change) return '/force-password-change';
  if (user.role === 'CEO' && !user.profile_completed) return '/profile-setup';
  return roleHomePath(user.role);
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  // SM-1: the session's real expiry, from /api/auth/me (the cookie is
  // httponly, so the frontend cannot read it itself). Drives the
  // "session expires in 15 min" warning (SessionExpiryBanner).
  const [sessionExpiresAt, setSessionExpiresAt] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.me();
      setUser(data.user);
      setSessionExpiresAt(data.session_expires_at || null);
      return data.user;
    } catch {
      setUser(null);
      setSessionExpiresAt(null);
      return null;
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  // SM-1: the global 401 handler in api/client.js fires this event when
  // any call comes back 401 (expired or revoked session). Drop the user
  // at once — ProtectedRoute then sends the tab to /login — instead of
  // leaving a logged-in-looking UI until the next action fails.
  useEffect(() => {
    const onExpired = () => {
      setUser(null);
      setSessionExpiresAt(null);
    };
    window.addEventListener('session-expired', onExpired);
    return () => window.removeEventListener('session-expired', onExpired);
  }, []);

  const login = useCallback(async (username, password) => {
    const data = await api.login(username, password);
    setUser(data.user);
    // Exact expiry comes from /api/auth/me; refresh so the warning banner
    // has the real value instead of a client-side guess.
    refresh().catch(() => {});
    return data;
  }, [refresh]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
      setSessionExpiresAt(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh, setUser, sessionExpiresAt }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
