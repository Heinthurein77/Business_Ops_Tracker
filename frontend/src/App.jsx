import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider, useAuth, resolveHomePath } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import ProtectedRoute from './components/ProtectedRoute';

import LoginPage from './pages/LoginPage';
import ForcePasswordChangePage from './pages/ForcePasswordChangePage';
import ProfileSetupPage from './pages/ProfileSetupPage';
import CeoDashboardPage from './pages/CeoDashboardPage';
import CeoHistoryPage from './pages/CeoHistoryPage';
import CeoReportLogsPage from './pages/CeoReportLogsPage';
import AdminDashboardPage from './pages/AdminDashboardPage';
import ComparisonPage from './pages/ComparisonPage';
import OperationsPage from './pages/OperationsPage';
import OpsPage from './pages/OpsPage';
import AccountingPage from './pages/AccountingPage';
import ActivityDashboardPage from './pages/ActivityDashboardPage';
import HealthDashboardPage from './pages/HealthDashboardPage';
import ReportLogsPage from './pages/ReportLogsPage';
import SecurityLogPage from './pages/SecurityLogPage';
import CeoProfilesPage from './pages/CeoProfilesPage';
import BusinessUnitDetailPage from './pages/BusinessUnitDetailPage';
import HeadOfficePage from './pages/HeadOfficePage';
import ManageUsersPage from './pages/ManageUsersPage';

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
            path="/accounting"
            element={
              <ProtectedRoute roles={['Chairman', 'HO', 'Accountant']}>
                <AccountingPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/operations-center"
            element={
              <ProtectedRoute roles={['Chairman', 'HO']}>
                <OpsPage />
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

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
