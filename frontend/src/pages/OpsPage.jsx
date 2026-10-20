import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const TABS = [
  { key: 'actions', label: 'Action Items' },
  { key: 'inventory', label: 'Inventory' },
  { key: 'purchasing', label: 'Purchasing' },
  { key: 'suppliers', label: 'Suppliers' },
  { key: 'invoices', label: 'Invoices' },
  { key: 'hr', label: 'HR & Payroll' },
  { key: 'expenses', label: 'Expenses' },
  { key: 'documents', label: 'Documents' },
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
  draft: 'bg-slate-500/10 border-slate-500/20 text-slate-600 dark:text-slate-300',
  sent: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
  partial: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  overdue: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
  ordered: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
  received: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  paid: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  cancelled: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
  open: 'bg-slate-500/10 border-slate-500/20 text-slate-600 dark:text-slate-300',
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
  const [transferTo, setTransferTo] = useState({});

  const [expenses, setExpenses] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [docForm, setDocForm] = useState({ title: '', document_type: 'license', expiry_date: '', notes: '' });
  const [reportMonth, setReportMonth] = useState(new Date().toISOString().slice(0, 7));
  const [staff, setStaff] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [forecast, setForecast] = useState(null);
  const [suppliers, setSuppliers] = useState([]);
  const [supForm, setSupForm] = useState({ name: '', contact_person: '', phone: '' });
  const [pos, setPos] = useState([]);
  const [poForm, setPoForm] = useState({ supplier_id: '', order_date: new Date().toISOString().slice(0, 10), lines: [{ item_name: '', quantity: '', unit_cost: '' }] });
  const [salesCustomers, setSalesCustomers] = useState([]);
  const [custForm, setCustForm] = useState({ name: '', phone: '' });
  const [invoices, setInvoices] = useState([]);
  const [invCreate, setInvCreate] = useState({ customer_id: '', invoice_date: new Date().toISOString().slice(0, 10), due_date: '', tax_rate: 5, lines: [{ description: '', quantity: 1, unit_price: '' }] });
  const [payAmt, setPayAmt] = useState({});
  const [agingData, setAgingData] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [empForm, setEmpForm] = useState({ name: '', position: '', base_salary: '' });
  const [leaveList, setLeaveList] = useState([]);
  const [leaveForm, setLeaveForm] = useState({ employee_id: '', leave_type: 'annual', start_date: '', end_date: '' });
  const [payrollRuns, setPayrollRuns] = useState([]);
  const [payrollPeriod, setPayrollPeriod] = useState(new Date().toISOString().slice(0, 7));
  const [lineEdits, setLineEdits] = useState({});
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
      if (tab === 'documents') setDocuments((await api.listDocuments({ business_unit_id: bu })).items);
      if (tab === 'staff') setStaff((await api.listStaff({ business_unit_id: bu, days: 14 })).items);
      if (tab === 'customers') setCustomers((await api.listCustomers({ business_unit_id: bu, days: 14 })).items);
      if (tab === 'forecast') setForecast(await api.forecast(bu));
      if (tab === 'suppliers') setSuppliers((await api.listSuppliers(bu)).items);
      if (tab === 'purchasing') {
        setPos((await api.listPOs(bu)).items);
        setSuppliers((await api.listSuppliers(bu)).items);
      }
      if (tab === 'invoices') {
        setInvoices((await api.listInvoices(bu)).items);
        setSalesCustomers((await api.listSalesCustomers(bu)).items);
        setAgingData(await api.aging(bu));
      }
      if (tab === 'hr') {
        setEmployees((await api.listEmployees(bu)).items);
        setLeaveList((await api.listLeave(bu)).items);
        setPayrollRuns((await api.listPayroll(bu)).items);
      }
    } catch (e) {
      setError(e.message || 'Failed to load');
    }
  }, [tab, buId]);

  useEffect(() => { load(); }, [load]);

  async function addEmployee(e) {
    e.preventDefault();
    if (!empForm.name.trim() || !buId) return;
    try {
      await api.createEmployee({ business_unit_id: Number(buId), name: empForm.name.trim(), position: empForm.position || undefined, base_salary: Number(empForm.base_salary) || 0 });
      setEmpForm({ name: '', position: '', base_salary: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function submitLeave(e) {
    e.preventDefault();
    if (!leaveForm.employee_id || !leaveForm.start_date || !leaveForm.end_date) return;
    try {
      await api.createLeave({ employee_id: Number(leaveForm.employee_id), leave_type: leaveForm.leave_type, start_date: leaveForm.start_date, end_date: leaveForm.end_date });
      setLeaveForm({ employee_id: '', leave_type: 'annual', start_date: '', end_date: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function generatePayroll() {
    if (!buId) return;
    try {
      await api.createPayroll({ business_unit_id: Number(buId), period: payrollPeriod });
      setNotice(`Payroll draft for ${payrollPeriod} generated.`);
      setTimeout(() => setNotice(''), 4000);
      load();
    } catch (err) { setError(err.message); }
  }

  async function addSalesCustomer(e) {
    e.preventDefault();
    if (!custForm.name.trim() || !buId) return;
    try {
      await api.createSalesCustomer({ business_unit_id: Number(buId), name: custForm.name.trim(), phone: custForm.phone || undefined });
      setCustForm({ name: '', phone: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function createInvoice(e) {
    e.preventDefault();
    const lines = invCreate.lines.filter((l) => l.description.trim()).map((l) => ({ description: l.description.trim(), quantity: Number(l.quantity) || 1, unit_price: Number(l.unit_price) || 0 }));
    if (!invCreate.customer_id || lines.length === 0 || !buId) { setError('Pick a customer and add at least one line'); return; }
    try {
      await api.createInvoice({ business_unit_id: Number(buId), customer_id: Number(invCreate.customer_id), invoice_date: invCreate.invoice_date, due_date: invCreate.due_date || undefined, tax_rate: Number(invCreate.tax_rate) || 0, lines });
      setInvCreate({ customer_id: '', invoice_date: new Date().toISOString().slice(0, 10), due_date: '', tax_rate: 5, lines: [{ description: '', quantity: 1, unit_price: '' }] });
      setNotice('Invoice created (draft). Send it to post to the ledger.');
      setTimeout(() => setNotice(''), 4000);
      load();
    } catch (err) { setError(err.message); }
  }

  async function addSupplier(e) {
    e.preventDefault();
    if (!supForm.name.trim()) return;
    try {
      await api.createSupplier({ name: supForm.name.trim(), contact_person: supForm.contact_person || undefined, phone: supForm.phone || undefined, business_unit_id: buId ? Number(buId) : undefined });
      setSupForm({ name: '', contact_person: '', phone: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function createPO(e) {
    e.preventDefault();
    const lines = poForm.lines.filter((l) => l.item_name.trim()).map((l) => ({ item_name: l.item_name.trim(), quantity: Number(l.quantity) || 0, unit_cost: Number(l.unit_cost) || 0 }));
    if (!poForm.supplier_id || lines.length === 0 || !buId) { setError('Pick a supplier and add at least one line'); return; }
    try {
      await api.createPO({ business_unit_id: Number(buId), supplier_id: Number(poForm.supplier_id), order_date: poForm.order_date, lines });
      setPoForm({ supplier_id: '', order_date: new Date().toISOString().slice(0, 10), lines: [{ item_name: '', quantity: '', unit_cost: '' }] });
      setNotice('Purchase order created (draft).');
      setTimeout(() => setNotice(''), 4000);
      load();
    } catch (err) { setError(err.message); }
  }

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

  async function recordMove(item, type, postCogs = false) {
    const qty = Number(moveQty[item.id]);
    if (!qty) return;
    try {
      await api.recordStockMovement(item.id, { movement_type: type, quantity: qty, post_cogs: postCogs });
    } catch (err) { setError(err.message); }
    setMoveQty((m) => ({ ...m, [item.id]: '' }));
    load();
  }

  async function transferItem(item) {
    const qty = Number(moveQty[item.id]);
    const dest = transferTo[item.id];
    if (!qty || !dest) { setError('Enter a quantity and pick a destination BU to transfer'); return; }
    try {
      await api.transferStock(item.id, { to_business_unit_id: Number(dest), quantity: qty });
      setNotice(`Transferred ${qty} × ${item.name}.`);
      setTimeout(() => setNotice(''), 4000);
    } catch (err) { setError(err.message); }
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
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-lg">Operations Center</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Action items, inventory, expenses, documents, staff, customers & forecast</p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <select value={buId} onChange={(e) => setBuId(e.target.value)} className="text-sm font-semibold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2">
              <option value="">All Business Units</option>
              {bus.map((b) => <option key={b.bu_id} value={b.bu_id}>{b.bu_name}</option>)}
            </select>
            <button onClick={remind} className="whitespace-nowrap text-xs font-semibold bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 px-3.5 py-2 rounded-lg hover:bg-amber-500/20 transition-colors">
              Remind missing reports
            </button>
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

        <div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 rounded-lg p-1 flex-wrap w-fit">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)} className={`text-xs font-semibold px-3.5 py-1.5 rounded-md transition-colors whitespace-nowrap ${tab === t.key ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm' : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300'}`}>
              {t.label}
              {t.key === 'inventory' && lowCount > 0 && <span className="ml-1.5 text-[10px] font-bold bg-red-500 text-white rounded-full px-1.5 py-0.5">{lowCount}</span>}
              {t.key === 'documents' && documents.filter((d) => d.days_left !== null && d.days_left <= 30).length > 0 && <span className="ml-1.5 text-[10px] font-bold bg-red-500 text-white rounded-full px-1.5 py-0.5">{documents.filter((d) => d.days_left !== null && d.days_left <= 30).length}</span>}
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
                  <tr><th className="py-2 pr-3">Item</th><th className="py-2 pr-3">BU</th><th className="py-2 pr-3 text-right">Stock</th><th className="py-2 pr-3 text-right">Avg Cost</th><th className="py-2 pr-3 text-right">Stock Value</th><th className="py-2 pr-3 text-right">Reorder at</th><th className="py-2">Move stock</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {inventory.map((i) => (
                    <tr key={i.id}>
                      <td className="py-2.5 pr-3 font-semibold text-slate-800 dark:text-slate-200">{i.name} {i.is_low && <span className="ml-1 text-[10px] font-bold bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 rounded-full px-2 py-0.5">LOW</span>}</td>
                      <td className="py-2.5 pr-3 text-slate-500 dark:text-slate-400">{buName(i.business_unit_id)}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums font-semibold">{i.current_stock} {i.unit}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-slate-500">{i.unit_cost ? money(i.unit_cost) : '—'}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-slate-500">{i.unit_cost ? money(i.current_stock * i.unit_cost) : '—'}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-slate-500">{i.reorder_level}</td>
                      <td className="py-2.5">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <input type="number" value={moveQty[i.id] || ''} onChange={(e) => setMoveQty((m) => ({ ...m, [i.id]: e.target.value }))} placeholder="Qty" className="w-20 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs" />
                          <button onClick={() => recordMove(i, 'in')} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">+ In</button>
                          <button onClick={() => recordMove(i, 'out')} className="text-xs font-semibold text-red-500 hover:underline">− Out</button>
                          <button onClick={() => recordMove(i, 'out', true)} className="text-xs font-semibold text-violet-600 dark:text-violet-400 hover:underline" title="Stock out as a sale — posts COGS to the journal at average cost">Sold (COGS)</button>
                          <select value={transferTo[i.id] || ''} onChange={(e) => setTransferTo((t) => ({ ...t, [i.id]: e.target.value }))} className="text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-1.5 py-1.5">
                            <option value="">Transfer to…</option>
                            {bus.filter((b) => b.bu_id !== i.business_unit_id).map((b) => <option key={b.bu_id} value={b.bu_id}>{b.bu_name}</option>)}
                          </select>
                          <button onClick={() => transferItem(i)} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Transfer</button>
                          <button onClick={async () => { await api.deleteInventoryItem(i.id); load(); }} className="text-xs font-semibold text-slate-400 hover:underline">Remove</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {inventory.length === 0 && <tr><td colSpan={7} className="py-8 text-center text-slate-400">No inventory items yet</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === 'purchasing' && (
          <section className={`${GLASS} p-6`}>
            <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">New Purchase Order</h2>
            <form onSubmit={createPO} className="space-y-3 mb-6">
              <div className="flex flex-wrap gap-3">
                <select value={poForm.supplier_id} onChange={(e) => setPoForm({ ...poForm, supplier_id: e.target.value })} className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Select supplier…</option>
                  {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.scope})</option>)}
                </select>
                <input type="date" value={poForm.order_date} onChange={(e) => setPoForm({ ...poForm, order_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              </div>
              {poForm.lines.map((l, idx) => (
                <div key={idx} className="flex flex-wrap gap-2">
                  <input value={l.item_name} onChange={(e) => { const lines = [...poForm.lines]; lines[idx].item_name = e.target.value; setPoForm({ ...poForm, lines }); }} placeholder="Item name" className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                  <input type="number" value={l.quantity} onChange={(e) => { const lines = [...poForm.lines]; lines[idx].quantity = e.target.value; setPoForm({ ...poForm, lines }); }} placeholder="Qty" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input type="number" value={l.unit_cost} onChange={(e) => { const lines = [...poForm.lines]; lines[idx].unit_cost = e.target.value; setPoForm({ ...poForm, lines }); }} placeholder="Unit cost" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                </div>
              ))}
              <div className="flex items-center justify-between flex-wrap gap-3">
                <button type="button" onClick={() => setPoForm({ ...poForm, lines: [...poForm.lines, { item_name: '', quantity: '', unit_cost: '' }] })} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">+ Add line</button>
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Create PO</button>
              </div>
            </form>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {pos.map((po) => (
                <li key={po.id} className="py-3">
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{po.po_no} <span className="font-normal text-slate-400">· {po.supplier_name} · {money(po.total)}</span></p>
                      <p className="text-xs text-slate-400">{po.order_date}{po.bu_name ? ` · ${po.bu_name}` : ''} · {po.lines.map((l) => `${l.item_name} ×${l.quantity}`).join(', ')}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[po.status] || STATUS_BADGE.open}`}>{po.status}</span>
                      {po.status === 'draft' && <button onClick={async () => { await api.orderPO(po.id); load(); }} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Order</button>}
                      {(po.status === 'draft' || po.status === 'ordered') && <button onClick={async () => { await api.receivePO(po.id); setNotice(`Received ${po.po_no} — stock updated, AP posted.`); setTimeout(() => setNotice(''), 4000); load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Receive</button>}
                      {po.status === 'received' && <button onClick={async () => { await api.payPO(po.id); load(); }} className="text-xs font-semibold text-violet-600 dark:text-violet-400 hover:underline">Pay</button>}
                      {(po.status === 'draft' || po.status === 'ordered') && <button onClick={async () => { await api.cancelPO(po.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Cancel</button>}
                    </div>
                  </div>
                </li>
              ))}
              {pos.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No purchase orders yet</li>}
            </ul>
          </section>
        )}

        {tab === 'suppliers' && (
          <section className={`${GLASS} p-6`}>
            <form onSubmit={addSupplier} className="flex flex-wrap gap-3 mb-5">
              <input value={supForm.name} onChange={(e) => setSupForm({ ...supForm, name: e.target.value })} placeholder="Supplier name" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
              <input value={supForm.contact_person} onChange={(e) => setSupForm({ ...supForm, contact_person: e.target.value })} placeholder="Contact person" className="w-48 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input value={supForm.phone} onChange={(e) => setSupForm({ ...supForm, phone: e.target.value })} placeholder="Phone" className="w-40 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add supplier</button>
            </form>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {suppliers.map((s) => (
                <li key={s.id} className="py-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{s.name} <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400 ml-1">{s.scope}</span></p>
                    <p className="text-xs text-slate-400">{[s.contact_person, s.phone, s.email].filter(Boolean).join(' · ') || '—'}</p>
                  </div>
                  <button onClick={async () => { await api.deleteSupplier(s.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Remove</button>
                </li>
              ))}
              {suppliers.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No suppliers yet</li>}
            </ul>
          </section>
        )}

        {tab === 'invoices' && (
          <div className="space-y-6">
            {agingData && (
              <section className="grid sm:grid-cols-2 gap-4">
                {[
                  { title: 'Receivables (AR)', data: agingData.ar, color: 'text-sky-600 dark:text-sky-400' },
                  { title: 'Payables (AP)', data: agingData.ap, color: 'text-violet-600 dark:text-violet-400' },
                ].map((card) => (
                  <div key={card.title} className={`${GLASS} p-5`}>
                    <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">{card.title} — outstanding {money(card.data.total)}</p>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2.5">
                      {Object.entries(card.data.buckets).map(([k, v]) => (
                        <p key={k} className="text-xs text-slate-500 dark:text-slate-400">{k === 'current' ? 'Current' : `${k} days`}: <span className={`font-bold tabular-nums ${v > 0 ? card.color : ''}`}>{money(v)}</span></p>
                      ))}
                    </div>
                  </div>
                ))}
              </section>
            )}
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">New Invoice</h2>
              <form onSubmit={createInvoice} className="space-y-3 mb-6">
                <div className="flex flex-wrap gap-3">
                  <select value={invCreate.customer_id} onChange={(e) => setInvCreate({ ...invCreate, customer_id: e.target.value })} className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                    <option value="">Select customer…</option>
                    {salesCustomers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                  <input type="date" value={invCreate.invoice_date} onChange={(e) => setInvCreate({ ...invCreate, invoice_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input type="date" value={invCreate.due_date} onChange={(e) => setInvCreate({ ...invCreate, due_date: e.target.value })} title="Due date" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input type="number" value={invCreate.tax_rate} onChange={(e) => setInvCreate({ ...invCreate, tax_rate: e.target.value })} title="Commercial tax %" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                </div>
                {invCreate.lines.map((l, idx) => (
                  <div key={idx} className="flex flex-wrap gap-2">
                    <input value={l.description} onChange={(e) => { const lines = [...invCreate.lines]; lines[idx].description = e.target.value; setInvCreate({ ...invCreate, lines }); }} placeholder="Description" className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                    <input type="number" value={l.quantity} onChange={(e) => { const lines = [...invCreate.lines]; lines[idx].quantity = e.target.value; setInvCreate({ ...invCreate, lines }); }} placeholder="Qty" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                    <input type="number" value={l.unit_price} onChange={(e) => { const lines = [...invCreate.lines]; lines[idx].unit_price = e.target.value; setInvCreate({ ...invCreate, lines }); }} placeholder="Unit price" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  </div>
                ))}
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <button type="button" onClick={() => setInvCreate({ ...invCreate, lines: [...invCreate.lines, { description: '', quantity: 1, unit_price: '' }] })} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">+ Add line</button>
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Create invoice</button>
                </div>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {invoices.map((inv) => (
                  <li key={inv.id} className="py-3">
                    <div className="flex items-center justify-between gap-3 flex-wrap">
                      <div>
                        <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{inv.invoice_no} <span className="font-normal text-slate-400">· {inv.customer_name} · {money(inv.total)}</span></p>
                        <p className="text-xs text-slate-400">{inv.invoice_date}{inv.due_date ? ` · due ${inv.due_date}` : ''} · paid {money(inv.paid)} · balance {money(inv.balance)}</p>
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[inv.status] || STATUS_BADGE.draft}`}>{inv.status}</span>
                        {inv.stored_status === 'draft' && <button onClick={async () => { await api.sendInvoice(inv.id); load(); }} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Send</button>}
                        {inv.stored_status === 'draft' && <button onClick={async () => { await api.cancelInvoice(inv.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Cancel</button>}
                        {(inv.stored_status === 'sent' || inv.stored_status === 'partial') && <>
                          <input type="number" value={payAmt[inv.id] || ''} onChange={(e) => setPayAmt((p) => ({ ...p, [inv.id]: e.target.value }))} placeholder="Amount" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs" />
                          <button onClick={async () => { const amt = Number(payAmt[inv.id]); if (!amt) return; try { await api.recordInvoicePayment(inv.id, { amount: amt, payment_date: new Date().toISOString().slice(0, 10), method: 'cash' }); setPayAmt((p) => ({ ...p, [inv.id]: '' })); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Record payment</button>
                        </>}
                      </div>
                    </div>
                  </li>
                ))}
                {invoices.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No invoices yet</li>}
              </ul>
            </section>
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Customers</h2>
              <form onSubmit={addSalesCustomer} className="flex flex-wrap gap-3 mb-4">
                <input value={custForm.name} onChange={(e) => setCustForm({ ...custForm, name: e.target.value })} placeholder="Customer name" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                <input value={custForm.phone} onChange={(e) => setCustForm({ ...custForm, phone: e.target.value })} placeholder="Phone" className="w-44 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add customer</button>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {salesCustomers.map((c) => (
                  <li key={c.id} className="py-2.5 flex items-center justify-between">
                    <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{c.name} <span className="text-xs text-slate-400 font-normal">{c.phone || ''}</span></p>
                    <button onClick={async () => { await api.deleteSalesCustomer(c.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Remove</button>
                  </li>
                ))}
                {salesCustomers.length === 0 && <li className="py-6 text-center text-sm text-slate-400">No customers yet</li>}
              </ul>
            </section>
          </div>
        )}

        {tab === 'hr' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Employees</h2>
              <form onSubmit={addEmployee} className="flex flex-wrap gap-3 mb-4">
                <input value={empForm.name} onChange={(e) => setEmpForm({ ...empForm, name: e.target.value })} placeholder="Employee name" className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                <input value={empForm.position} onChange={(e) => setEmpForm({ ...empForm, position: e.target.value })} placeholder="Position" className="w-44 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={empForm.base_salary} onChange={(e) => setEmpForm({ ...empForm, base_salary: e.target.value })} placeholder="Monthly salary" className="w-40 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add employee</button>
              </form>
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[480px]">
                  <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    <tr><th className="py-2 pr-3">Name</th><th className="py-2 pr-3">Position</th><th className="py-2 pr-3 text-right">Monthly Salary</th><th className="py-2" /></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {employees.map((e) => (
                      <tr key={e.id}>
                        <td className="py-2 pr-3 font-medium">{e.name}</td>
                        <td className="py-2 pr-3 text-slate-500">{e.position || '—'}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{money(e.base_salary)}</td>
                        <td className="py-2 text-right"><button onClick={async () => { await api.deleteEmployee(e.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Remove</button></td>
                      </tr>
                    ))}
                    {employees.length === 0 && <tr><td colSpan={4} className="py-6 text-center text-slate-400">No employees yet for this BU</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>

            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Leave Requests</h2>
              <form onSubmit={submitLeave} className="flex flex-wrap gap-3 mb-4">
                <select value={leaveForm.employee_id} onChange={(e) => setLeaveForm({ ...leaveForm, employee_id: e.target.value })} className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Select employee…</option>
                  {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                </select>
                <select value={leaveForm.leave_type} onChange={(e) => setLeaveForm({ ...leaveForm, leave_type: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  {['annual', 'sick', 'casual', 'unpaid'].map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
                <input type="date" value={leaveForm.start_date} onChange={(e) => setLeaveForm({ ...leaveForm, start_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="date" value={leaveForm.end_date} onChange={(e) => setLeaveForm({ ...leaveForm, end_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Request leave</button>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {leaveList.map((lv) => (
                  <li key={lv.id} className="py-2.5 flex items-center justify-between gap-3 flex-wrap">
                    <p className="text-sm"><span className="font-semibold">{lv.employee_name}</span> <span className="text-slate-400">· {lv.leave_type} · {lv.start_date} → {lv.end_date} ({lv.days}d)</span></p>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[lv.status] || STATUS_BADGE.pending}`}>{lv.status}</span>
                      {lv.status === 'pending' && <>
                        <button onClick={async () => { await api.reviewLeave(lv.id, { decision: 'approved' }); load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Approve</button>
                        <button onClick={async () => { await api.reviewLeave(lv.id, { decision: 'rejected' }); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Reject</button>
                      </>}
                    </div>
                  </li>
                ))}
                {leaveList.length === 0 && <li className="py-6 text-center text-sm text-slate-400">No leave requests</li>}
              </ul>
            </section>

            <section className={`${GLASS} p-6`}>
              <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
                <h2 className="font-bold text-slate-900 dark:text-slate-100">Payroll</h2>
                <div className="flex items-center gap-2">
                  <input type="month" value={payrollPeriod} onChange={(e) => setPayrollPeriod(e.target.value)} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm font-semibold" />
                  <button onClick={generatePayroll} className="whitespace-nowrap bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors">Generate run</button>
                </div>
              </div>
              {payrollRuns.map((run) => (
                <div key={run.id} className="border border-slate-200 dark:border-slate-700 rounded-xl p-4 mb-4">
                  <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
                    <p className="text-sm font-bold text-slate-900 dark:text-slate-100">{run.period} <span className="font-normal text-slate-400">· gross {money(run.total_gross)} · deductions {money(run.total_deductions)} · net {money(run.total_net)}</span></p>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[run.status] || STATUS_BADGE.draft}`}>{run.status}</span>
                      {run.status === 'draft' && <button onClick={async () => { try { await api.approvePayroll(run.id); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Approve</button>}
                      {run.status === 'approved' && <button onClick={async () => { try { await api.payPayroll(run.id); } catch (err) { setError(err.message); } setNotice(`Payroll ${run.period} paid — journal posted.`); setTimeout(() => setNotice(''), 4000); load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Pay (post journal)</button>}
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm min-w-[560px]">
                      <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                        <tr><th className="py-1.5 pr-3">Employee</th><th className="py-1.5 pr-3 text-right">Base</th><th className="py-1.5 pr-3 text-right">Allowances</th><th className="py-1.5 pr-3 text-right">Deductions</th><th className="py-1.5 text-right">Net</th></tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                        {run.lines.map((l) => (
                          <tr key={l.id}>
                            <td className="py-1.5 pr-3 font-medium">{l.employee_name}</td>
                            <td className="py-1.5 pr-3 text-right tabular-nums">{money(l.base_salary)}</td>
                            <td className="py-1.5 pr-3 text-right">
                              {run.status === 'draft' ? (
                                <input type="number" defaultValue={l.allowances || ''} placeholder="0" onBlur={async (e) => { const v = Number(e.target.value) || 0; if (v !== l.allowances) { await api.updatePayrollLine(run.id, l.id, { allowances: v }); load(); } }} className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1 text-xs text-right" />
                              ) : <span className="tabular-nums">{money(l.allowances)}</span>}
                            </td>
                            <td className="py-1.5 pr-3 text-right">
                              {run.status === 'draft' ? (
                                <input type="number" defaultValue={l.deductions || ''} placeholder="0" onBlur={async (e) => { const v = Number(e.target.value) || 0; if (v !== l.deductions) { await api.updatePayrollLine(run.id, l.id, { deductions: v }); load(); } }} className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1 text-xs text-right" />
                              ) : <span className="tabular-nums">{money(l.deductions)}</span>}
                            </td>
                            <td className="py-1.5 text-right tabular-nums font-semibold">{money(l.net)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
              {payrollRuns.length === 0 && <p className="text-sm text-slate-400 text-center py-4">No payroll runs yet — generate one for this month</p>}
            </section>
          </div>
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
