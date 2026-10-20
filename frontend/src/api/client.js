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

  // ---- Operations extensions ----
  listActionItems: (params = {}) => {
    const q = new URLSearchParams();
    if (params.business_unit_id) q.set('business_unit_id', params.business_unit_id);
    if (params.status) q.set('status', params.status);
    if (params.mine) q.set('mine', 'true');
    const query = q.toString();
    return getJson(`/api/ops/action-items${query ? `?${query}` : ''}`);
  },
  createActionItem: (payload) => postJson('/api/ops/action-items', payload),
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

  listInventory: (params = {}) => {
    const q = new URLSearchParams();
    if (params.business_unit_id) q.set('business_unit_id', params.business_unit_id);
    if (params.low_only) q.set('low_only', 'true');
    const query = q.toString();
    return getJson(`/api/ops/inventory${query ? `?${query}` : ''}`);
  },
  createInventoryItem: (payload) => postJson('/api/ops/inventory', payload),
  recordStockMovement: (id, payload) => postJson(`/api/ops/inventory/${id}/movements`, payload),
  listStockMovements: (id) => getJson(`/api/ops/inventory/${id}/movements`),
  deleteInventoryItem: (id) => fetch(`/api/ops/inventory/${id}`, {
    method: 'DELETE', credentials: 'include',
  }).then(handle),

  listExpenses: (params = {}) => {
    const q = new URLSearchParams();
    if (params.business_unit_id) q.set('business_unit_id', params.business_unit_id);
    if (params.status) q.set('status', params.status);
    const query = q.toString();
    return getJson(`/api/ops/expenses${query ? `?${query}` : ''}`);
  },
  submitExpense: (payload) => postJson('/api/ops/expenses', payload),
  reviewExpense: (id, payload) => postJson(`/api/ops/expenses/${id}/review`, payload),

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
  createDocument: (payload) => postJson('/api/ops/documents', payload),
  deleteDocument: (id) => fetch(`/api/ops/documents/${id}`, {
    method: 'DELETE', credentials: 'include',
  }).then(handle),
  checkDocumentExpiry: () => postJson('/api/ops/documents/check-expiry'),
  // ---- Procurement (ERP Phase 3) ----
  listSuppliers: (buId) => getJson(`/api/ops/suppliers${buId ? `?business_unit_id=${buId}` : ''}`),
  createSupplier: (payload) => postJson('/api/ops/suppliers', payload),
  deleteSupplier: (id) => fetch(`/api/ops/suppliers/${id}`, { method: 'DELETE', credentials: 'include' }).then(handle),
  listPOs: (buId) => getJson(`/api/ops/purchase-orders${buId ? `?business_unit_id=${buId}` : ''}`),
  createPO: (payload) => postJson('/api/ops/purchase-orders', payload),
  orderPO: (id) => postJson(`/api/ops/purchase-orders/${id}/order`),
  receivePO: (id) => postJson(`/api/ops/purchase-orders/${id}/receive`),
  payPO: (id) => postJson(`/api/ops/purchase-orders/${id}/pay`),
  cancelPO: (id) => postJson(`/api/ops/purchase-orders/${id}/cancel`),
  transferStock: (itemId, payload) => postJson(`/api/ops/inventory/${itemId}/transfer`, payload),
  // ---- Sales & receivables (ERP Phase 4) ----
  listSalesCustomers: (buId) => getJson(`/api/ops/sales-customers${buId ? `?business_unit_id=${buId}` : ''}`),
  createSalesCustomer: (payload) => postJson('/api/ops/sales-customers', payload),
  deleteSalesCustomer: (id) => fetch(`/api/ops/sales-customers/${id}`, { method: 'DELETE', credentials: 'include' }).then(handle),
  listInvoices: (buId) => getJson(`/api/ops/invoices${buId ? `?business_unit_id=${buId}` : ''}`),
  createInvoice: (payload) => postJson('/api/ops/invoices', payload),
  sendInvoice: (id) => postJson(`/api/ops/invoices/${id}/send`),
  cancelInvoice: (id) => postJson(`/api/ops/invoices/${id}/cancel`),
  recordInvoicePayment: (id, payload) => postJson(`/api/ops/invoices/${id}/payments`, payload),
  aging: (buId) => getJson(`/api/ops/aging${buId ? `?business_unit_id=${buId}` : ''}`),
  monthlyReportUrl: (month) => `/api/ops/monthly-report${month ? `?month=${month}` : ''}`,
  opsExportUrl: (tab, buId) => {
    const q = new URLSearchParams({ tab: tab || 'actions' });
    if (buId) q.set('business_unit_id', buId);
    return `/api/ops/export-excel?${q.toString()}`;
  },

  // ---- Accounting (ERP Phase 1) ----
  accTemplates: () => getJson('/api/accounting/templates'),
  accProvision: (buId, template) => postJson(`/api/accounting/provision/${buId}?template=${template}`),
  accCoa: (buId) => getJson(`/api/accounting/coa?business_unit_id=${buId}`),
  accCreateAccount: (payload) => postJson('/api/accounting/accounts', payload),
  accUpdateAccount: (id, payload) => fetch(`/api/accounting/accounts/${id}`, {
    method: 'PATCH', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  accJournal: (buId, params = {}) => {
    const q = new URLSearchParams({ business_unit_id: buId });
    if (params.status) q.set('status', params.status);
    if (params.source) q.set('source', params.source);
    if (params.period) q.set('period', params.period);
    return getJson(`/api/accounting/journal?${q.toString()}`);
  },
  accCreateJournal: (payload) => postJson('/api/accounting/journal', payload),
  accPostJournal: (id) => postJson(`/api/accounting/journal/${id}/post`),
  accReverseJournal: (id) => postJson(`/api/accounting/journal/${id}/reverse`),
  accDeleteJournal: (id) => fetch(`/api/accounting/journal/${id}`, {
    method: 'DELETE', credentials: 'include',
  }).then(handle),
  accTrialBalance: (buId, period) => getJson(`/api/accounting/trial-balance?business_unit_id=${buId}${period ? `&period=${period}` : ''}`),
  accLedger: (accountId, period) => getJson(`/api/accounting/ledger?account_id=${accountId}${period ? `&period=${period}` : ''}`),
  accPL: (buId, period) => getJson(`/api/accounting/reports/pl?business_unit_id=${buId}${period ? `&period=${period}` : ''}`),
  accBS: (buId, period) => getJson(`/api/accounting/reports/balance-sheet?business_unit_id=${buId}${period ? `&period=${period}` : ''}`),
  accPeriods: (buId) => getJson(`/api/accounting/periods?business_unit_id=${buId}`),
  accClosePeriod: (payload) => postJson('/api/accounting/periods/close', payload),
  accReopenPeriod: (payload) => postJson('/api/accounting/periods/reopen', payload),
  accConsolidatedPL: (period) => getJson(`/api/accounting/reports/consolidated-pl${period ? `?period=${period}` : ''}`),
  accConsolidatedBS: (period) => getJson(`/api/accounting/reports/consolidated-bs${period ? `?period=${period}` : ''}`),
  accBuComparison: (period) => getJson(`/api/accounting/reports/bu-comparison${period ? `?period=${period}` : ''}`),
};

export { ApiError };
