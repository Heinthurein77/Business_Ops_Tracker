import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import Chart from 'react-apexcharts';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { useTheme } from '../context/ThemeContext';
import { DAILY_STEP_GOAL } from '../constants';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const RISK_CARD_CLASS = {
  red: 'border-red-500/20 dark:border-red-500/30 bg-red-500/5 glow-red',
  yellow: 'border-amber-500/20 dark:border-amber-500/30 bg-amber-500/5 glow-yellow',
  green: 'border-emerald-500/20 dark:border-emerald-500/30 bg-emerald-500/5 glow-green',
  unknown: 'border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 glow-slate',
};
const RISK_DOT = { red: 'bg-red-500 glow-red', yellow: 'bg-amber-500 glow-yellow', green: 'bg-emerald-500 glow-green', unknown: 'bg-slate-400' };

export default function HealthDashboardPage() {
  const { theme } = useTheme();
  const isDark = theme === 'dark';
  const [data, setData] = useState(null);
  const [dirSearch, setDirSearch] = useState('');
  const [dirFilter, setDirFilter] = useState('all');
  const [historySearch, setHistorySearch] = useState('');

  useEffect(() => {
    api.healthDashboard().then(setData);
  }, []);

  const filteredDirectory = useMemo(() => {
    if (!data) return [];
    const query = dirSearch.trim().toLowerCase();
    return data.directory.filter((w) => {
      const matchesSearch = !query || `${w.bu_code} ${w.bu_name} ${w.ceo_username}`.toLowerCase().includes(query);
      const matchesFilter = dirFilter === 'all' || w.risk === dirFilter;
      return matchesSearch && matchesFilter;
    });
  }, [data, dirSearch, dirFilter]);

  const filteredHistory = useMemo(() => {
    if (!data) return [];
    const query = historySearch.trim().toLowerCase();
    if (!query) return data.health_history;
    return data.health_history.filter((h) => `${h.bu_code} ${h.bu_name} ${h.ceo_username}`.toLowerCase().includes(query));
  }, [data, historySearch]);

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  const donut = {
    series: [data.counts.healthy, data.counts.warning, data.counts.critical, data.counts.unknown],
    options: {
      chart: { type: 'donut', height: 220, fontFamily: 'Inter, sans-serif' },
      labels: ['Healthy', 'Fatigue Watch', 'Critical', 'No Data'],
      colors: ['#10b981', '#f59e0b', '#f43f5e', '#cbd5e1'],
      legend: { show: false },
      dataLabels: { enabled: false },
      stroke: { width: 2, colors: [isDark ? '#0f172a' : '#ffffff'] },
      plotOptions: { pie: { donut: { size: '68%', labels: { show: true, total: { show: true, label: 'CEOs', fontSize: '12px', color: isDark ? '#94a3b8' : '#64748b' } } } } },
      tooltip: { theme: isDark ? 'dark' : 'light' },
    },
  };

  const filterBtnClass = (key) =>
    `text-xs font-semibold px-3.5 py-2 rounded-lg transition-colors ${dirFilter === key ? 'bg-brand-600 text-white' : 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 hover:text-slate-700 dark:hover:text-slate-300'}`;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-8 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-lg">Health and Fitness</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{data.today_display}</p>
          </div>
        </header>

        <div className="grid lg:grid-cols-12 gap-6">
          <section className={`lg:col-span-8 ${GLASS} overflow-hidden`}>
            <div className="p-6 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-4">
              <div>
                <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Today's Briefing</p>
                <h1 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">CEO Well-being Check-in</h1>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Heart rate, sleep time, blood pressure &amp; steps submitted today</p>
              </div>
              <span className={`text-xs font-bold px-3 py-1.5 rounded-full border ${data.submitted_count === data.total_business_units ? 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400'}`}>
                {data.submitted_count} of {data.total_business_units} CEOs checked in
              </span>
            </div>
            <div className="p-6">
              {data.today_briefing.length > 0 ? (
                <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
                  {data.today_briefing.map((h, i) => (
                    <Link key={i} to={`/admin/business-unit/${h.bu_id}`} className={`rounded-xl border p-4 hover:-translate-y-0.5 transition-transform ${RISK_CARD_CLASS[h.risk]}`}>
                      <div className="flex items-center justify-between mb-2.5">
                        <div>
                          <p className="text-sm font-bold text-slate-900 dark:text-slate-100">{h.bu_name}</p>
                          <p className="text-xs text-slate-500 dark:text-slate-400">{h.ceo_username}</p>
                        </div>
                        <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">{h.submitted_at ? h.submitted_at.split(', ')[1] : ''}</span>
                      </div>
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className="bg-white dark:bg-slate-900 rounded-lg px-2.5 py-2"><p className="text-slate-500 dark:text-slate-400">Heart Rate</p><p className="font-semibold text-slate-800 dark:text-slate-200">{h.heart_rate != null ? `${h.heart_rate} bpm` : '—'}</p></div>
                        <div className="bg-white dark:bg-slate-900 rounded-lg px-2.5 py-2"><p className="text-slate-500 dark:text-slate-400">Sleep Time</p><p className="font-semibold text-slate-800 dark:text-slate-200">{h.sleep_hours != null ? `${h.sleep_hours} hrs` : '—'}</p></div>
                        <div className="bg-white dark:bg-slate-900 rounded-lg px-2.5 py-2 col-span-2">
                          <p className="text-slate-500 dark:text-slate-400">Blood Pressure</p>
                          <p className="font-semibold text-slate-800 dark:text-slate-200">{h.bp_systolic != null && h.bp_diastolic != null ? `${h.bp_systolic}/${h.bp_diastolic} mmHg` : '—'}</p>
                        </div>
                        <div className="bg-white dark:bg-slate-900 rounded-lg px-2.5 py-2 col-span-2">
                          <p className="text-slate-500 dark:text-slate-400">Step Counter</p>
                          <p className={`font-bold ${h.daily_steps == null ? 'text-slate-500 dark:text-slate-400' : h.step_status === 'red' ? 'text-red-600 dark:text-red-400' : h.step_status === 'yellow' ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
                            {h.daily_steps != null ? `${h.daily_steps.toLocaleString()} / ${DAILY_STEP_GOAL.toLocaleString()}` : '—'}
                          </p>
                        </div>
                      </div>
                    </Link>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-5 py-12 text-center">
                  <p className="text-sm font-semibold text-slate-600 dark:text-slate-400">No health check-ins submitted yet today</p>
                </div>
              )}
            </div>
          </section>

          <section className={`lg:col-span-4 ${GLASS} p-6`}>
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Group Snapshot</p>
            <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg mb-1">Well-being Distribution</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">All {data.total_business_units} CEOs by latest status on record</p>
            <Chart options={donut.options} series={donut.series} type="donut" height={220} />
            <div className="grid grid-cols-2 gap-2 mt-2 text-xs text-slate-600 dark:text-slate-400">
              <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />Healthy ({data.counts.healthy})</span>
              <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-amber-500" />Fatigue Watch ({data.counts.warning})</span>
              <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-rose-500" />Critical ({data.counts.critical})</span>
              <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-slate-300 dark:bg-slate-600" />No Data ({data.counts.unknown})</span>
            </div>
          </section>
        </div>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Full Directory</p>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">CEO Well-being Directory</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Every business unit's latest recorded status &middot; click a card for full history</p>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <input type="text" placeholder="Search BU, CEO..." value={dirSearch} onChange={(e) => setDirSearch(e.target.value)}
                className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 px-3.5 py-2 text-sm w-full sm:w-56 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent" />
              <button onClick={() => setDirFilter('all')} className={filterBtnClass('all')}>All</button>
              <button onClick={() => setDirFilter('red')} className={filterBtnClass('red')}>Critical</button>
              <button onClick={() => setDirFilter('yellow')} className={filterBtnClass('yellow')}>Fatigue Watch</button>
              <button onClick={() => setDirFilter('green')} className={filterBtnClass('green')}>Healthy</button>
            </div>
          </div>
          <div className="p-6">
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {filteredDirectory.map((w) => (
                <Link key={w.bu_id} to={`/admin/business-unit/${w.bu_id}`} className={`rounded-xl border p-4 hover:-translate-y-0.5 transition-transform ${RISK_CARD_CLASS[w.risk]}`}>
                  <div className="flex items-center justify-between mb-2.5">
                    <div>
                      <p className="text-sm font-bold text-slate-900 dark:text-slate-100">{w.bu_name}</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">{w.ceo_username}</p>
                    </div>
                    <span className={`w-2.5 h-2.5 rounded-full ${RISK_DOT[w.risk]}`} />
                  </div>
                  {w.reported_display ? (
                    <div className="space-y-1 text-xs">
                      <div className="flex justify-between"><span className="text-slate-500 dark:text-slate-400">Heart Rate</span><span className="font-semibold text-slate-800 dark:text-slate-200">{w.heart_rate != null ? `${w.heart_rate} bpm` : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500 dark:text-slate-400">Sleep Time</span><span className="font-semibold text-slate-800 dark:text-slate-200">{w.sleep_hours != null ? `${w.sleep_hours} hrs` : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500 dark:text-slate-400">Blood Pressure</span><span className="font-semibold text-slate-800 dark:text-slate-200">{w.bp_systolic != null && w.bp_diastolic != null ? `${w.bp_systolic}/${w.bp_diastolic}` : '—'}</span></div>
                      <div className="flex justify-between">
                        <span className="text-slate-500 dark:text-slate-400">Step Counter</span>
                        <span className="font-semibold text-slate-800 dark:text-slate-200">{w.daily_steps != null ? `${w.daily_steps.toLocaleString()} / ${DAILY_STEP_GOAL.toLocaleString()}` : '—'}</span>
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500 dark:text-slate-400 italic">No health data submitted yet</p>
                  )}
                  {w.reported_display && <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-2.5">Reported {w.reported_display}</p>}
                </Link>
              ))}
            </div>
            {filteredDirectory.length === 0 && <p className="text-center text-sm text-slate-500 dark:text-slate-400 py-10">No business units match this filter.</p>}
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Audit Trail</p>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Health Submission History</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Last 300 submissions across all business units</p>
            </div>
            <input type="text" placeholder="Search BU, CEO..." value={historySearch} onChange={(e) => setHistorySearch(e.target.value)}
              className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 px-3.5 py-2 text-sm w-full sm:w-64 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent" />
          </div>
          <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto max-h-[32rem] overflow-y-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="sticky top-0 z-[5] bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Business Unit</th>
                  <th className="px-6 py-3 text-left">CEO</th>
                  <th className="px-6 py-3 text-left">Date</th>
                  <th className="px-6 py-3 text-left">Submitted At</th>
                  <th className="px-6 py-3 text-left">Heart Rate</th>
                  <th className="px-6 py-3 text-left">Sleep Time</th>
                  <th className="px-6 py-3 text-left">Blood Pressure</th>
                  <th className="px-6 py-3 text-left">Step Counter</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filteredHistory.map((h, i) => (
                  <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors">
                    <td className="px-6 py-3">
                      <p className="font-semibold text-slate-800 dark:text-slate-200">{h.bu_name}</p>
                    </td>
                    <td className="px-6 py-3 text-slate-600 dark:text-slate-400">{h.ceo_username}</td>
                    <td className="px-6 py-3 text-slate-500 dark:text-slate-400">{h.report_date}</td>
                    <td className="px-6 py-3 text-slate-500 dark:text-slate-400">{h.submitted_at || '—'}</td>
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{h.heart_rate != null ? `${h.heart_rate} bpm` : '—'}</td>
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{h.sleep_hours != null ? `${h.sleep_hours} hrs` : '—'}</td>
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{h.bp_systolic != null && h.bp_diastolic != null ? `${h.bp_systolic}/${h.bp_diastolic}` : '—'}</td>
                    <td className={`px-6 py-3 font-medium ${h.step_status === 'red' ? 'text-red-600 dark:text-red-400' : h.step_status === 'yellow' ? 'text-amber-600 dark:text-amber-400' : h.step_status === 'green' ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}`}>
                      {h.daily_steps != null ? `${h.daily_steps.toLocaleString()} / ${DAILY_STEP_GOAL.toLocaleString()}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredHistory.length === 0 && <p className="text-center text-sm text-slate-500 dark:text-slate-400 py-10">No submissions match your search.</p>}
          </div>
        </section>
        <Footer />
      </main>
    </div>
  );
}
