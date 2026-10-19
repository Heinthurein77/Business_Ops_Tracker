import { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

/** Split out of the old all-in-one CeoDashboardPage — a dedicated page for
 * this CEO's own submission log, mirroring the admin section's Report Logs
 * page but scoped to just their own business unit. */
export default function CeoReportLogsPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.ceoDashboard().then(setData).catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load report logs'));
  }, []);

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 pb-24">
        {error && (
          <div className="mb-6 rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-sm text-red-700">{error}</div>
        )}

        <div className="mb-6">
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Audit Trail</p>
          <h1 className="font-bold text-slate-900 tracking-tight text-lg">My Report Logs</h1>
          <p className="text-xs text-slate-500 mt-0.5">Your own submission history, with exact submission time</p>
        </div>

        <section className={`${GLASS} overflow-hidden`}>
          <p className="lg:hidden px-6 pt-4 text-[11px] font-medium text-slate-400 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="bg-white text-slate-500 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Report Date</th>
                  <th className="px-6 py-3 text-left">Submitted At</th>
                  <th className="px-6 py-3 text-left">Meetings Attended</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.report_logs.map((log, i) => (
                  <tr key={i} className="hover:bg-slate-50 transition-colors">
                    <td className="px-6 py-3 text-slate-600">{log.report_date}</td>
                    <td className="px-6 py-3 text-slate-500">{log.submitted_at || '—'}</td>
                    <td className="px-6 py-3 text-slate-400 max-w-xs truncate">{log.meetings_attended || '—'}</td>
                  </tr>
                ))}
                {data.report_logs.length === 0 && (
                  <tr><td colSpan={3} className="text-center text-sm text-slate-400 py-10">No submissions recorded yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
        <Footer light />
      </main>
    </div>
  );
}
