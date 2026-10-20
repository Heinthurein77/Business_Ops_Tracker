import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { GLASS } from '../utils/glassTheme';

const STATUS_BADGE = {
  pending: 'bg-amber-500/10 border-amber-500/20 text-amber-600',
  in_progress: 'bg-sky-500/10 border-sky-500/20 text-sky-600',
  done: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600',
  approved: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600',
  rejected: 'bg-red-500/10 border-red-500/20 text-red-600',
};

const inputCls = 'w-full rounded-lg border border-slate-200 bg-white text-slate-800 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent';
const labelCls = 'block text-sm font-medium text-slate-700 mb-1.5';

export default function CeoOpsSection() {
  const [tasks, setTasks] = useState([]);
  const [expenses, setExpenses] = useState([]);
  const [expForm, setExpForm] = useState({ title: '', amount: '', category: '' });
  const [staffForm, setStaffForm] = useState({ total_staff: '', present: '', absent: '' });
  const [custForm, setCustForm] = useState({ footfall: '', transactions: '', complaints: '', satisfaction_score: '' });
  const [notice, setNotice] = useState('');
  const today = new Date().toISOString().slice(0, 10);

  const load = useCallback(async () => {
    try {
      setTasks((await api.listActionItems({ mine: true })).items);
      setExpenses((await api.listExpenses({})).items.slice(0, 5));
    } catch { /* silent */ }
  }, []);

  useEffect(() => { load(); }, [load]);

  function flash(msg) {
    setNotice(msg);
    setTimeout(() => setNotice(''), 4000);
  }

  async function completeTask(t) {
    await api.updateActionItem(t.id, { status: 'done' });
    load();
  }

  async function submitExpense(e) {
    e.preventDefault();
    if (!expForm.title.trim() || !expForm.amount) return;
    await api.submitExpense({ title: expForm.title.trim(), amount: Number(expForm.amount), category: expForm.category || undefined });
    setExpForm({ title: '', amount: '', category: '' });
    flash('Expense submitted for approval.');
    load();
  }

  async function submitStaff(e) {
    e.preventDefault();
    await api.submitStaff({
      report_date: today,
      total_staff: Number(staffForm.total_staff) || 0,
      present: Number(staffForm.present) || 0,
      absent: Number(staffForm.absent) || 0,
    });
    flash('Staff record saved.');
  }

  async function submitCustomers(e) {
    e.preventDefault();
    await api.submitCustomers({
      report_date: today,
      footfall: custForm.footfall ? Number(custForm.footfall) : undefined,
      transactions: custForm.transactions ? Number(custForm.transactions) : undefined,
      complaints: Number(custForm.complaints) || 0,
      satisfaction_score: custForm.satisfaction_score ? Number(custForm.satisfaction_score) : undefined,
    });
    flash('Customer metrics saved.');
  }

  return (
    <div className="space-y-8 mb-8">
      {notice && <div className="text-sm font-medium text-emerald-700 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-2.5">{notice}</div>}

      <section className={`${GLASS} p-6`}>
        <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Follow-ups</p>
        <h2 className="font-bold text-slate-900 tracking-tight text-lg mb-4">My Action Items</h2>
        {tasks.length === 0 && <p className="text-sm text-slate-400">No action items assigned to you.</p>}
        <ul className="divide-y divide-slate-100">
          {tasks.map((t) => (
            <li key={t.id} className="py-2.5 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className={`text-sm font-semibold text-slate-800 ${t.status === 'done' ? 'line-through opacity-60' : ''}`}>{t.title}</p>
                <p className="text-xs text-slate-400">{t.due_date ? `Due ${t.due_date}` : 'No due date'}</p>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[t.status]}`}>{t.status.replace('_', ' ')}</span>
                {t.status !== 'done' && <button onClick={() => completeTask(t)} className="text-xs font-semibold text-emerald-600 hover:underline">Mark done</button>}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <div className="grid lg:grid-cols-3 gap-8">
        <section className={`${GLASS} p-6`}>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Approvals</p>
          <h2 className="font-bold text-slate-900 tracking-tight text-lg mb-4">Submit Expense</h2>
          <form onSubmit={submitExpense} className="space-y-3">
            <div><label className={labelCls}>Title</label><input className={inputCls} value={expForm.title} onChange={(e) => setExpForm({ ...expForm, title: e.target.value })} placeholder="e.g. Delivery fuel" /></div>
            <div><label className={labelCls}>Amount (Ks)</label><input type="number" className={inputCls} value={expForm.amount} onChange={(e) => setExpForm({ ...expForm, amount: e.target.value })} placeholder="0" /></div>
            <div><label className={labelCls}>Category</label><input className={inputCls} value={expForm.category} onChange={(e) => setExpForm({ ...expForm, category: e.target.value })} placeholder="transport / supplies / other" /></div>
            <button type="submit" className="w-full bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold py-2.5 rounded-lg transition-colors">Submit for approval</button>
          </form>
          {expenses.length > 0 && (
            <ul className="mt-4 space-y-1.5">
              {expenses.map((e) => (
                <li key={e.id} className="flex items-center justify-between text-xs">
                  <span className="text-slate-600 truncate">{e.title} · Ks {Number(e.amount).toLocaleString()}</span>
                  <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border flex-shrink-0 ${STATUS_BADGE[e.status]}`}>{e.status}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className={`${GLASS} p-6`}>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Team</p>
          <h2 className="font-bold text-slate-900 tracking-tight text-lg mb-4">Staff Today</h2>
          <form onSubmit={submitStaff} className="space-y-3">
            <div><label className={labelCls}>Total staff</label><input type="number" className={inputCls} value={staffForm.total_staff} onChange={(e) => setStaffForm({ ...staffForm, total_staff: e.target.value })} /></div>
            <div><label className={labelCls}>Present</label><input type="number" className={inputCls} value={staffForm.present} onChange={(e) => setStaffForm({ ...staffForm, present: e.target.value })} /></div>
            <div><label className={labelCls}>Absent</label><input type="number" className={inputCls} value={staffForm.absent} onChange={(e) => setStaffForm({ ...staffForm, absent: e.target.value })} /></div>
            <button type="submit" className="w-full bg-slate-900 hover:bg-slate-800 text-white text-sm font-semibold py-2.5 rounded-lg transition-colors">Save staff record</button>
          </form>
        </section>

        <section className={`${GLASS} p-6`}>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Customers</p>
          <h2 className="font-bold text-slate-900 tracking-tight text-lg mb-4">Customers Today</h2>
          <form onSubmit={submitCustomers} className="space-y-3">
            <div><label className={labelCls}>Footfall</label><input type="number" className={inputCls} value={custForm.footfall} onChange={(e) => setCustForm({ ...custForm, footfall: e.target.value })} /></div>
            <div><label className={labelCls}>Transactions</label><input type="number" className={inputCls} value={custForm.transactions} onChange={(e) => setCustForm({ ...custForm, transactions: e.target.value })} /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className={labelCls}>Complaints</label><input type="number" className={inputCls} value={custForm.complaints} onChange={(e) => setCustForm({ ...custForm, complaints: e.target.value })} /></div>
              <div><label className={labelCls}>Satisfaction (1–5)</label><input type="number" step="0.1" min="1" max="5" className={inputCls} value={custForm.satisfaction_score} onChange={(e) => setCustForm({ ...custForm, satisfaction_score: e.target.value })} /></div>
            </div>
            <button type="submit" className="w-full bg-slate-900 hover:bg-slate-800 text-white text-sm font-semibold py-2.5 rounded-lg transition-colors">Save customer metrics</button>
          </form>
        </section>
      </div>
    </div>
  );
}
