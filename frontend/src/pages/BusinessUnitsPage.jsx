import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const INP = 'rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm';
const BTN = 'bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors';

export default function BusinessUnitsPage() {
  const [items, setItems] = useState([]);
  const [natures, setNatures] = useState([]);
  const [form, setForm] = useState({ name: '', nature: 'retail' });
  const [editName, setEditName] = useState({}); // bu_id -> name being edited
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    try {
      const d = await api.adminBusinessUnits();
      setItems(d.items);
      setNatures(d.natures);
    } catch (e) {
      setError(e.message || 'Failed to load');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function flash(msg) {
    setNotice(msg);
    setTimeout(() => setNotice(''), 5000);
  }

  async function create(e) {
    e.preventDefault();
    setError('');
    try {
      const r = await api.adminCreateBU({ name: form.name.trim(), nature: form.nature });
      flash(`Created ${r.code} — ${r.name} with a ${r.nature_label} chart of accounts (${r.account_count} accounts).`);
      setForm({ name: '', nature: 'retail' });
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function saveName(bu) {
    const name = (editName[bu.id] ?? bu.name).trim();
    if (!name || name === bu.name) { setEditName((m) => ({ ...m, [bu.id]: undefined })); return; }
    setError('');
    try {
      await api.adminUpdateBU(bu.id, { name });
      flash(`Renamed ${bu.code} to "${name}".`);
      setEditName((m) => ({ ...m, [bu.id]: undefined }));
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function setNature(bu, nature) {
    setError('');
    try {
      const r = await api.adminUpdateBU(bu.id, { nature });
      flash(`${bu.code} — ${bu.name}: ${r.note || `nature set to ${r.nature_label}`}`);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />
      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <div>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Group Structure</p>
          <h1 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-xl">Business Units</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Add new businesses, rename units, and set each unit's business nature. A new unit gets its chart of accounts automatically from the nature template — then add its CEO in User Management and opening balances in Accounting → Setup.</p>
        </div>

        {error && <div className="rounded-xl border border-red-300/60 bg-red-50 dark:bg-red-500/10 px-4 py-3 text-sm text-red-600 dark:text-red-400" role="alert">{error}</div>}
        {notice && <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400" role="status">{notice}</p>}

        <section className={`${GLASS} p-6`}>
          <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">New Business Unit</h2>
          <form onSubmit={create} className="flex flex-wrap gap-3">
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Business name (e.g. ABC Mart — Mandalay)" className={`flex-1 min-w-[240px] ${INP}`} />
            <select value={form.nature} onChange={(e) => setForm({ ...form, nature: e.target.value })} title="Business nature — decides the chart of accounts template" className={INP}>
              {natures.map((n) => <option key={n.key} value={n.key}>{n.label}</option>)}
            </select>
            <button type="submit" className={BTN}>Create unit</button>
          </form>
        </section>

        <section className={`${GLASS} p-6`}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[760px]">
              <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                <tr><th className="py-2 pr-3">Code</th><th className="py-2 pr-3">Name</th><th className="py-2 pr-3">Nature</th><th className="py-2 pr-3">CEO</th><th className="py-2 pr-3 text-right">Users</th><th className="py-2 pr-3">Books</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {items.map((bu) => (
                  <tr key={bu.id}>
                    <td className="py-2.5 pr-3 font-mono text-xs text-slate-500">{bu.code}</td>
                    <td className="py-2.5 pr-3">
                      <div className="flex items-center gap-2">
                        <input
                          value={editName[bu.id] ?? bu.name}
                          onChange={(e) => setEditName((m) => ({ ...m, [bu.id]: e.target.value }))}
                          onBlur={() => saveName(bu)}
                          onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                          className="flex-1 min-w-[160px] rounded-lg border border-transparent hover:border-slate-200 dark:hover:border-slate-700 focus:border-brand-500 bg-transparent px-2 py-1.5 text-sm font-medium focus:outline-none focus:bg-white dark:focus:bg-slate-900"
                        />
                        {(editName[bu.id] !== undefined && editName[bu.id] !== bu.name) && <span className="text-[10px] font-bold text-amber-500 uppercase">unsaved</span>}
                      </div>
                    </td>
                    <td className="py-2.5 pr-3">
                      <select value={bu.nature || ''} onChange={(e) => setNature(bu, e.target.value)} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs">
                        <option value="" disabled>Set nature…</option>
                        {natures.map((n) => <option key={n.key} value={n.key}>{n.label}</option>)}
                      </select>
                    </td>
                    <td className="py-2.5 pr-3 text-slate-500 dark:text-slate-400">{bu.ceo_name || '—'}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{bu.user_count}</td>
                    <td className="py-2.5">
                      {bu.has_books
                        ? <span className="text-[10px] font-bold uppercase tracking-wide bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 rounded-full px-2 py-0.5">CoA · {bu.account_count}</span>
                        : <span className="text-[10px] font-bold uppercase tracking-wide bg-slate-500/10 border border-slate-500/20 text-slate-500 rounded-full px-2 py-0.5">No books</span>}
                    </td>
                  </tr>
                ))}
                {items.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-slate-400">No business units yet</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="mt-4 text-xs text-slate-400">Renaming: edit the name and click away to save. Setting a nature on a unit without books creates its chart of accounts; on a unit with books it only updates the label.</p>
        </section>
        <Footer />
      </main>
    </div>
  );
}
