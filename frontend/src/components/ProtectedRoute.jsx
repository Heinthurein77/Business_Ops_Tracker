import { Navigate, useLocation } from 'react-router-dom';
import { useAuth, roleHomePath } from '../context/AuthContext';

export default function ProtectedRoute({ roles, children }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="w-8 h-8 border-2 border-slate-300 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Password rotation always comes first — a CEO who both needs a new
  // password AND hasn't completed their profile must land on
  // /force-password-change, not /profile-setup, otherwise the two redirects
  // below would bounce the user between the two routes forever.
  if (user.require_password_change) {
    if (location.pathname !== '/force-password-change') {
      return <Navigate to="/force-password-change" replace />;
    }
    return children;
  }

  if (location.pathname === '/force-password-change') {
    return <Navigate to={roleHomePath(user.role)} replace />;
  }

  const needsProfileSetup = user.role === 'CEO' && !user.profile_completed;
  if (needsProfileSetup && location.pathname !== '/profile-setup') {
    return <Navigate to="/profile-setup" replace />;
  }

  if (roles && !roles.includes(user.role)) {
    return <Navigate to={roleHomePath(user.role)} replace />;
  }

  return children;
}
