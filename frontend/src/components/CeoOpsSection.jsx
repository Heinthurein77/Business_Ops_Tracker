import { useCallback, useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import { GLASS } from '../utils/glassTheme';
import { localTodayISO } from '../utils/format';
import { Field, inputClass } from './FormFields';

const STATUS_BADGE = {
  pending: 'bg-amber-500/10 border-amber-500/20 text-amber-600',
  in_progress: 'bg-sky-500/10 border-sky-500/20 text-sky-600',
  done: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600',
  approved: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600',
  rejected: 'bg-red-500/10 border-red-500/20 text-red-600',
};

export default function CeoOpsSection() {
  const [tasks, setTasks] = useState([]);
  const [staffForm, setStaffForm] = useState({ total_staff: '', present: '', absent: '', new_hires: '', resignations: '', notes: '' });
  const [custForm, setCustForm] = useState({ footfall: '', transactions: '', complaints: '', satisfaction_score: '', notes: '' });
  // PM-3: the CEO's own documents — previously only on Daily Ops
  // (Chairman/HO), so a document-expiry notification sent a CEO to a
  // page they could not open. Listed/created here, on /ceo.
  const [documents, setDocuments] = useState([]);
  const [docForm, setDocForm] = useState({ title: '', document_type: 'license', expiry_date: '', notes: '' });
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [completingId, setCompletingId] = useState(null);
  const [staffSaving, setStaffSaving] = useState(false);
  const [custSaving, setCustSaving] = useState(false);
  // F-m4: local date, not UTC — before 06:30 Yangon time the UTC date is
  // still yesterday.
  const today = localTodayISO();

  const load = useCallback(async () => {
    try {
      setTasks((await api.listActionItems({ mine: true })).items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load action items');
    }
    // PM-3: the CEO's documents for their own BU (server-scoped).
    try {
      setDocuments((await api.listDocuments()).items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load documents');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function flash(msg) {
    setNotice(msg);
    setTimeout(() => setNotice(''), 4000);
  }

  function showError(err, fallback) {
    setError(err instanceof ApiError ? err.message : fallback);
  }

  // F-M3: previously these three handlers awaited the API with no
  // try/catch, no pending state and no double-submit guard — a failed
  // save looked identical to a saved one. Now each surfaces its error,
  // disables its button while pending, and (for staff/customers) sends
  // the same Idempotency-Key the client already generates for action
  // items/documents (see api/client.js).
  async function completeTask(t) {
    setCompletingId(t.id);
    setError('');
    try {
      await api.updateActionItem(t.id, { status: 'done' });
      await load();
    } catch (err) {
      showError(err, 'Failed to mark the task done');
    } finally {
      setCompletingId(null);
    }
  }

  async function submitStaff(e) {
    e.preventDefault();
    setStaffSaving(true);
    setError('');
    try {
      await api.submitStaff({
        report_date: today,
        total_staff: Number(staffForm.total_staff) || 0,
        present: Number(staffForm.present) || 0,
        absent: Number(staffForm.absent) || 0,
        // PM-15: hires/resignations/notes were accepted by the API and
        // displayed (Hires/Left) on Daily Ops, but no CEO form ever
        // collected them — the columns could only ever read 0.
        new_hires: Number(staffForm.new_hires) || 0,
        resignations: Number(staffForm.resignations) || 0,
        notes: staffForm.notes || undefined,
      });
      flash('Staff record saved.');
    } catch (err) {
      showError(err, 'Failed to save staff record');
    } finally {
      setStaffSaving(false);
    }
  }

  async function submitCustomers(e) {
    e.preventDefault();
    setCustSaving(true);
    setError('');
    try {
      await api.submitCustomers({
        report_date: today,
        footfall: custForm.footfall ? Number(custForm.footfall) : undefined,
        transactions: custForm.transactions ? Number(custForm.transactions) : undefined,
        complaints: Number(custForm.complaints) || 0,
        satisfaction_score: custForm.satisfaction_score ? Number(custForm.satisfaction_score) : undefined,
        // PM-15: customer notes, same gap as staff notes above.
        notes: custForm.notes || undefined,
      });
      flash('Customer metrics saved.');
    } catch (err) {
      showError(err, 'Failed to save customer metrics');
    } finally {
      setCustSaving(false);
    }
  }

  // PM-3: add a document for the CEO's own BU (the server pins a CEO
  // to their own BU, so no business_unit_id is sent).
  async function submitDocument(e) {
    e.preventDefault();
    setError('');
    if (!docForm.title.trim() || !docForm.expiry_date) {
      setError('Enter a document title and an expiry date.');
      return;
    }
    try {
      await api.createDocument({
        title: docForm.title.trim(),
        document_type: docForm.document_type,
        expiry_date: docForm.expiry_date,
        notes: docForm.notes || undefined,
      });
      setDocForm({ title: '', document_type: 'license', expiry_date: '', notes: '' });
      await load();
      flash('Document saved.');
    } catch (err) {
      showError(err, 'Failed to save document');
    }
  }

  async function deleteDocument(doc) {
    setError('');
    try {
      await api.deleteDocument(doc.id);
      await load();
    } catch (err) {
      showError(err, 'Failed to delete document');
    }
  }

  return (
    <div className="space-y-8 mb-8">
      {notice && <div className="text-sm font-medium text-emerald-700 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-2.5">{notice}</div>}
      {error && <div className="text-sm font-medium text-red-700 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2.5">{error}</div>}

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
                {t.status !== 'done' && (
                  <button type="button" onClick={() => completeTask(t)} disabled={completingId === t.id} className="text-xs font-semibold text-emerald-600 hover:underline disabled:opacity-50">
                    {completingId === t.id ? 'Saving…' : 'Mark done'}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <div className="grid lg:grid-cols-2 gap-8">
        <section className={`${GLASS} p-6`}>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Team</p>
          <h2 className="font-bold text-slate-900 tracking-tight text-lg mb-4">Staff Today</h2>
          <form onSubmit={submitStaff} className="space-y-3">
            <Field label="Total staff">{(fieldId) => <input id={fieldId} type="number" className={inputClass} value={staffForm.total_staff} onChange={(e) => setStaffForm({ ...staffForm, total_staff: e.target.value })} />}</Field>
            <Field label="Present">{(fieldId) => <input id={fieldId} type="number" className={inputClass} value={staffForm.present} onChange={(e) => setStaffForm({ ...staffForm, present: e.target.value })} />}</Field>
            <Field label="Absent">{(fieldId) => <input id={fieldId} type="number" className={inputClass} value={staffForm.absent} onChange={(e) => setStaffForm({ ...staffForm, absent: e.target.value })} />}</Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="New hires">{(fieldId) => <input id={fieldId} type="number" className={inputClass} value={staffForm.new_hires} onChange={(e) => setStaffForm({ ...staffForm, new_hires: e.target.value })} />}</Field>
              <Field label="Resignations">{(fieldId) => <input id={fieldId} type="number" className={inputClass} value={staffForm.resignations} onChange={(e) => setStaffForm({ ...staffForm, resignations: e.target.value })} />}</Field>
            </div>
            <Field label="Notes (optional)">{(fieldId) => <input id={fieldId} type="text" className={inputClass} value={staffForm.notes} onChange={(e) => setStaffForm({ ...staffForm, notes: e.target.value })} />}</Field>
            <button type="submit" disabled={staffSaving} className="w-full bg-slate-900 hover:bg-slate-800 text-white text-sm font-semibold py-2.5 rounded-lg transition-colors disabled:opacity-50">{staffSaving ? 'Saving…' : 'Save staff record'}</button>
          </form>
        </section>

        <section className={`${GLASS} p-6`}>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Customers</p>
          <h2 className="font-bold text-slate-900 tracking-tight text-lg mb-4">Customers Today</h2>
          <form onSubmit={submitCustomers} className="space-y-3">
            <Field label="Footfall">{(fieldId) => <input id={fieldId} type="number" className={inputClass} value={custForm.footfall} onChange={(e) => setCustForm({ ...custForm, footfall: e.target.value })} />}</Field>
            <Field label="Transactions">{(fieldId) => <input id={fieldId} type="number" className={inputClass} value={custForm.transactions} onChange={(e) => setCustForm({ ...custForm, transactions: e.target.value })} />}</Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Complaints">{(fieldId) => <input id={fieldId} type="number" className={inputClass} value={custForm.complaints} onChange={(e) => setCustForm({ ...custForm, complaints: e.target.value })} />}</Field>
              <Field label="Satisfaction (1–5)">{(fieldId) => <input id={fieldId} type="number" step="0.1" min="1" max="5" className={inputClass} value={custForm.satisfaction_score} onChange={(e) => setCustForm({ ...custForm, satisfaction_score: e.target.value })} />}</Field>
            </div>
            <Field label="Notes (optional)">{(fieldId) => <input id={fieldId} type="text" className={inputClass} value={custForm.notes} onChange={(e) => setCustForm({ ...custForm, notes: e.target.value })} />}</Field>
            <button type="submit" disabled={custSaving} className="w-full bg-slate-900 hover:bg-slate-800 text-white text-sm font-semibold py-2.5 rounded-lg transition-colors disabled:opacity-50">{custSaving ? 'Saving…' : 'Save customer metrics'}</button>
          </form>
        </section>
      </div>

      {/* PM-3: the CEO's documents surface on /ceo — licences/permits
          for their own BU, with expiry status. This is where the
          document-expiry notification now links a CEO. */}
      <section className={`${GLASS} p-6`}>
        <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Documents</p>
        <h2 className="font-bold text-slate-900 tracking-tight text-lg mb-4">My Documents</h2>
        <form onSubmit={submitDocument} className="flex flex-wrap gap-3 mb-5">
          <input value={docForm.title} onChange={(e) => setDocForm({ ...docForm, title: e.target.value })} placeholder="Document title…" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
          <select value={docForm.document_type} onChange={(e) => setDocForm({ ...docForm, document_type: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
            <option value="license">License</option>
            <option value="contract">Contract</option>
            <option value="permit">Permit</option>
            <option value="other">Other</option>
          </select>
          <input type="date" value={docForm.expiry_date} onChange={(e) => setDocForm({ ...docForm, expiry_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
          <input value={docForm.notes} onChange={(e) => setDocForm({ ...docForm, notes: e.target.value })} placeholder="Notes (optional)" className="flex-1 min-w-[160px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
          <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add</button>
        </form>
        {documents.length === 0 && <p className="text-sm text-slate-400">No documents yet.</p>}
        <ul className="divide-y divide-slate-100">
          {documents.map((d) => (
            <li key={d.id} className="py-2.5 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-slate-800">{d.title}</p>
                <p className="text-xs text-slate-400 capitalize">{d.document_type} · expires {d.expiry_date || '—'}{d.days_left !== null && d.days_left !== undefined ? ` (${d.days_left < 0 ? 'expired' : `${d.days_left}d left`})` : ''}</p>
                {d.notes && <p className="text-xs text-slate-400">{d.notes}</p>}
              </div>
              <button type="button" onClick={() => deleteDocument(d)} className="text-xs font-semibold text-red-500 hover:underline flex-shrink-0">Remove</button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
