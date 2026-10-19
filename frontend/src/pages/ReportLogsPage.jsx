import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import DatePicker from '../components/DatePicker';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

function formatReportDate(iso) {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

export default function ReportLogsPage() {
  const [data, setData] = useState(null);
  const [search, setSearch] = useState('');
  const [dateFilter, setDateFilter] = useState(null);
  const [buFilter, setBuFilter] = useState('all');

  useEffect(() => {
    api.reportLogs().then(setData);
  }, []);

  const buOptions = useMemo(() => {
    if (!data) return [];
    const seen = new Map();
    for (const log of data.logs) {
      if (!seen.has(log.bu_code)) seen.set(log.bu_code, log.bu_name);
    }
    return [...seen.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [data]);

  const filtered = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    return data.logs.filter((log) => {
      const matchesSearch = !query || `${log.bu_code} ${log.bu_name} ${log.username}`.toLowerCase().includes(query);
      const matchesDate = !dateFilter || log.report_date === dateFilter;
      const matchesBu = buFilter === 'all' || log.bu_code === buFilter;
      return matchesSearch && matchesDate && matchesBu;
    });
  }, [data, search, dateFilter, buFilter]);

  function clearFilters() {
    setSearch('');
    setDateFilter(null);
    setBuFilter('all');
  }

  const hasActiveFilters = search.trim() !== '' || dateFilter || buFilter !== 'all';

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 dark:border-slate-700/70 flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Audit Trail</p>
              <h1 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Report Submission Logs</h1>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {hasActiveFilters ? (
                  <>{filtered.length} of {data.total_logs} submissions match your filters</>
                ) : (
                  <>{data.total_logs} submission{data.total_logs !== 1 ? 's' : ''} recorded</>
                )}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="text"
                placeholder="Search BU, submitter..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white/70 dark:bg-slate-800/60 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 px-3.5 py-2 text-sm w-full sm:w-56 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              />
              <select
                value={buFilter}
                onChange={(e) => setBuFilter(e.target.value)}
                className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white/70 dark:bg-slate-800/60 text-slate-700 dark:text-slate-300 px-3.5 py-2.5 text-sm w-full sm:w-44 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              >
                <option value="all">All Business Units</option>
                {buOptions.map(([code, name]) => (
                  <option key={code} value={code}>{name}</option>
                ))}
              </select>
              <DatePicker value={dateFilter} onChange={setDateFilter} placeholder="Filter by date" className="w-full sm:w-40" />
              {hasActiveFilters && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="text-xs font-semibold text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 px-2.5 py-2 rounded-lg transition-colors whitespace-nowrap"
                >
                  Clear filters
                </button>
              )}
            </div>
          </div>
          <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns, including What Changed
          </p>
          <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="sticky top-0 z-[5] bg-white/90 dark:bg-slate-800/90 backdrop-blur text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Submitted At</th>
                  <th className="px-6 py-3 text-left">Report Date</th>
                  <th className="px-6 py-3 text-left">Business Unit</th>
                  <th className="px-6 py-3 text-left">Submitted By</th>
                  <th className="px-6 py-3 text-left">What Changed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filtered.map((log, i) => (
                  <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-800/60 transition-colors">
                    <td className="px-6 py-3.5 font-medium tabular-nums text-slate-700 dark:text-slate-300">{log.timestamp_display}</td>
                    <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400">{formatReportDate(log.report_date)}</td>
                    <td className="px-6 py-3.5">
                      <p className="font-semibold text-slate-800 dark:text-slate-200">{log.bu_name}</p>
                    </td>
                    <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{log.username}</td>
                    <td className="px-6 py-3.5 text-xs">
                      {log.is_initial ? (
                        <span className="text-slate-400 dark:text-slate-500 italic">Initial submission</span>
                      ) : log.changes.length > 0 ? (
                        <span className="text-slate-600 dark:text-slate-400">
                          {log.changes.map((c, j) => (
                            <span key={j} className="inline-block mr-2 whitespace-nowrap">
                              {j > 0 && <span className="mr-2 text-slate-300 dark:text-slate-600">&middot;</span>}
                              <span className="font-medium text-slate-700 dark:text-slate-300">{c.field}</span>{' '}
                              {c.old} &rarr; {c.new}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <span className="text-slate-400 dark:text-slate-500 italic">Resubmitted, no changes</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filtered.length === 0 && (
              <div className="text-center py-14">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-8 h-8 text-slate-300 dark:text-slate-600 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                </svg>
                <p className="text-sm font-semibold text-slate-500 dark:text-slate-400">No submissions match this filter</p>
                <p className="text-xs text-slate-400 dark:text-slate-500 mt-1">Try a different search term</p>
              </div>
            )}
          </div>
        </section>
        <Footer />
      </main>
    </div>
  );
}
