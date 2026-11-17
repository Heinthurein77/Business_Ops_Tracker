const JSON_HEADERS = { 'Content-Type': 'application/json' };

class ApiError extends Error {
  constructor(message, status, redirectTo, data) {
    super(message);
    this.status = status;
    this.redirectTo = redirectTo;
    // M-01: the full response body — the login flow reads
    // `data.totp_setup_token` from a TOTP_ENROLLMENT_REQUIRED response.
    this.data = data || null;
  }
}

// FastAPI returns `detail` as a string for HTTPException and as a list of
// {loc, msg, type} objects for 422 validation errors. Normalise both to text.
function formatDetail(detail) {
  if (typeof detail === 'string') return detail.trim() || null;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item === 'string' ? item : item?.msg))
      .filter((msg) => typeof msg === 'string' && msg.trim())
      .map((msg) => msg.trim().replace(/^Value error,\s*/i, ''));
    if (messages.length === 0) return null;
    const sentence = messages.join('. ');
    return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
  }
  return null;
}

async function handle(response) {
  if (response.status === 204) return null;
  const isJson = (response.headers.get('content-type') || '').includes('application/json');
  const body = isJson ? await response.json().catch(() => ({})) : null;
  if (!response.ok) {
    if (response.status === 401 && body?.detail !== 'TOTP_REQUIRED') {
      // SM-1: global 401 handling — every API call funnels through here,
      // so a session that expired (or was revoked on another device) is
      // caught in one place instead of each page's .catch. The flag lets
      // the login page say "your session expired" instead of a bare
      // redirect, and the event lets AuthContext drop the user immediately
      // (ProtectedRoute then sends the tab to /login). A 401 with detail
      // TOTP_REQUIRED is not an expired session — it is a login step
      // asking for a second-factor code — so it skips this side effect.
      try {
        sessionStorage.setItem('bot-session-expired', '1');
        window.dispatchEvent(new CustomEvent('session-expired'));
      } catch { /* storage/event unavailable — the 401 still throws below */ }
    }
    throw new ApiError(formatDetail(body?.detail) || `Request failed (${response.status})`, response.status, body?.redirect_to, body);
  }
  return body;
}

function getJson(path) {
  return fetch(path, { credentials: 'include' }).then(handle);
}

function postJson(path, data, extraHeaders = {}) {
  return fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: { ...JSON_HEADERS, ...extraHeaders },
    body: JSON.stringify(data ?? {}),
  }).then(handle);
}

// m-10: one fresh UUID per create attempt, sent as Idempotency-Key so a
// retried request replays the first response instead of creating a
// second row. crypto.randomUUID is available in all modern browsers;
// the fallback only covers very old ones.
function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function postForm(path, formData) {
  return fetch(path, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  }).then(handle);
}

