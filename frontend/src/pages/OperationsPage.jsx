import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import DatePicker from '../components/DatePicker';
import Footer from '../components/Footer';
import QuadrantStatusCircle from '../components/QuadrantStatusCircle';
import { BUDGET_BADGE_CLASS, BUDGET_STATUS_LABEL, formatVariance, money } from '../utils/quadrantMetrics';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

export default function OperationsPage() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  useEffect(() => {
    api.adminOperations().then(setData);
  }, []);

  const filtered = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    return data.operations.filter((op) => {
      const matchesSearch = !query || `${op.bu_code} ${op.bu_name} ${op.ceo_username}`.toLowerCase().includes(query);
      let matchesFilter = true;
      if (filter === 'red') {
        matchesFilter = [op.financial_status, op.social_status, op.activity_status, op.health_status].includes('red');
      }
      if (filter === 'missing') matchesFilter = !op.submitted_today;
      return matchesSearch && matchesFilter;
    });
  }, [data, search, filter]);

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  const filterBtnClass = (key) =>
    `text-xs font-semibold px-3.5 py-2 rounded-lg transition-colors ${
      filter === key ? 'bg-brand-600 text-white' : 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 hover:text-slate-700 dark:hover:text-slate-300'
    }`;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-8 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-xl tracking-tight">Operations Grid</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{data.today_display}</p>
          </div>
          <a
            href={api.exportExcelUrl()}
            className="flex items-center gap-2 text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white px-3.5 py-2.5 rounded-xl transition-colors shadow-lg shadow-emerald-600/20"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Export Master Excel
          </a>
        </header>

        {/* DATA EXPORT */}
        <section className={`${GLASS} p-6`}>
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Data Export</p>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Download Master Data</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Export a single day, a custom date range, or the full historical dataset as Excel</p>
            </div>
            <div className="flex items-end gap-3 flex-wrap">
              <div>
                <label className="block text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">From</label>
                <DatePicker value={startDate} onChange={setStartDate} max={data.today_iso} placeholder="Start date" className="w-36" />
              </div>
              <div>
                <label className="block text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">To</label>
                <DatePicker value={endDate} onChange={setEndDate} max={data.today_iso} placeholder="End date" className="w-36" />
              </div>
              <a
                href={api.exportExcelUrl(startDate, endDate)}
                className="flex items-center gap-2 text-xs font-semibold bg-brand-600 hover:bg-brand-700 text-white px-3.5 py-2.5 rounded-lg transition-colors shadow-lg shadow-brand-600/20"
              >
                Export Range
              </a>
              <a
                href={api.exportExcelUrl(data.today_iso, data.today_iso)}
                className="text-xs font-semibold bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 px-3.5 py-2.5 rounded-lg transition-colors"
              >
                Export Today Only
              </a>
              <a href={api.exportExcelUrl()} className="text-xs font-semibold text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300 px-2 py-2.5 transition-colors">
                Export Full History
              </a>
            </div>
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Live Dataset</p>
              <h1 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Interactive Operations Grid</h1>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Latest submission per business unit &middot; click a row for full details</p>
              <div className="flex items-center gap-3 flex-wrap mt-2 text-[11px] text-slate-500 dark:text-slate-400">
                <span className="font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wide">Status circle:</span>
                <span>Top-Left Business Performance &middot; Top-Right Social Activity &middot; Bottom-Left Daily Activities &middot; Bottom-Right Health and Fitness</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500" />Good</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-500" />Warning</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500" />Critical</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-slate-300 dark:bg-slate-600" />No Data</span>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="text"
                placeholder="Search BU, CEO..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 px-3.5 py-2 text-sm w-full sm:w-56 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              />
              <button onClick={() => setFilter('all')} className={filterBtnClass('all')}>View All</button>
              <button onClick={() => setFilter('red')} className={filterBtnClass('red')}>View Critical</button>
              <button onClick={() => setFilter('missing')} className={filterBtnClass('missing')}>Missing Today</button>
            </div>
          </div>
          <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="sticky top-0 z-[5] bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Business Unit</th>
                  <th className="px-6 py-3 text-left">CEO</th>
                  <th className="px-6 py-3 text-left">Last Report</th>
                  <th className="px-6 py-3 text-right">Revenue</th>
                  <th className="px-6 py-3 text-right">Cash Flow</th>
                  <th className="px-6 py-3 text-right">Receivable</th>
                  <th className="px-6 py-3 text-right">Payable</th>
                  <th className="px-6 py-3 text-center">Status</th>
                  <th className="px-6 py-3 text-center">Budget</th>
                  <th className="px-6 py-3 text-right">Cumulative Net Revenue</th>
                  <th className="px-6 py-3 text-center">Achievement %</th>
                  <th className="px-6 py-3 text-right">Variance</th>
                  <th className="px-6 py-3 text-center">Today</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filtered.map((op) => (
                  <tr key={op.bu_id} className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors cursor-pointer" onClick={() => navigate(`/admin/business-unit/${op.bu_id}`)}>
                    <td className="px-6 py-3.5">
                      <p className="font-semibold text-slate-800 dark:text-slate-200">{op.bu_name}</p>
                    </td>
                    <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{op.ceo_username}</td>
                    <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400">
                      {op.report_date || '—'}
                      {op.report_time && <span className="block text-[11px] text-slate-400 dark:text-slate-500">{op.report_time}</span>}
                    </td>
                    <td className="px-6 py-3.5 text-right font-medium tabular-nums text-slate-700 dark:text-slate-300">{op.revenue != null ? money(op.revenue) : '—'}</td>
                    <td className="px-6 py-3.5 text-right font-medium tabular-nums text-slate-700 dark:text-slate-300">{op.cash_flow != null ? money(op.cash_flow) : '—'}</td>
                    <td className="px-6 py-3.5 text-right font-medium tabular-nums text-slate-700 dark:text-slate-300">{op.receivable != null ? money(op.receivable) : '—'}</td>
                    <td className="px-6 py-3.5 text-right font-medium tabular-nums text-slate-700 dark:text-slate-300">{op.payable != null ? money(op.payable) : '—'}</td>
                    <td className="px-6 py-3.5 text-center">
                      <QuadrantStatusCircle
                        financial={op.financial_status}
                        social={op.social_status}
                        activity={op.activity_status}
                        health={op.health_status}
                        size={32}
                      />
                    </td>
                    <td className="px-6 py-3.5 text-center">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-1 rounded-full border ${BUDGET_BADGE_CLASS[op.budget_status] || BUDGET_BADGE_CLASS.grey}`}>
                        {BUDGET_STATUS_LABEL[op.budget_status] || BUDGET_STATUS_LABEL.grey}
                      </span>
                    </td>
                    <td className="px-6 py-3.5 text-right font-medium tabular-nums text-slate-700 dark:text-slate-300">{op.cumulative_net_revenue != null ? money(op.cumulative_net_revenue) : '—'}</td>
                    <td className="px-6 py-3.5 text-center font-semibold tabular-nums text-slate-700 dark:text-slate-300">{op.achievement_pct != null ? `${op.achievement_pct}%` : '—'}</td>
                    <td className={`px-6 py-3.5 text-right font-medium tabular-nums ${op.variance_amount > 0 ? 'text-emerald-600 dark:text-emerald-400' : op.variance_amount < 0 ? 'text-red-600 dark:text-red-400' : 'text-slate-500 dark:text-slate-400'}`}>{formatVariance(op.variance_amount)}</td>
                    <td className="px-6 py-3.5 text-center">
                      {op.submitted_today ? (
                        <>
                          <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 px-2.5 py-1 rounded-full">Submitted</span>
                          {op.submitted_today_time && <span className="block text-[11px] text-emerald-500 dark:text-emerald-400 mt-0.5">{op.submitted_today_time}</span>}
                        </>
                      ) : (
                        <span className="text-xs font-semibold text-red-600 dark:text-red-400 bg-red-500/10 border border-red-500/20 dark:border-red-500/30 px-2.5 py-1 rounded-full">Missing</span>
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
                <p className="text-sm font-semibold text-slate-500 dark:text-slate-400">No business units match this filter</p>
                <p className="text-xs text-slate-400 dark:text-slate-500 mt-1">Try a different search term or switch back to "View All"</p>
              </div>
            )}
          </div>
        </section>
        <Footer />
      </main>
    </div>
  );
}
