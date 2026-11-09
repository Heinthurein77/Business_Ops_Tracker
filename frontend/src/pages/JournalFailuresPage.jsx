import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import LastUpdated from '../components/LastUpdated';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

/** SC-1: admin view over operations whose journal never posted (POS sales,
 * sales/purchase returns, formal stock transfers, expense approvals, daily
 * reports). The same detection as the daily reconciliation job
 * (src/jobs/reconcile_journals.py). Each row can be re-posted in place via
 * POST /api/accounting/journal/retry {"source", "source_id"} — already
 * posted returns already_posted:true, unknown source 404, still
 * unpostable 409. */
export default function JournalFailuresPage() {
  const [failures, setFailures] = useState([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [updatedAt, setUpdatedAt] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [retrying, setRetrying] = useState(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const d = await api.journalFailures();
      setFailures(d.failures || []);
      setTotal(d.total || 0);
      setError('');
      setUpdatedAt(new Date());
    } catch (err) {
      setError(err.message || 'Failed to load journal failures');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function retry(f) {
    setRetrying(f.source + ':' + f.source_id);
    setNotice('');
    setError('');
    try {
      const out = await api.retryJournal(f.source, f.source_id);
      if (out && out.already_posted) {
        setNotice(`${f.label} already has a posted journal — removed from this list.`);
      } else {
        setNotice(`${f.label} re-posted to the journal.`);
      }
      await load();
    } catch (err) {
      // 409 = still unpostable (e.g. CoA not provisioned, period closed);
      // 404 = the source record is gone. Both are shown as-is — the row
      // stays in the list until a retry succeeds.
      setError(err.message || `Could not re-post ${f.label}`);
    } finally {
      setRetrying(null);
    }
  }

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />
      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-xl tracking-tight">Journal Failures</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Operations that completed but whose journal never posted. The books are wrong until each one is re-posted.
            </p>
          </div>
          <LastUpdated updatedAt={updatedAt} onRefresh={load} refreshing={refreshing} />
        </header>

        {notice && <div className="text-sm font-medium text-emerald-700 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-2.5">{notice}</div>}
        {error && <div className="text-sm font-medium text-red-700 dark:text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2.5">{error}</div>}

        <section className={`${GLASS} p-6`}>
          {failures.length === 0 && !error && (
            <p className="py-8 text-center text-sm text-slate-400">
              No operations with a missing journal. The daily reconciliation job also checks this list every morning.
            </p>
          )}
          {failures.length > 0 && (
            <>
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">
                {total} operation{total === 1 ? '' : 's'} with no posted journal.
              </p>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {failures.map((f) => (
                  <li key={`${f.source}:${f.source_id}`} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{f.label}</p>
                      <p className="text-xs text-slate-400 dark:text-slate-500">
                        {f.bu_name}{f.date ? ` · ${f.date}` : ''}{f.amount ? ` · Ks ${Number(f.amount).toLocaleString()}` : ''}
                      </p>
                      <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">{f.detail}</p>
                    </div>
                    <button
                      onClick={() => retry(f)}
                      disabled={retrying === f.source + ':' + f.source_id}
                      className="text-xs font-semibold bg-brand-600 hover:bg-brand-700 text-white px-3.5 py-2 rounded-lg transition-colors disabled:opacity-50"
                    >
                      {retrying === f.source + ':' + f.source_id ? 'Re-posting…' : 'Re-post journal'}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="text-xs text-slate-400 dark:text-slate-500 mt-4">
            <Link to="/accounting" className="font-semibold text-brand-600 dark:text-brand-400 hover:underline">Open the journal</Link>
            {' '}to verify a re-posted entry landed.
          </p>
        </section>
        <Footer />
      </main>
    </div>
  );
}
