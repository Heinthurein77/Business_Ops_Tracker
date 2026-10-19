const JSON_HEADERS = { 'Content-Type': 'application/json' };

class ApiError extends Error {
  constructor(message, status, redirectTo) {
    super(message);
    this.status = status;
    this.redirectTo = redirectTo;
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
    throw new ApiError(formatDetail(body?.detail) || `Request failed (${response.status})`, response.status, body?.redirect_to);
  }
  return body;
}

function getJson(path) {
  return fetch(path, { credentials: 'include' }).then(handle);
}

function postJson(path, data) {
  return fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: JSON_HEADERS,
    body: JSON.stringify(data ?? {}),
  }).then(handle);
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
  login: (username, password) => postJson('/api/auth/login', { username, password }),
  logout: () => postJson('/api/auth/logout'),
  forgotPassword: (username) => postJson('/api/auth/forgot-password', { username }),
  forcePasswordChange: (currentPassword, newPassword, confirmPassword) =>
    postJson('/api/auth/force-password-change', {
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    }),

  hoDashboard: () => getJson('/api/ho/dashboard'),
  submitHoReport: (formData) => postForm('/api/ho/submit-report', formData),
  getHoMonthlyTarget: (month) => getJson(`/api/ho/monthly-target${month ? `?month=${month}` : ''}`),
  setHoMonthlyTarget: (payload) => postJson('/api/ho/monthly-target', payload),

  ceoDashboard: (month) => getJson(`/api/ceo/dashboard${month ? `?month=${month}` : ''}`),
  submitReport: (formData) => postForm('/api/ceo/submit-report', formData),
  getMonthlyTarget: (month) => getJson(`/api/ceo/monthly-target${month ? `?month=${month}` : ''}`),
  setMonthlyTarget: (payload) => postJson('/api/ceo/monthly-target', payload),
  getCeoProfile: () => getJson('/api/ceo/profile'),
  setCeoProfile: (formData) => postForm('/api/ceo/profile', formData),
  getFinancialNotes: () => getJson('/api/ceo/financial-notes'),
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
  healthDashboard: () => getJson('/api/admin/health-dashboard'),
  listUsers: () => getJson('/api/admin/users'),
  createUser: (payload) => postJson('/api/admin/users', payload),
  resetUserPassword: (userId) => postJson(`/api/admin/users/${userId}/reset-password`),
  setUserActive: (userId, isActive) => postJson(`/api/admin/users/${userId}/active`, { is_active: isActive }),
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
};

export { ApiError };
