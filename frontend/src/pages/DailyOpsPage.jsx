import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import LastUpdated from '../components/LastUpdated';
import useRefreshOnFocus from '../hooks/useRefreshOnFocus';
import { GLASS, PAGE_BG } from '../utils/glassTheme';
import { money, localMonth } from '../utils/format';

// Tracker-only daily-ops inputs (ex-Operations Center, ERP tabs removed
// 2026-11-17): action items, documents, staff, customers, forecast.
// All five call kept tracker APIs under /api/ops/*.
const TABS = [
  { key: 'actions', label: 'Action Items' },
  { key: 'documents', label: 'Documents' },
  { key: 'staff', label: 'Staff' },
  { key: 'customers', label: 'Customers' },
  { key: 'forecast', label: 'Forecast' },
];

const STATUS_BADGE = {
  pending: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  in_progress: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
  done: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
};

// F-M5: money() now comes from utils/format.js (rounded, null -> Ks 0).

export default function DailyOpsPage() {
  const [tab, setTab] = useState('actions');
  const [bus, setBus] = useState([]);
  const [buId, setBuId] = useState('');

  const [actions, setActions] = useState([]);
  const [newTitle, setNewTitle] = useState('');
  const [newDue, setNewDue] = useState('');
  // PM-1: users of the selected BU, for the assignee picker. listUsers
  // is Chairman-only (src/routers/users.py), so an HO user gets no
  // picker options — see the report note.
  const [users, setUsers] = useState([]);
  const [newAssignee, setNewAssignee] = useState('');
  // PM-2: how many CEOs the manual missing-report button would ping.
  const [missingCount, setMissingCount] = useState(null);

  const [documents, setDocuments] = useState([]);
  const [docForm, setDocForm] = useState({ title: '', document_type: 'license', expiry_date: '', notes: '' });
  const [reportMonth, setReportMonth] = useState(localMonth());
  const [staff, setStaff] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [forecast, setForecast] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // SM-5: when the lists were last refreshed (LastUpdated, top of page).
  const [updatedAt, setUpdatedAt] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    api.adminOverview().then((d) => setBus(d.operations || [])).catch((err) => setError(err.message || 'Failed to load business units'));
    // PM-1: assignee picker options (Chairman-only endpoint; HO sees
    // an empty picker rather than a broken page).
    api.listUsers().then((d) => setUsers(d.users || [])).catch(() => setUsers([]));
    // PM-2: recipient count for the manual missing-report button.
    api.missingReportCount().then((d) => setMissingCount(d.count)).catch(() => setMissingCount(null));
  }, []);

  const load = useCallback(async () => {
    setError('');
    setRefreshing(true);
    try {
      const bu = buId ? Number(buId) : undefined;
      if (tab === 'actions') setActions((await api.listActionItems({ business_unit_id: bu })).items);
      if (tab === 'documents') setDocuments((await api.listDocuments({ business_unit_id: bu })).items);
      if (tab === 'staff') setStaff((await api.listStaff({ business_unit_id: bu, days: 14 })).items);
      if (tab === 'customers') setCustomers((await api.listCustomers({ business_unit_id: bu, days: 14 })).items);
      if (tab === 'forecast') setForecast(await api.forecast(bu));
      // SM-5: the "Updated HH:MM" timestamp only moves on a successful
      // refresh — a failed load keeps the old (honest) timestamp.
      setUpdatedAt(new Date());
    } catch (e) {
      setError(e.message || 'Failed to load');
    } finally {
      setRefreshing(false);
    }
  }, [tab, buId]);

  useEffect(() => { load(); }, [load]);

  // m-09: re-run the load when the tab regains focus (no polling).
  useRefreshOnFocus(load);

  async function addAction(e) {
    e.preventDefault();
    if (!newTitle.trim()) return;
    // PM-17: a BU is required — creating with none selected used to
    // 400 only on submit, with no upfront signal.
    if (!buId) {
      setError('Pick a business unit first — action items belong to one business unit.');
      return;
    }
    try {
      await api.createActionItem({
        business_unit_id: Number(buId),
        title: newTitle.trim(),
        due_date: newDue || undefined,
        // PM-1: assignee picker (users of the selected BU).
        assigned_to_id: newAssignee ? Number(newAssignee) : undefined,
      });
      setNewTitle('');
      setNewDue('');
      setNewAssignee('');
      load();
    } catch (err) { setError(err.message); }
  }

  // PM-18: these handlers used to await with no try/catch — a failed
  // click silently did nothing (contrast remindActions, which catches).
  async function setActionStatus(item, status) {
    try {
      await api.updateActionItem(item.id, { status });
      load();
    } catch (err) { setError(err.message || 'Failed to update action item'); }
  }

  // PM-1: assign/reassign an existing item from its row.
  async function setActionAssignee(item, assigneeId) {
    try {
      await api.updateActionItem(item.id, { assigned_to_id: assigneeId ? Number(assigneeId) : undefined });
      load();
    } catch (err) { setError(err.message || 'Failed to assign action item'); }
  }

  async function deleteActionItem(item) {
    try {
      await api.deleteActionItem(item.id);
      load();
    } catch (err) { setError(err.message || 'Failed to delete action item'); }
  }

  async function remind() {
    try {
      const d = await api.remindMissingReports();
      setNotice(`Reminder sent to ${d.notified} CEO(s) who have not reported today.`);
      setTimeout(() => setNotice(''), 5000);
      api.missingReportCount().then((c) => setMissingCount(c.count)).catch(() => {});
    } catch (err) { setError(err.message || 'Failed to send reminders'); }
  }

  // M-15: the remind-actions trigger had no frontend caller (curl-only).
  async function remindActions() {
    try {
      const d = await api.remindActionItems();
      setNotice(`Action-item reminders sent to ${d.notified} assignee(s) (${d.checked} due/overdue checked).`);
      setTimeout(() => setNotice(''), 5000);
    } catch (err) { setError(err.message); }
  }

  async function addDocument(e) {
    e.preventDefault();
    if (!docForm.title.trim() || !docForm.expiry_date || !buId) {
      setError('Pick a business unit, enter a title and an expiry date');
      return;
    }
    try {
      await api.createDocument({
        business_unit_id: Number(buId),
        title: docForm.title.trim(),
        document_type: docForm.document_type,
        expiry_date: docForm.expiry_date,
        notes: docForm.notes || undefined,
      });
      setDocForm({ title: '', document_type: 'license', expiry_date: '', notes: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function checkExpiry() {
    try {
      const d = await api.checkDocumentExpiry();
      setNotice(`Expiry check done — ${d.checked} document(s) expiring soon, ${d.notified} notification(s) sent.`);
      setTimeout(() => setNotice(''), 5000);
    } catch (err) { setError(err.message); }
  }

  const buName = (id) => bus.find((b) => b.bu_id === id)?.bu_name || `BU ${id}`;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />
      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-xl tracking-tight">Daily Operations</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Action items, documents, staff, customers &amp; forecast</p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <select value={buId} onChange={(e) => setBuId(e.target.value)} className="text-sm font-semibold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2">
              <option value="">All Business Units</option>
              {bus.map((b) => <option key={b.bu_id} value={b.bu_id}>{b.bu_name}</option>)}
            </select>
            <button onClick={remind} className="whitespace-nowrap text-xs font-semibold bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 px-3.5 py-2 rounded-lg hover:bg-amber-500/20 transition-colors" title={missingCount === null ? 'Remind CEOs who have not reported today' : `Would notify ${missingCount} CEO(s) who have not reported today`}>
              Remind missing reports{missingCount !== null ? ` (${missingCount})` : ''}
            </button>
            <button onClick={remindActions} className="whitespace-nowrap text-xs font-semibold bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 px-3.5 py-2 rounded-lg hover:bg-amber-500/20 transition-colors">
              Remind due action items
            </button>
            {/* SM-5: visible freshness + manual refresh for these lists. */}
            <LastUpdated updatedAt={updatedAt} onRefresh={load} refreshing={refreshing} />
            <div className="flex items-center gap-1.5 flex-wrap">
              <input type="month" value={reportMonth} onChange={(e) => setReportMonth(e.target.value)} className="text-xs font-semibold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-2.5 py-2" />
              <a href={api.monthlyReportUrl(reportMonth)} className="whitespace-nowrap text-xs font-semibold bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 px-3.5 py-2 rounded-lg hover:opacity-90 transition-opacity">
                Monthly report (PDF)
              </a>
              <a href={api.opsExportUrl(tab === 'forecast' ? 'actions' : tab, buId)} className="whitespace-nowrap text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white px-3.5 py-2 rounded-lg transition-colors">
                Export Excel
              </a>
            </div>
          </div>
        </header>

        {notice && <div className="text-sm font-medium text-emerald-700 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-2.5">{notice}</div>}
        {error && <div className="text-sm font-medium text-red-700 dark:text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2.5">{error}</div>}

        <div className="tab-track flex-wrap w-fit">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)} className={`tab-pill ${tab === t.key ? 'tab-pill-active' : ''}`}>
              {t.label}
              {t.key === 'documents' && documents.filter((d) => d.days_left !== null && d.days_left <= 30).length > 0 && <span className="ml-1.5 text-[10px] font-bold bg-red-500 text-white rounded-full px-1.5 py-0.5">{documents.filter((d) => d.days_left !== null && d.days_left <= 30).length}</span>}
            </button>
          ))}
        </div>

        {tab === 'actions' && (
          <section className={`${GLASS} p-6`}>
            <form onSubmit={addAction} className="flex flex-wrap gap-3 mb-5">
              <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="New action item…" className="flex-1 min-w-[220px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              <input type="date" value={newDue} onChange={(e) => setNewDue(e.target.value)} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
              {/* PM-1: assignee picker — users of the selected BU. */}
              <select value={newAssignee} onChange={(e) => setNewAssignee(e.target.value)} disabled={!buId} title={buId ? 'Assign to a user in this business unit' : 'Pick a business unit first'} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm disabled:opacity-50">
                <option value="">Unassigned</option>
                {users.filter((u) => u.is_active && String(u.business_unit_id) === String(buId)).map((u) => (
                  <option key={u.id} value={u.id}>{u.full_name || u.username} ({u.role})</option>
                ))}
              </select>
              <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add</button>
            </form>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {actions.map((a) => (
                <li key={a.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                  <div className="min-w-0">
                    <p className={`text-sm font-semibold text-slate-800 dark:text-slate-200 ${a.status === 'done' ? 'line-through opacity-60' : ''}`}>{a.title}</p>
                    <p className="text-xs text-slate-400 dark:text-slate-500">{buName(a.business_unit_id)}{a.due_date ? ` · due ${a.due_date}` : ''}{a.assigned_to_name ? ` · ${a.assigned_to_name}` : ''}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[a.status]}`}>{a.status.replace('_', ' ')}</span>
                    {/* PM-1: assign / reassign from the row. */}
                    <select value={a.assigned_to_id || ''} onChange={(e) => e.target.value && setActionAssignee(a, e.target.value)} title="Assign or reassign" className="text-xs bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-2 py-1.5">
                      <option value="" disabled>{a.assigned_to_id ? 'Reassign…' : 'Assign…'}</option>
                      {users.filter((u) => u.is_active && String(u.business_unit_id) === String(a.business_unit_id)).map((u) => (
                        <option key={u.id} value={u.id}>{u.full_name || u.username} ({u.role})</option>
                      ))}
                    </select>
                    {a.status !== 'done' && <button onClick={() => setActionStatus(a, 'in_progress')} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Start</button>}
                    {a.status !== 'done' && <button onClick={() => setActionStatus(a, 'done')} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Done</button>}
                    <button onClick={() => deleteActionItem(a)} className="text-xs font-semibold text-red-500 hover:underline">Delete</button>
                  </div>
                </li>
              ))}
              {actions.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No action items yet</li>}
            </ul>
          </section>
        )}

        {tab === 'documents' && (
          <section className={`${GLASS} p-6`}>
            <form onSubmit={addDocument} className="flex flex-wrap gap-3 mb-5">
              <input value={docForm.title} onChange={(e) => setDocForm({ ...docForm, title: e.target.value })} placeholder="Document title (select a BU first)" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              <select value={docForm.document_type} onChange={(e) => setDocForm({ ...docForm, document_type: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                <option value="license">License</option>
                <option value="contract">Contract</option>
                <option value="permit">Permit</option>
                <option value="other">Other</option>
              </select>
              <input type="date" value={docForm.expiry_date} onChange={(e) => setDocForm({ ...docForm, expiry_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input value={docForm.notes} onChange={(e) => setDocForm({ ...docForm, notes: e.target.value })} placeholder="Notes (optional)" className="flex-1 min-w-[160px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
              <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add</button>
            </form>
            <div className="flex justify-end mb-3">
              <button onClick={checkExpiry} className="text-xs font-semibold bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 px-3.5 py-2 rounded-lg hover:bg-red-500/20 transition-colors">
                Send expiry alerts
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[560px]">
                <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  <tr><th className="py-2 pr-3">Document</th><th className="py-2 pr-3">Type</th><th className="py-2 pr-3">BU</th><th className="py-2 pr-3 text-right">Expires</th><th className="py-2 text-right">Status</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {documents.map((d) => (
                    <tr key={d.id}>
                      <td className="py-2.5 pr-3 font-semibold text-slate-800 dark:text-slate-200">{d.title}
                        {d.notes && <span className="block text-xs font-normal text-slate-400">{d.notes}</span>}
                      </td>
                      <td className="py-2.5 pr-3 text-slate-500 dark:text-slate-400 capitalize">{d.document_type}</td>
                      <td className="py-2.5 pr-3 text-slate-500 dark:text-slate-400">{d.bu_name}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums">{d.expiry_date}</td>
                      <td className="py-2.5 text-right">
                        {d.days_left !== null && d.days_left < 0 && <span className="text-[10px] font-bold bg-red-500 text-white rounded-full px-2 py-0.5">EXPIRED</span>}
                        {d.days_left !== null && d.days_left >= 0 && d.days_left <= 30 && <span className="text-[10px] font-bold bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 rounded-full px-2 py-0.5">{d.days_left}d left</span>}
                        {(d.days_left === null || d.days_left > 30) && <span className="text-[10px] font-bold bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 rounded-full px-2 py-0.5">OK</span>}
                        <button onClick={async () => { await api.deleteDocument(d.id); load(); }} className="ml-2 text-xs font-semibold text-slate-400 hover:text-red-500">Remove</button>
                      </td>
                    </tr>
                  ))}
                  {documents.length === 0 && <tr><td colSpan={5} className="py-8 text-center text-slate-400">No documents yet</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === 'staff' && (
          <section className={`${GLASS} p-6 overflow-x-auto`}>
            <table className="w-full text-sm min-w-[560px]">
              <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                <tr><th className="py-2 pr-3">Date</th><th className="py-2 pr-3">BU</th><th className="py-2 pr-3 text-right">Total</th><th className="py-2 pr-3 text-right">Present</th><th className="py-2 pr-3 text-right">Absent</th><th className="py-2 pr-3 text-right">Hires</th><th className="py-2 text-right">Left</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {staff.map((s) => (
                  <tr key={s.id}>
                    <td className="py-2.5 pr-3 tabular-nums">{s.report_date}</td>
                    <td className="py-2.5 pr-3">{buName(s.business_unit_id)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{s.total_staff}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-emerald-600 dark:text-emerald-400">{s.present}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-red-500">{s.absent}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{s.new_hires}</td>
                    <td className="py-2.5 text-right tabular-nums">{s.resignations}</td>
                  </tr>
                ))}
                {staff.length === 0 && <tr><td colSpan={7} className="py-8 text-center text-slate-400">No staff records in the last 14 days</td></tr>}
              </tbody>
            </table>
          </section>
        )}

        {tab === 'customers' && (
          <section className={`${GLASS} p-6 overflow-x-auto`}>
            <table className="w-full text-sm min-w-[560px]">
              <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                <tr><th className="py-2 pr-3">Date</th><th className="py-2 pr-3">BU</th><th className="py-2 pr-3 text-right">Footfall</th><th className="py-2 pr-3 text-right">Transactions</th><th className="py-2 pr-3 text-right">Complaints</th><th className="py-2 text-right">Satisfaction</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {customers.map((c) => (
                  <tr key={c.id}>
                    <td className="py-2.5 pr-3 tabular-nums">{c.report_date}</td>
                    <td className="py-2.5 pr-3">{buName(c.business_unit_id)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{c.footfall ?? '—'}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{c.transactions ?? '—'}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{c.complaints}</td>
                    <td className="py-2.5 text-right tabular-nums">{c.satisfaction_score ? `${c.satisfaction_score} / 5` : '—'}</td>
                  </tr>
                ))}
                {customers.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-slate-400">No customer metrics in the last 14 days</td></tr>}
              </tbody>
            </table>
          </section>
        )}

        {tab === 'forecast' && forecast && (
          <section className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { label: `MTD Revenue (${forecast.days_elapsed}/${forecast.days_in_month} days)`, value: money(forecast.mtd_revenue) },
              { label: 'Projected Month-End Revenue', value: money(forecast.projected_revenue) },
              { label: `MTD Cash Flow`, value: money(forecast.mtd_cash_flow) },
              { label: 'Projected Month-End Cash Flow', value: money(forecast.projected_cash_flow) },
            ].map((c) => (
              <div key={c.label} className={`${GLASS} p-5`}>
                <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">{c.label}</p>
                <p className="font-bold text-slate-900 dark:text-slate-100 text-[24px] leading-tight mt-1.5 tabular-nums">{c.value}</p>
                {/* PM-16: which BU (or the whole group) these cards cover. */}
                <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-1">{forecast.business_unit_name || 'All Business Units (group total)'} · {forecast.month} · {forecast.reporting_days} reporting days</p>
              </div>
            ))}
          </section>
        )}

        <Footer />
      </main>
    </div>
  );
}
