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
  updateUser: (userId, payload) => fetch(`/api/admin/users/${userId}`, {
    method: 'PATCH', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
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
  // ---- HR & payroll (ERP Phase 5) ----
  listEmployees: (buId) => getJson(`/api/ops/employees${buId ? `?business_unit_id=${buId}` : ''}`),
  createEmployee: (payload) => postJson('/api/ops/employees', payload),
  deleteEmployee: (id) => fetch(`/api/ops/employees/${id}`, { method: 'DELETE', credentials: 'include' }).then(handle),
  listLeave: (buId) => getJson(`/api/ops/leave-requests${buId ? `?business_unit_id=${buId}` : ''}`),
  createLeave: (payload) => postJson('/api/ops/leave-requests', payload),
  reviewLeave: (id, payload) => postJson(`/api/ops/leave-requests/${id}/review`, payload),
  listPayroll: (buId) => getJson(`/api/ops/payroll-runs${buId ? `?business_unit_id=${buId}` : ''}`),
  createPayroll: (payload) => postJson('/api/ops/payroll-runs', payload),
  updatePayrollLine: (runId, lineId, payload) => fetch(`/api/ops/payroll-runs/${runId}/lines/${lineId}`, {
    method: 'PATCH', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  approvePayroll: (id) => postJson(`/api/ops/payroll-runs/${id}/approve`),
  payPayroll: (id) => postJson(`/api/ops/payroll-runs/${id}/pay`),
  monthlyReportUrl: (month) => `/api/ops/monthly-report${month ? `?month=${month}` : ''}`,
  opsExportUrl: (tab, buId) => {
    const q = new URLSearchParams({ tab: tab || 'actions' });
    if (buId) q.set('business_unit_id', buId);
    return `/api/ops/export-excel?${q.toString()}`;
  },

  // ---- Accounting (ERP Phase 1) ----
  accBusinessUnits: () => getJson('/api/accounting/business-units'),
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
  // ---- Fixed assets & banking (ERP Phase 6) ----
  accFixedAssets: (buId) => getJson(`/api/accounting/fixed-assets?business_unit_id=${buId}`),
  accCreateAsset: (payload) => postJson('/api/accounting/fixed-assets', payload),
  accDisposeAsset: (id) => postJson(`/api/accounting/fixed-assets/${id}/dispose`),
  accDepreciate: (payload) => postJson('/api/accounting/fixed-assets/depreciate', payload),
  accBankAccounts: (buId) => getJson(`/api/accounting/bank-accounts?business_unit_id=${buId}`),
  accCreateBankAccount: (payload) => postJson('/api/accounting/bank-accounts', payload),
  accBankTxns: (bankId) => getJson(`/api/accounting/bank-accounts/${bankId}/transactions`),
  accAddBankTxn: (bankId, payload) => postJson(`/api/accounting/bank-accounts/${bankId}/transactions`, payload),
  accSetReconciled: (txnId, isReconciled) => fetch(`/api/accounting/bank-transactions/${txnId}`, {
    method: 'PATCH', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify({ is_reconciled: isReconciled }),
  }).then(handle),
  accReconciliation: (bankId) => getJson(`/api/accounting/bank-accounts/${bankId}/reconciliation`),
  // ---- Go-live setup (opening balances, posting rules, exports) ----
  accPostingRules: () => getJson('/api/accounting/posting-rules'),
  accSavePostingRule: (payload) => fetch('/api/accounting/posting-rules', {
    method: 'PUT', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  accOpeningTemplateUrl: (buId) => `/api/accounting/opening-balance/template?business_unit_id=${buId}`,
  accUploadOpeningBalance: async (buId, asOf, file) => {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch(`/api/accounting/opening-balance?business_unit_id=${buId}&as_of=${asOf}`, {
      method: 'POST', credentials: 'include', body: fd,
    });
    return handle(res);
  },
  accExportUrl: (report, format, buId, period) => {
    const q = new URLSearchParams({ report, format });
    if (buId) q.set('business_unit_id', buId);
    if (period) q.set('period', period);
    return `/api/accounting/reports/export?${q.toString()}`;
  },
  // ---- ERP B/C/D additions ----
  accFinanceSummary: () => getJson('/api/accounting/finance-summary'),
  accTaxReport: (buId, period) => getJson(`/api/accounting/reports/tax?business_unit_id=${buId}&period=${period}`),
  accCashFlow: (buId, period) => getJson(`/api/accounting/reports/cash-flow?business_unit_id=${buId}&period=${period}`),
  accYearClose: (buId, year) => fetch('/api/accounting/year-close', {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS,
    body: JSON.stringify({ business_unit_id: buId, year }),
  }).then(handle),
  accBudgets: (buId, period) => getJson(`/api/accounting/budgets?business_unit_id=${buId}&period=${period}`),
  accSaveBudget: (payload) => fetch('/api/accounting/budgets', {
    method: 'PUT', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  accDeleteBudget: (id) => fetch(`/api/accounting/budgets/${id}`, { method: 'DELETE', credentials: 'include' }).then(handle),
  accExchangeRates: (currency = 'USD') => getJson(`/api/accounting/exchange-rates?currency=${currency}`),
  accCreateRate: (payload) => fetch('/api/accounting/exchange-rates', {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  accIntercompany: () => getJson('/api/accounting/intercompany'),
  accCreateIntercompany: (payload) => fetch('/api/accounting/intercompany', {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  accSettleIntercompany: (id) => fetch(`/api/accounting/intercompany/${id}/settle`, { method: 'POST', credentials: 'include' }).then(handle),
  opsUploadReceipt: async (expenseId, file) => {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch(`/api/ops/expenses/${expenseId}/receipt`, { method: 'POST', credentials: 'include', body: fd });
    return handle(res);
  },
  // ---- Business units (management) ----
  adminBusinessUnits: () => getJson('/api/admin/business-units'),
  adminCreateBU: (payload) => postJson('/api/admin/business-units', payload),
  adminUpdateBU: (id, payload) => fetch(`/api/admin/business-units/${id}`, {
    method: 'PATCH', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  opsPayslipUrl: (runId, lineId) => `/api/ops/payroll-runs/${runId}/lines/${lineId}/payslip`,
  opsStatementUrl: (customerId, format) => `/api/ops/sales-customers/${customerId}/statement?format=${format}`,
  // ---- Nature modules (batches, projects, shipments, production, real estate, principal) ----
  modBatches: (itemId) => getJson(`/api/modules/inventory/${itemId}/batches`),
  modReceiveBatch: (itemId, payload) => fetch(`/api/modules/inventory/${itemId}/batches`, {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  modExpiring: (buId, days = 30) => getJson(`/api/modules/expiring?days=${days}${buId ? `&business_unit_id=${buId}` : ''}`),
  modProjects: (buId) => getJson(`/api/modules/projects${buId ? `?business_unit_id=${buId}` : ''}`),
  modCreateProject: (payload) => fetch('/api/modules/projects', {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  modProjectStatus: (id, status) => fetch(`/api/modules/projects/${id}/status?status=${status}`, { method: 'POST', credentials: 'include' }).then(handle),
  modProjectPL: (id) => getJson(`/api/modules/projects/${id}/pl`),
  modShipments: (buId) => getJson(`/api/modules/shipments${buId ? `?business_unit_id=${buId}` : ''}`),
  modCreateShipment: (payload) => fetch('/api/modules/shipments', {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  modShipmentStatus: (id, status) => fetch(`/api/modules/shipments/${id}/status?status=${status}`, { method: 'POST', credentials: 'include' }).then(handle),
  modBoms: (buId) => getJson(`/api/modules/boms${buId ? `?business_unit_id=${buId}` : ''}`),
  modCreateBom: (payload) => fetch('/api/modules/boms', {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  modProduction: (buId) => getJson(`/api/modules/production-orders${buId ? `?business_unit_id=${buId}` : ''}`),
  modCreateProduction: (payload) => fetch('/api/modules/production-orders', {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  modCompleteProduction: (id) => fetch(`/api/modules/production-orders/${id}/complete`, { method: 'POST', credentials: 'include' }).then(handle),
  modUnits: (buId) => getJson(`/api/modules/property-units${buId ? `?business_unit_id=${buId}` : ''}`),
  modCreateUnit: (payload) => fetch('/api/modules/property-units', {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  modSellUnit: (id, payload) => fetch(`/api/modules/property-units/${id}/sell`, {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  modInstallments: (invoiceId) => getJson(`/api/modules/invoices/${invoiceId}/installments`),
  modLeases: (buId) => getJson(`/api/modules/leases${buId ? `?business_unit_id=${buId}` : ''}`),
  modCreateLease: (payload) => fetch('/api/modules/leases', {
    method: 'POST', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  modBillLease: (id, period) => fetch(`/api/modules/leases/${id}/bill${period ? `?period=${period}` : ''}`, { method: 'POST', credentials: 'include' }).then(handle),
  modPrincipalReport: (buId, period) => getJson(`/api/modules/principal-report?${new URLSearchParams({ ...(buId ? { business_unit_id: buId } : {}), ...(period ? { period } : {}) })}`),
  // ---- ERP completion: POS, quotations, returns, transfers, serials, advances, attendance ----
  listPosSales: (buId, saleDate) => getJson(`/api/ops/pos-sales?${new URLSearchParams({ ...(buId ? { business_unit_id: buId } : {}), ...(saleDate ? { sale_date: saleDate } : {}) })}`),
  createPosSale: (payload) => postJson('/api/ops/pos-sales', payload),
  voidPosSale: (id) => postJson(`/api/ops/pos-sales/${id}/void`),
  posDailySummary: (buId, day) => getJson(`/api/ops/pos-sales/daily-summary?${new URLSearchParams({ ...(buId ? { business_unit_id: buId } : {}), ...(day ? { date: day } : {}) })}`),
  listQuotations: (buId) => getJson(`/api/ops/quotations${buId ? `?business_unit_id=${buId}` : ''}`),
  createQuotation: (payload) => postJson('/api/ops/quotations', payload),
  updateQuotation: (id, payload) => fetch(`/api/ops/quotations/${id}`, {
    method: 'PATCH', credentials: 'include', headers: JSON_HEADERS, body: JSON.stringify(payload),
  }).then(handle),
  setQuotationStatus: (id, status) => postJson(`/api/ops/quotations/${id}/status`, { status }),
  convertQuotation: (id) => postJson(`/api/ops/quotations/${id}/convert`),
  invoicePdfUrl: (id) => `/api/ops/sales-invoices/${id}/pdf`,
  deliveryNoteUrl: (id) => `/api/ops/sales-invoices/${id}/delivery-note`,
  listSalesReturns: (buId, invoiceId) => getJson(`/api/ops/sales-returns?${new URLSearchParams({ ...(buId ? { business_unit_id: buId } : {}), ...(invoiceId ? { invoice_id: invoiceId } : {}) })}`),
  createSalesReturn: (payload) => postJson('/api/ops/sales-returns', payload),
  listPurchaseReturns: (buId, poId) => getJson(`/api/ops/purchase-returns?${new URLSearchParams({ ...(buId ? { business_unit_id: buId } : {}), ...(poId ? { po_id: poId } : {}) })}`),
  createPurchaseReturn: (payload) => postJson('/api/ops/purchase-returns', payload),
  modStockTransfers: (params = {}) => getJson(`/api/modules/stock-transfers?${new URLSearchParams({ ...(params.from ? { from_business_unit_id: params.from } : {}), ...(params.to ? { to_business_unit_id: params.to } : {}), ...(params.status ? { status: params.status } : {}) })}`),
  modCreateTransfer: (payload) => postJson('/api/modules/stock-transfers', payload),
  modSendTransfer: (id) => postJson(`/api/modules/stock-transfers/${id}/send`),
  modReceiveTransfer: (id) => postJson(`/api/modules/stock-transfers/${id}/receive`),
  modCancelTransfer: (id) => postJson(`/api/modules/stock-transfers/${id}/cancel`),
  listSerials: (itemId, status) => getJson(`/api/ops/inventory/${itemId}/serials${status ? `?status=${status}` : ''}`),
  addSerials: (itemId, payload) => postJson(`/api/ops/inventory/${itemId}/serials`, payload),
  lookupSerial: (sn) => getJson(`/api/ops/serials/lookup?serial_no=${encodeURIComponent(sn)}`),
  setSerialStatus: (id, status) => postJson(`/api/ops/serials/${id}/status`, { status }),
  listAdvances: (buId, status) => getJson(`/api/ops/staff-advances?${new URLSearchParams({ ...(buId ? { business_unit_id: buId } : {}), ...(status ? { status } : {}) })}`),
  createAdvance: (payload) => postJson('/api/ops/staff-advances', payload),
  approveAdvance: (id) => postJson(`/api/ops/staff-advances/${id}/approve`),
  rejectAdvance: (id) => postJson(`/api/ops/staff-advances/${id}/reject`),
  approvePO: (id) => postJson(`/api/ops/purchase-orders/${id}/approve`),
  rejectPOApproval: (id) => postJson(`/api/ops/purchase-orders/${id}/reject-approval`),
  accApproveJournal: (id) => postJson(`/api/accounting/journal/${id}/approve`),
  accRejectJournalApproval: (id) => postJson(`/api/accounting/journal/${id}/reject-approval`),
  bulkAttendance: (payload) => postJson('/api/ops/attendance/bulk', payload),
  listAttendance: (buId, month) => getJson(`/api/ops/attendance?${new URLSearchParams({ ...(buId ? { business_unit_id: buId } : {}), ...(month ? { month } : {}) })}`),
  leaveBalances: (buId, year) => getJson(`/api/ops/leave-balances?${new URLSearchParams({ ...(buId ? { business_unit_id: buId } : {}), ...(year ? { year } : {}) })}`),
};

export { ApiError };