export const api = {
  me: () => getJson('/api/auth/me'),
  login: (username, password, totpCode) => postJson('/api/auth/login', { username, password, totp_code: totpCode || null }),
  logout: () => postJson('/api/auth/logout'),
  forgotPassword: (username) => postJson('/api/auth/forgot-password', { username }),
  // M-01: TOTP second factor. totpEnroll/totpConfirm take the setup token
  // from a TOTP_ENROLLMENT_REQUIRED login response; omit it when calling
  // with a live session.
  totpEnroll: (setupToken) => postJson('/api/auth/totp/enroll', { setup_token: setupToken || null }),
  totpConfirm: (code, setupToken) => postJson('/api/auth/totp/confirm', { code, setup_token: setupToken || null }),
  totpDisable: (code) => postJson('/api/auth/totp/disable', { code }),
  totpDisableUser: (userId) => postJson(`/api/admin/users/${userId}/totp-disable`),
  forcePasswordChange: (currentPassword, newPassword, confirmPassword) =>
    postJson('/api/auth/force-password-change', {
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    }),

  hoDashboard: () => getJson('/api/ho/dashboard'),
  submitHoReport: (formData) => postForm('/api/ho/submit-report', formData),
  setHoMonthlyTarget: (payload) => postJson('/api/ho/monthly-target', payload),

  ceoDashboard: (month, date) => {
    const q = new URLSearchParams();
    if (month) q.set('month', month);
    if (date) q.set('date', date);
    const query = q.toString();
    return getJson(`/api/ceo/dashboard${query ? `?${query}` : ''}`);
  },
  submitReport: (formData) => postForm('/api/ceo/submit-report', formData),
  setMonthlyTarget: (payload) => postJson('/api/ceo/monthly-target', payload),
  getCeoProfile: () => getJson('/api/ceo/profile'),
  setCeoProfile: (formData) => postForm('/api/ceo/profile', formData),
  addFinancialNote: (noteText) => postJson('/api/ceo/financial-notes', { note_text: noteText }),

  adminOverview: () => getJson('/api/admin/overview'),
  adminComparison: () => getJson('/api/admin/comparison'),
  adminOperations: () => getJson('/api/admin/operations'),
  businessUnitDetail: (id) => getJson(`/api/admin/business-unit/${id}`),
  overrideMonthlyTarget: (id, payload) => postJson(`/api/admin/business-unit/${id}/monthly-target`, payload),
  reportLogs: () => getJson('/api/admin/report-logs'),
  securityLog: () => getJson('/api/admin/security-log'),
  ceoProfiles: () => getJson('/api/admin/ceo-profiles'),
  headOfficeReport: () => getJson('/api/admin/head-office'),
  overrideHoMonthlyTarget: (payload) => postJson('/api/admin/head-office/monthly-target', payload),
  activityDashboard: () => getJson('/api/admin/activity-dashboard'),
  ceoActivityCalendar: (buId, month) =>
    getJson(`/api/admin/ceo-activity-calendar?business_unit_id=${buId}${month ? `&month=${month}` : ''}`),
  resolveHelp: (logId, response) => postJson(`/api/admin/activity-logs/${logId}/resolve-help`, { response }),
  ceoMonthlySummaryUrl: (buId, month) =>
    `/api/admin/ceo-monthly-summary?business_unit_id=${buId}${month ? `&month=${month}` : ''}`,
  healthDashboard: () => getJson('/api/admin/health-dashboard'),
  listUsers: () => getJson('/api/admin/users'),
  createUser: (payload) => postJson('/api/admin/users', payload),
  resetUserPassword: (userId) => postJson(`/api/admin/users/${userId}/reset-password`),
  setUserActive: (userId, isActive) => postJson(`/api/admin/users/${userId}/active`, { is_active: isActive }),
  unlockUserLogin: (userId) => postJson(`/api/admin/users/${userId}/unlock-login`),
  listPasswordResets: () => getJson('/api/admin/password-resets'),
  approvePasswordReset: (id) => postJson(`/api/admin/password-resets/${id}/approve`),
  rejectPasswordReset: (id) => postJson(`/api/admin/password-resets/${id}/reject`),
  exportExcelUrl: (startDate, endDate) => {
    const params = new URLSearchParams();
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    const query = params.toString();
    return `/api/admin/export-excel${query ? `?${query}` : ''}`;
  },

  // ---- Operations extensions (tracker daily ops) ----
  listActionItems: (params = {}) => {
    const q = new URLSearchParams();
    if (params.business_unit_id) q.set('business_unit_id', params.business_unit_id);
    if (params.status) q.set('status', params.status);
    if (params.mine) q.set('mine', 'true');
    const query = q.toString();
    return getJson(`/api/ops/action-items${query ? `?${query}` : ''}`);
  },
  createActionItem: (payload) => postJson('/api/ops/action-items', payload, { 'Idempotency-Key': newIdempotencyKey() }),
  updateActionItem: (id, payload) => fetch(`/api/ops/action-items/${id}`, {
    method: 'PATCH', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  deleteActionItem: (id) => fetch(`/api/ops/action-items/${id}`, {
    method: 'DELETE', credentials: 'include',
  }).then(handle),

  listNotifications: (unreadOnly = false) => getJson(`/api/ops/notifications${unreadOnly ? '?unread_only=true' : ''}`),
  markNotificationRead: (id) => postJson(`/api/ops/notifications/${id}/read`),
  markAllNotificationsRead: () => postJson('/api/ops/notifications/read-all'),
  remindMissingReports: () => postJson('/api/ops/notifications/remind-missing'),
  remindActionItems: () => postJson('/api/ops/notifications/remind-actions'),

  submitStaff: (payload) => postJson('/api/ops/staff', payload),
  listStaff: (params = {}) => {
    const q = new URLSearchParams();
    if (params.business_unit_id) q.set('business_unit_id', params.business_unit_id);
    if (params.days) q.set('days', params.days);
    const query = q.toString();
    return getJson(`/api/ops/staff${query ? `?${query}` : ''}`);
  },
  submitCustomers: (payload) => postJson('/api/ops/customers', payload),
  listCustomers: (params = {}) => {
    const q = new URLSearchParams();
    if (params.business_unit_id) q.set('business_unit_id', params.business_unit_id);
    if (params.days) q.set('days', params.days);
    const query = q.toString();
    return getJson(`/api/ops/customers${query ? `?${query}` : ''}`);
  },
  forecast: (businessUnitId) => getJson(`/api/ops/forecast${businessUnitId ? `?business_unit_id=${businessUnitId}` : ''}`),

  listDocuments: (params = {}) => {
    const q = new URLSearchParams();
    if (params.business_unit_id) q.set('business_unit_id', params.business_unit_id);
    if (params.expiring_soon) q.set('expiring_soon', 'true');
    const query = q.toString();
    return getJson(`/api/ops/documents${query ? `?${query}` : ''}`);
  },
  createDocument: (payload) => postJson('/api/ops/documents', payload, { 'Idempotency-Key': newIdempotencyKey() }),
  deleteDocument: (id) => fetch(`/api/ops/documents/${id}`, {
    method: 'DELETE', credentials: 'include',
  }).then(handle),
  checkDocumentExpiry: () => postJson('/api/ops/documents/check-expiry'),
  monthlyReportUrl: (month) => `/api/ops/monthly-report${month ? `?month=${month}` : ''}`,
  opsExportUrl: (tab, buId) => {
    const q = new URLSearchParams({ tab: tab || 'actions' });
    if (buId) q.set('business_unit_id', buId);
    return `/api/ops/export-excel?${q.toString()}`;
  },

  // ---- Business units (management) ----
  adminBusinessUnits: () => getJson('/api/admin/business-units'),
  adminCreateBU: (payload) => postJson('/api/admin/business-units', payload),
  adminUpdateBU: (id, payload) => fetch(`/api/admin/business-units/${id}`, {
    method: 'PATCH', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
};

export { ApiError };
