import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from '../api/client';

const AuthContext = createContext(null);

export function roleHomePath(role) {
  if (role === 'CEO') return '/ceo';
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

  const refresh = useCallback(async () => {
    try {
      const data = await api.me();
      setUser(data.user);
      return data.user;
    } catch {
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const login = useCallback(async (username, password) => {
    const data = await api.login(username, password);
    setUser(data.user);
    return data;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
