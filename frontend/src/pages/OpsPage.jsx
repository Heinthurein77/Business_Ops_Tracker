import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const TABS = [
  { key: 'actions', label: 'Action Items' },
  { key: 'inventory', label: 'Inventory' },
  { key: 'expenses', label: 'Expenses' },
  { key: 'staff', label: 'Staff' },
  { key: 'customers', label: 'Customers' },
  { key: 'forecast', label: 'Forecast' },
];

const STATUS_BADGE = {
  pending: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  in_progress: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
  done: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  approved: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  rejected: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
};

function money(v) {
  return `Ks ${Number(v || 0).toLocaleString()}`;
}

export default function OpsPage() {
  const [tab, setTab] = useState('actions');
  const [bus, setBus] = useState([]);
  const [buId, setBuId] = useState('');

  const [actions, setActions] = useState([]);
  const [newTitle, setNewTitle] = useState('');
  const [newDue, setNewDue] = useState('');

  const [inventory, setInventory] = useState([]);
  const [lowCount, setLowCount] = useState(0);
  const [invForm, setInvForm] = useState({ name: '', reorder_level: '', initial_stock: '', unit: 'pcs' });
  const [moveQty, setMoveQty] = useState({});

  const [expenses, setExpenses] = useState([]);
  const [staff, setStaff] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [forecast, setForecast] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    api.adminOverview().then((d) => setBus(d.operations || [])).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setError('');
    try {
      const bu = buId ? Number(buId) : undefined;
      if (tab === 'actions') setActions((await api.listActionItems({ business_unit_id: bu })).items);
      if (tab === 'inventory') {
        const d = await api.listInventory({ business_unit_id: bu });
        setInventory(d.items);
        setLowCount(d.low_count);
      }
      if (tab === 'expenses') setExpenses((await api.listExpenses({ business_unit_id: bu })).items);
      if (tab === 'staff') setStaff((await api.listStaff({ business_unit_id: bu, days: 14 })).items);
      if (tab === 'customers') setCustomers((await api.listCustomers({ business_unit_id: bu, days: 14 })).items);
      if (tab === 'forecast') setForecast(await api.forecast(bu));
    } catch (e) {
      setError(e.message || 'Failed to load');
    }
  }, [tab, buId]);

  useEffect(() => { load(); }, [load]);

  async function addAction(e) {
    e.preventDefault();
    if (!newTitle.trim()) return;
    try {
      await api.createActionItem({ business_unit_id: buId ? Number(buId) : undefined, title: newTitle.trim(), due_date: newDue || undefined });
      setNewTitle('');
      setNewDue('');
      load();
    } catch (err) { setError(err.message); }
  }

  async function setActionStatus(item, status) {
    await api.updateActionItem(item.id, { status });
    load();
  }

  async function addInventory(e) {
    e.preventDefault();
    if (!invForm.name.trim() || !buId) { setError('Pick a business unit and enter an item name'); return; }
    try {
      await api.createInventoryItem({
        business_unit_id: Number(buId),
        name: invForm.name.trim(),
        reorder_level: Number(invForm.reorder_level) || 0,
        initial_stock: Number(invForm.initial_stock) || 0,
        unit: invForm.unit || 'pcs',
      });
      setInvForm({ name: '', reorder_level: '', initial_stock: '', unit: 'pcs' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function recordMove(item, type) {
    const qty = Number(moveQty[item.id]);
    if (!qty) return;
    await api.recordStockMovement(item.id, { movement_type: type, quantity: qty });
    setMoveQty((m) => ({ ...m, [item.id]: '' }));
    load();
  }

  async function review(exp, decision) {
    await api.reviewExpense(exp.id, { decision });
    load();
  }

  async function remind() {
    const d = await api.remindMissingReports();
    setNotice(`Reminder sent to ${d.notified} CEO(s) who have not reported today.`);
    setTimeout(() => setNotice(''), 5000);
  }

  const buName = (id) => bus.find((b) => b.bu_id === id)?.bu_name || `BU ${id}`;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />
      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-lg">Operations Center</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Action items, inventory, expenses, staff, customers & forecast</p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <select value={buId} onChange={(e) => setBuId(e.target.value)} className="text-sm font-semibold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2">
              <option value="">All Business Units</option>
              {bus.map((b) => <option key={b.bu_id} value={b.bu_id}>{b.bu_name}</option>)}
            </select>
            <button onClick={remind} className="text-xs font-semibold bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 px-3.5 py-2 rounded-lg hover:bg-amber-500/20 transition-colors">
              Remind missing reports
            </button>
          </div>
        </header>

        {notice && <div className="text-sm font-medium text-emerald-700 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-2.5">{notice}</div>}
        {error && <div className="text-sm font-medium text-red-700 dark:text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2.5">{error}</div>}

        <div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 rounded-lg p-1 flex-wrap w-fit">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)} className={`text-xs font-semibold px-3.5 py-1.5 rounded-md transition-colors whitespace-nowrap ${tab === t.key ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm' : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300'}`}>
              {t.label}
              {t.key === 'inventory' && lowCount > 0 && <span className="ml-1.5 text-[10px] font-bold bg-red-500 text-white rounded-full px-1.5 py-0.5">{lowCount}</span>}
              {t.key === 'expenses' && expenses.filter((e) => e.status === 'pending').length > 0 && <span className="ml-1.5 text-[10px] font-bold bg-amber-500 text-white rounded-full px-1.5 py-0.5">{expenses.filter((e) => e.status === 'pending').length}</span>}
            </button>
          ))}
        </div>

        {tab === 'actions' && (
          <section className={`${GLASS} p-6`}>
            <form onSubmit={addAction} className="flex flex-wrap gap-3 mb-5">
              <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="New action item…" className="flex-1 min-w-[220px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              <input type="date" value={newDue} onChange={(e) => setNewDue(e.target.value)} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
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
                    {a.status !== 'done' && <button onClick={() => setActionStatus(a, 'in_progress')} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Start</button>}
                    {a.status !== 'done' && <button onClick={() => setActionStatus(a, 'done')} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Done</button>}
                    <button onClick={async () => { await api.deleteActionItem(a.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Delete</button>
                  </div>
                </li>
              ))}
              {actions.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No action items yet</li>}
            </ul>
          </section>
        )}

        {tab === 'inventory' && (
          <section className={`${GLASS} p-6`}>
            <form onSubmit={addInventory} className="flex flex-wrap gap-3 mb-5">
              <input value={invForm.name} onChange={(e) => setInvForm({ ...invForm, name: e.target.value })} placeholder="Item name (select a BU first)" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              <input type="number" value={invForm.initial_stock} onChange={(e) => setInvForm({ ...invForm, initial_stock: e.target.value })} placeholder="Stock" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input type="number" value={invForm.reorder_level} onChange={(e) => setInvForm({ ...invForm, reorder_level: e.target.value })} placeholder="Reorder at" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input value={invForm.unit} onChange={(e) => setInvForm({ ...invForm, unit: e.target.value })} placeholder="Unit" className="w-20 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add item</button>
            </form>
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[640px]">
                <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  <tr><th className="py-2 pr-3">Item</th><th className="py-2 pr-3">BU</th><th className="py-2 pr-3 text-right">Stock</th><th className="py-2 pr-3 text-right">Reorder at</th><th className="py-2">Move stock</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {inventory.map((i) => (
                    <tr key={i.id}>
                      <td className="py-2.5 pr-3 font-semibold text-slate-800 dark:text-slate-200">{i.name} {i.is_low && <span className="ml-1 text-[10px] font-bold bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 rounded-full px-2 py-0.5">LOW</span>}</td>
                      <td className="py-2.5 pr-3 text-slate-500 dark:text-slate-400">{buName(i.business_unit_id)}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums font-semibold">{i.current_stock} {i.unit}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-slate-500">{i.reorder_level}</td>
                      <td className="py-2.5">
                        <div className="flex items-center gap-1.5">
                          <input type="number" value={moveQty[i.id] || ''} onChange={(e) => setMoveQty((m) => ({ ...m, [i.id]: e.target.value }))} placeholder="Qty" className="w-20 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs" />
                          <button onClick={() => recordMove(i, 'in')} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">+ In</button>
                          <button onClick={() => recordMove(i, 'out')} className="text-xs font-semibold text-red-500 hover:underline">− Out</button>
                          <button onClick={async () => { await api.deleteInventoryItem(i.id); load(); }} className="text-xs font-semibold text-slate-400 hover:underline">Remove</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {inventory.length === 0 && <tr><td colSpan={5} className="py-8 text-center text-slate-400">No inventory items yet</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === 'expenses' && (
          <section className={`${GLASS} p-6`}>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {expenses.map((e) => (
                <li key={e.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{e.title} <span className="text-slate-400 font-normal">· {money(e.amount)}</span></p>
                    <p className="text-xs text-slate-400 dark:text-slate-500">{e.bu_name} · by {e.submitted_by_name}{e.category ? ` · ${e.category}` : ''}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[e.status]}`}>{e.status}</span>
                    {e.status === 'pending' && <>
                      <button onClick={() => review(e, 'approved')} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Approve</button>
                      <button onClick={() => review(e, 'rejected')} className="text-xs font-semibold text-red-500 hover:underline">Reject</button>
                    </>}
                  </div>
                </li>
              ))}
              {expenses.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No expense requests</li>}
            </ul>
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
                <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-1">{forecast.month} · {forecast.reporting_days} reporting days</p>
              </div>
            ))}
          </section>
        )}
        <Footer />
      </main>
    </div>
  );
}
