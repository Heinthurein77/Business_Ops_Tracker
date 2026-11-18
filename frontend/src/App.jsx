import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider, useAuth, resolveHomePath } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import ProtectedRoute from './components/ProtectedRoute';
import SessionExpiryBanner from './components/SessionExpiryBanner';

// F-m6: one lazy chunk per route — the login page no longer downloads
// every page's code. The auth pages stay eager (they are the entry point).
import { lazy, Suspense } from 'react';
import ErrorBoundary from './components/ErrorBoundary';
import LoginPage from './pages/LoginPage';
import ForcePasswordChangePage from './pages/ForcePasswordChangePage';
import TotpSetupPage from './pages/TotpSetupPage';
const ProfileSetupPage = lazy(() => import('./pages/ProfileSetupPage'));
const CeoDashboardPage = lazy(() => import('./pages/CeoDashboardPage'));
const CeoHistoryPage = lazy(() => import('./pages/CeoHistoryPage'));
const CeoReportLogsPage = lazy(() => import('./pages/CeoReportLogsPage'));
const AdminDashboardPage = lazy(() => import('./pages/AdminDashboardPage'));
const ComparisonPage = lazy(() => import('./pages/ComparisonPage'));
const OperationsPage = lazy(() => import('./pages/OperationsPage'));
const DailyOpsPage = lazy(() => import('./pages/DailyOpsPage'));
const ActivityDashboardPage = lazy(() => import('./pages/ActivityDashboardPage'));
const CeoCalendarPage = lazy(() => import('./pages/CeoCalendarPage'));
const HealthDashboardPage = lazy(() => import('./pages/HealthDashboardPage'));
const ReportLogsPage = lazy(() => import('./pages/ReportLogsPage'));
const SecurityLogPage = lazy(() => import('./pages/SecurityLogPage'));
const CeoProfilesPage = lazy(() => import('./pages/CeoProfilesPage'));
const BusinessUnitDetailPage = lazy(() => import('./pages/BusinessUnitDetailPage'));
const HeadOfficePage = lazy(() => import('./pages/HeadOfficePage'));
const ManageUsersPage = lazy(() => import('./pages/ManageUsersPage'));
const BusinessUnitsPage = lazy(() => import('./pages/BusinessUnitsPage'));

function RouteLoading() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="w-8 h-8 border-2 border-slate-200 border-t-brand-600 rounded-full animate-spin" />
    </div>
  );
}

function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={resolveHomePath(user)} replace />;
}

function LoginRoute() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (user) return <Navigate to={resolveHomePath(user)} replace />;
  return <LoginPage />;
}

export default function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
      <AuthProvider>
        <SessionExpiryBanner />
        <ErrorBoundary>
        <Suspense fallback={<RouteLoading />}>
        <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/login" element={<LoginRoute />} />
          <Route
            path="/force-password-change"
            element={
              <ProtectedRoute>
                <ForcePasswordChangePage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/profile-setup"
            element={
              <ProtectedRoute roles={['CEO']}>
                <ProfileSetupPage />
              </ProtectedRoute>
            }
          />

          <Route
            path="/ceo"
            element={
              <ProtectedRoute roles={['CEO']}>
                <CeoDashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ceo/history"
            element={
              <ProtectedRoute roles={['CEO']}>
                <CeoHistoryPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ceo/report-logs"
            element={
              <ProtectedRoute roles={['CEO']}>
                <CeoReportLogsPage />
              </ProtectedRoute>
            }
          />

          <Route
            path="/admin"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <AdminDashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/comparison"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <ComparisonPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/daily-ops"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <DailyOpsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/operations"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <OperationsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/activity-dashboard"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <ActivityDashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/ceo-calendar"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <CeoCalendarPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/health-dashboard"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <HealthDashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/report-logs"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <ReportLogsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/ceo-profiles"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <CeoProfilesPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/business-unit/:id"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <BusinessUnitDetailPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/head-office"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <HeadOfficePage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/business-units"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <BusinessUnitsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/users"
            element={
              <ProtectedRoute roles={['Chairman']}>
                <ManageUsersPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/security-log"
            element={
              <ProtectedRoute roles={['Chairman']}>
                <SecurityLogPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/totp-setup"
            element={
              <ProtectedRoute>
                <TotpSetupPage />
              </ProtectedRoute>
            }
          />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
        </ErrorBoundary>
      </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
