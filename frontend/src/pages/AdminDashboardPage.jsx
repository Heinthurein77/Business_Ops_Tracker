import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import Chart from 'react-apexcharts';
import { api } from '../api/client';
import AdminSidebar, { displayNameOf } from '../components/AdminSidebar';
import CeoProfileCard from '../components/CeoProfileCard';
import Footer from '../components/Footer';
import QuadrantStatusCircle from '../components/QuadrantStatusCircle';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import {
  BU_QUADRANTS,
  buildQuadrantMetrics,
  formatVariance,
  QUADRANT_ACCENT,
  QUADRANT_BADGE_CLASS,
  QUADRANT_ICON,
  QUADRANT_ICON_CLASS,
  QUADRANT_STATUS_LABEL,
} from '../utils/quadrantMetrics';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const STATUS_COLOR = { green: '#10B981', yellow: '#F59E0B', red: '#EF4444', grey: '#94a3b8' };
const STATUS_LABEL = { green: 'Optimal', yellow: 'Needs Attention', red: 'Critical Warning', grey: 'No Target Set' };
const STATUS_PILL = {
  green: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20 dark:border-emerald-500/30',
  yellow: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20 dark:border-amber-500/30',
  red: 'bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20 dark:border-red-500/30',
  grey: 'bg-slate-500/10 text-slate-500 dark:text-slate-400 border-slate-500/20 dark:border-slate-500/30',
};

function money(v) {
  return v == null ? '—' : `Ks ${Math.round(Number(v)).toLocaleString()}`;
}

function formatIsoShort(iso) {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).replace(/ /g, ' ');
}

/** Attaches a running Month-to-Date Revenue / Other Amount total to each row
 * (client-side only — financial_rows already carries the combined cumulative
 * figure via `performance.cumulative_net_revenue`, but not the two components
 * split out per day, so this reconstructs that split without a backend
 * change). Resets at each calendar-month boundary, mirroring the backend's
 * own build_financial_rows logic. */
function withRunningTotals(rowsDesc) {
  const asc = [...rowsDesc].reverse();
  let mtdRevenue = 0;
  let mtdOther = 0;
  let monthKey = null;
  const byDate = {};
  for (const r of asc) {
    const key = r.report_date.split(' ').slice(1).join(' '); // "27 Jul 2026" -> "Jul 2026"
    if (key !== monthKey) {
      monthKey = key;
      mtdRevenue = 0;
      mtdOther = 0;
    }
    mtdRevenue += r.revenue || 0;
    mtdOther += r.other_amount || 0;
    byDate[r.report_date] = { mtdRevenue, mtdOther };
  }
  return rowsDesc.map((r) => ({ ...r, ...byDate[r.report_date] }));
}

export default function AdminDashboardPage() {
  const { user } = useAuth();
  const { theme } = useTheme();
  const isDark = theme === 'dark';
  const [searchParams, setSearchParams] = useSearchParams();
  const [ov, setOv] = useState(null);
  const [detail, setDetail] = useState(null);
  const [dateIndex, setDateIndex] = useState(0);
  const [buSelectorOpen, setBuSelectorOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const buSelectorRef = useRef(null);
  const searchRef = useRef(null);

  useEffect(() => {
    function handleOutsideClick(e) {
      if (buSelectorRef.current && !buSelectorRef.current.contains(e.target)) setBuSelectorOpen(false);
      if (searchRef.current && !searchRef.current.contains(e.target)) setSearchOpen(false);
    }
    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, []);

  async function loadOverview() {
    const data = await api.adminOverview();
    setOv(data);
    if (!searchParams.get('bu') && data.operations[0]) {
      setSearchParams({ bu: String(data.operations[0].bu_id) }, { replace: true });
    }
  }

  useEffect(() => {
    loadOverview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedBuId = Number(searchParams.get('bu')) || ov?.operations?.[0]?.bu_id || null;

  useEffect(() => {
    if (!selectedBuId) return;
    setDetail(null);
    setDateIndex(0);
    api.businessUnitDetail(selectedBuId).then(setDetail);
  }, [selectedBuId]);

  function selectBu(buId) {
    setSearchParams({ bu: String(buId) }, { replace: true });
    setBuSelectorOpen(false);
    setSearchOpen(false);
    setSearchQuery('');
  }

  const financialRows = useMemo(() => (detail ? withRunningTotals(detail.financial_rows) : []), [detail]);
  const currentRow = financialRows[dateIndex] || null;
  const perf = currentRow?.performance || null;
  const status = (perf?.status || 'grey').toLowerCase();

  // Ascending (oldest-first) series for the trend chart.
  const trendAsc = useMemo(() => [...financialRows].reverse(), [financialRows]);

  const localHour = new Date().getHours();
  const greeting = localHour < 12 ? 'Good Morning' : localHour < 17 ? 'Good Afternoon' : 'Good Evening';

  if (!ov) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  const selectedBuIndex = ov.operations.findIndex((op) => op.bu_id === selectedBuId);
  const selectedBuMeta = ov.operations[selectedBuIndex] || null;
  const ceoProfile = detail?.ceo_profile || selectedBuMeta?.ceo_profile || null;
  const buMatches = ov.operations.filter((op) => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return true;
    return op.bu_code.toLowerCase().includes(q) || op.bu_name.toLowerCase().includes(q) || op.ceo_username.toLowerCase().includes(q);
  });

  // CEO Input Data — same 4-quadrant Financial/Social/Activity/Health snapshot
  // the CEO sees on their own dashboard and Chairman/HO see in the Operations
  // grid, assembled here from fields already present in `detail` (latest
  // activity/health/social row + the cumulative financial performance).
  const latestActivityRow = detail?.activity_rows?.[0] || {};
  const latestHealthRow = detail?.health_rows?.[0] || {};
  const latestSocialRow = detail?.social_rows?.[0] || {};
  const quadrantMetrics = detail
    ? buildQuadrantMetrics({
        financial_status: detail.financial_status,
        financial_reason: detail.financial_reason,
        social_status: detail.social_status,
        social_reason: detail.social_reason,
        activity_status: detail.activity_status,
        activity_reason: detail.activity_reason,
        health_status: detail.health_status,
        health_reason: detail.health_reason,
        cumulative_net_revenue: perf?.cumulative_net_revenue,
        mtd_revenue: currentRow?.mtdRevenue,
        mtd_other_amount: currentRow?.mtdOther,
        achievement_pct: perf?.achievement_pct,
        variance_amount: perf?.variance_amount,
        budget_status: status,
        event_flag: latestSocialRow.event_flag,
        meeting_flag: latestSocialRow.meeting_flag,
        golf_flag: latestSocialRow.golf_flag,
        social_other_notes: latestSocialRow.other_notes,
        monthly_event_count: detail.monthly_event_count,
        monthly_meeting_count: detail.monthly_meeting_count,
        monthly_golf_count: detail.monthly_golf_count,
        gym_percent: latestActivityRow.gym_percent,
        swimming_percent: latestActivityRow.swimming_percent,
        cycle_percent: latestActivityRow.cycle_percent,
        activity_other_notes: latestActivityRow.other_notes,
        daily_steps: latestHealthRow.daily_steps,
        heart_rate: latestHealthRow.heart_rate,
        sleep_hours: latestHealthRow.sleep_hours,
        bp_systolic: latestHealthRow.bp_systolic,
        bp_diastolic: latestHealthRow.bp_diastolic,
        health_other_notes: latestHealthRow.other_notes,
      })
    : null;

  const trendChart = {
    series: [
      { name: 'Cumulative Net Revenue', data: trendAsc.map((r) => r.performance?.cumulative_net_revenue ?? null) },
      { name: 'Daily Baseline Target', data: trendAsc.map((r) => r.performance?.expected_mtd_target ?? null) },
    ],
    options: {
      chart: { type: 'area', height: 280, toolbar: { show: false }, fontFamily: 'Inter, sans-serif' },
      colors: ['#4F46E5', '#94a3b8'],
      stroke: { curve: 'smooth', width: [3, 2], dashArray: [0, 6] },
      fill: {
        type: ['gradient', 'solid'],
        gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0.02 },
        opacity: [1, 0],
      },
      dataLabels: { enabled: false },
      xaxis: { categories: trendAsc.map((r) => r.report_date), labels: { style: { colors: '#94a3b8', fontSize: '11px' } }, axisBorder: { show: false }, axisTicks: { show: false } },
      yaxis: { labels: { formatter: (v) => 'Ks ' + (v / 1000).toFixed(0) + 'k', style: { colors: '#94a3b8', fontSize: '11px' } } },
      grid: { borderColor: isDark ? '#334155' : '#eef2f7' },
      legend: { position: 'top', horizontalAlign: 'right', fontSize: '12px', labels: { colors: isDark ? '#94a3b8' : '#64748b' } },
      tooltip: { theme: isDark ? 'dark' : 'light', y: { formatter: (v) => money(v) } },
    },
  };

  const achievementCapped = perf ? Math.max(0, Math.min(100, perf.achievement_pct)) : 0;
  const donutChart = {
    series: perf ? [achievementCapped, Math.max(0, 100 - achievementCapped)] : [0, 100],
    options: {
      chart: { type: 'donut', height: 240, fontFamily: 'Inter, sans-serif' },
      labels: ['Achieved', 'Remaining'],
      colors: [STATUS_COLOR[status], isDark ? '#334155' : '#eef2f7'],
      legend: { show: false },
      dataLabels: { enabled: false },
      stroke: { width: 0 },
      plotOptions: {
        pie: {
          donut: {
            size: '75%',
            labels: {
              show: true,
              value: { fontSize: '26px', fontWeight: 700, color: isDark ? '#f1f5f9' : '#0f172a', formatter: () => (perf ? `${perf.achievement_pct}%` : '—') },
              total: { show: true, label: perf ? STATUS_LABEL[status] : 'No Target', fontSize: '12px', color: STATUS_COLOR[status], formatter: () => (perf ? `${perf.achievement_pct}%` : '—') },
            },
          },
        },
      },
      tooltip: { enabled: false },
    },
  };

  const kpiCards = [
    { label: 'MTD Revenue', value: money(currentRow?.mtdRevenue), sub: 'Month-to-date', color: 'brand' },
    { label: 'Cash Flow', value: money(currentRow?.cash_flow), sub: currentRow ? currentRow.report_date : 'No data', color: 'emerald' },
    { label: 'Receivable', value: money(currentRow?.receivable), sub: currentRow ? currentRow.report_date : 'No data', color: 'amber' },
  ];

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">

        {/* TOP BAR */}
        <section className={`${GLASS} relative z-10 px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-lg">{greeting}, {displayNameOf(user)}</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{ov.today_display} &middot; {ov.submission_compliance.submitted} of {ov.submission_compliance.total} BUs reported today</p>
          </div>
          <div className="flex items-center gap-3 flex-wrap w-full sm:w-auto">
            <div className="relative w-full sm:w-auto" ref={searchRef}>
              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M17 10a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  setSearchOpen(true);
                }}
                onFocus={() => setSearchOpen(true)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && buMatches.length > 0) selectBu(buMatches[0].bu_id);
                  if (e.key === 'Escape') setSearchOpen(false);
                }}
                placeholder="Search business or CEO…"
                className="pl-9 pr-3 py-2.5 text-sm rounded-xl border border-slate-200 dark:border-slate-700 bg-white/70 dark:bg-slate-800/60 text-slate-700 dark:text-slate-300 placeholder:text-slate-400 dark:placeholder:text-slate-500 w-full sm:w-56 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              />
              {searchOpen && searchQuery.trim() !== '' && (
                <div className={`${GLASS} absolute z-50 top-full left-0 mt-2 w-full sm:w-72 max-w-[85vw] overflow-hidden fade-in`}>
                  <div className="max-h-72 overflow-y-auto py-1">
                    {buMatches.length === 0 && <p className="px-3 py-4 text-xs text-slate-400 dark:text-slate-500 text-center">No matching business unit</p>}
                    {buMatches.map((op) => (
                      <button
                        key={op.bu_id}
                        type="button"
                        onClick={() => selectBu(op.bu_id)}
                        className={`w-full text-left px-4 py-2.5 text-sm transition-colors ${op.bu_id === selectedBuId ? 'bg-brand-600/10 dark:bg-brand-500/10 text-brand-700 dark:text-brand-300 font-semibold' : 'text-slate-600 dark:text-slate-300 hover:bg-slate-900/5 dark:hover:bg-white/5'}`}
                      >
                        <span className="font-semibold">{op.bu_name}</span>
                        <span className="block text-[11px] text-slate-400 dark:text-slate-500">CEO: {op.ceo_username}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => selectedBuIndex > 0 && selectBu(ov.operations[selectedBuIndex - 1].bu_id)}
                disabled={selectedBuIndex <= 0}
                aria-label="Previous business unit"
                className="flex items-center justify-center w-9 h-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-white/70 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 hover:border-brand-300 dark:hover:border-brand-500/40 hover:text-slate-800 dark:hover:text-slate-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex-shrink-0"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" /></svg>
              </button>
              <div className="relative" ref={buSelectorRef}>
              <button
                type="button"
                onClick={() => setBuSelectorOpen((v) => !v)}
                className="flex items-center gap-2 text-sm font-bold text-slate-800 dark:text-slate-200 bg-white/70 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-2.5 hover:border-brand-300 dark:hover:border-brand-500/40 transition-colors"
              >
                {selectedBuMeta ? selectedBuMeta.bu_name : 'Select Business'}
                <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-slate-400 dark:text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" /></svg>
              </button>
              {buSelectorOpen && (
                <div className={`${GLASS} absolute z-20 top-full right-0 mt-2 w-72 max-w-[85vw] overflow-hidden fade-in`}>
                  <div className="max-h-72 overflow-y-auto py-1">
                    {buMatches.length === 0 && <p className="px-3 py-4 text-xs text-slate-400 dark:text-slate-500 text-center">No matching business unit</p>}
                    {buMatches.map((op) => (
                      <button
                        key={op.bu_id}
                        type="button"
                        onClick={() => selectBu(op.bu_id)}
                        className={`w-full text-left px-4 py-2.5 text-sm transition-colors ${op.bu_id === selectedBuId ? 'bg-brand-600/10 text-brand-700 dark:text-brand-300 font-semibold' : 'text-slate-600 dark:text-slate-400 hover:bg-slate-900/5 dark:hover:bg-white/5'}`}
                      >
                        {op.bu_name}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              </div>
              <button
                type="button"
                onClick={() => selectedBuIndex < ov.operations.length - 1 && selectBu(ov.operations[selectedBuIndex + 1].bu_id)}
                disabled={selectedBuIndex < 0 || selectedBuIndex >= ov.operations.length - 1}
                aria-label="Next business unit"
                className="flex items-center justify-center w-9 h-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-white/70 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 hover:border-brand-300 dark:hover:border-brand-500/40 hover:text-slate-800 dark:hover:text-slate-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex-shrink-0"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
              </button>
            </div>
            <Link to={`/admin/business-unit/${selectedBuId}`} className="text-sm font-semibold bg-brand-600 hover:bg-brand-500 text-white px-4 py-2.5 rounded-xl transition-colors shadow-md shadow-brand-600/30 whitespace-nowrap">
              View Full History
            </Link>
          </div>
        </section>

        {/* CEO PROFILE CARD */}
        <CeoProfileCard
          ceoProfile={ceoProfile}
          subtitle={ceoProfile ? `CEO of ${selectedBuMeta?.bu_name}` : ''}
          glassClass={GLASS}
        />

        {/* KPI CARDS ROW */}
        <section className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {kpiCards.map((card) => (
            <div key={card.label} className={`${GLASS} p-5 relative overflow-hidden`}>
              <span className={`absolute top-0 left-0 right-0 h-[3px] bg-${card.color}-500`} />
              <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">{card.label}</p>
              <p className="font-bold text-slate-900 dark:text-slate-100 text-[26px] leading-tight mt-1.5 tabular-nums">{card.value}</p>
              <p className={`text-[11px] font-medium mt-1 text-${card.color}-600 dark:text-${card.color}-400`}>{card.sub}</p>
            </div>
          ))}
          <div className={`${GLASS} p-5 relative overflow-hidden flex flex-col justify-between`}>
            <span className="absolute top-0 left-0 right-0 h-[3px]" style={{ backgroundColor: STATUS_COLOR[status] }} />
            <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">Cumulative Health</p>
            <span className={`inline-flex items-center gap-1.5 text-sm font-bold px-3 py-1.5 rounded-full border mt-2 w-fit ${STATUS_PILL[status]}`}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: STATUS_COLOR[status] }} />
              {perf ? `${perf.achievement_pct}%` : '—'} · {STATUS_LABEL[status]}
            </span>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1.5">vs Monthly Target: {perf ? formatVariance(perf.variance_amount) : '—'}</p>
          </div>
        </section>

        {!detail ? (
          <section className={`${GLASS} p-10 flex items-center justify-center`}>
            <div className="w-6 h-6 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
          </section>
        ) : (
          <>
            {/* CEO INPUT DATA — 4-quadrant circle + detail cards, the same
                Financial/Social/Activity/Health snapshot the CEO sees on
                their own dashboard and Chairman/HO see in the Operations
                grid, restyled here for this page's light theme. */}
            <section className={`${GLASS} p-6`}>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">CEO Submitted Data</p>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 text-base mb-5">4-Quadrant Snapshot — {selectedBuMeta?.bu_name}</h2>
              <div className="grid lg:grid-cols-12 gap-6 items-start">
                <div className="lg:col-span-3 flex flex-col items-center gap-5 rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6">
                  <QuadrantStatusCircle
                    financial={detail.financial_status}
                    social={detail.social_status}
                    activity={detail.activity_status}
                    health={detail.health_status}
                    size={240}
                    label={selectedBuMeta?.bu_name}
                    showLabels
                  />
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px] text-slate-500 dark:text-slate-400">
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-emerald-500" />Good</span>
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-amber-500" />Warning</span>
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-red-500" />Critical</span>
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-slate-300 dark:bg-slate-600" />No Data</span>
                  </div>
                  <div
                    className={`w-full text-center rounded-xl px-3 py-2.5 text-xs font-semibold ${
                      detail.submitted_today
                        ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                        : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                    }`}
                  >
                    {detail.submitted_today ? 'Reported today' : 'No report submitted today'}
                  </div>
                </div>

                <div className="lg:col-span-9 grid sm:grid-cols-2 gap-4">
                  {BU_QUADRANTS.map((q) => {
                    const m = quadrantMetrics[q.key];
                    const badgeClass = QUADRANT_BADGE_CLASS[m.status] || QUADRANT_BADGE_CLASS.unknown;
                    return (
                      <div key={q.key} className="relative overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 pt-4 shadow-[0_1px_3px_rgba(15,23,42,0.04)] dark:shadow-[0_1px_3px_rgba(0,0,0,0.25)] hover:shadow-lg hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-0.5 transition-all duration-200">
                        <span className={`absolute top-0 left-0 right-0 h-1 ${QUADRANT_ACCENT[m.status] || QUADRANT_ACCENT.unknown}`} />
                        <div className="flex items-start justify-between gap-2 mb-3 flex-wrap">
                          <div className="flex items-center gap-2.5 min-w-0">
                            <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${QUADRANT_ICON_CLASS[m.status] || QUADRANT_ICON_CLASS.unknown}`}>
                              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                                <path strokeLinecap="round" strokeLinejoin="round" d={QUADRANT_ICON[q.key]} />
                              </svg>
                            </div>
                            <h3 className="text-[15px] font-bold text-slate-900 dark:text-slate-100 whitespace-nowrap">{q.label}</h3>
                          </div>
                          <span className={`text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border flex-shrink-0 ${badgeClass}`}>
                            {QUADRANT_STATUS_LABEL[m.status] || QUADRANT_STATUS_LABEL.unknown}
                          </span>
                        </div>
                        {m.displayValue && <p className="font-bold text-slate-800 dark:text-slate-200 text-xl tracking-tight mb-3">{m.displayValue}</p>}
                        {m.reason && (
                          <div
                            className={`flex items-start gap-1.5 rounded-lg px-2.5 py-2 mb-3 text-[11.5px] leading-snug ${
                              m.status === 'critical'
                                ? 'bg-red-500/10 text-red-700 dark:text-red-300 font-medium'
                                : m.status === 'warning'
                                ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
                                : 'bg-slate-100 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400'
                            }`}
                          >
                            {(m.status === 'critical' || m.status === 'warning') && (
                              <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                              </svg>
                            )}
                            <span>{m.reason}</span>
                          </div>
                        )}
                        <ul className="space-y-2 text-[13px] border-t border-slate-100 dark:border-slate-800 pt-3">
                          {m.subMetrics.map((sm) => (
                            <li key={sm.label} className="flex items-start justify-between gap-3">
                              <span className="text-slate-500 dark:text-slate-400 flex-shrink-0">{sm.label}</span>
                              <span className="font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5 min-w-0 text-right break-words justify-end">
                                {sm.color && <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: sm.color }} />}
                                {sm.value}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}
                </div>
              </div>
            </section>

            {/* CHARTS ROW */}
            <section className="grid lg:grid-cols-5 gap-6">
              <div className={`${GLASS} lg:col-span-3 p-6`}>
                <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Trend</p>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 text-base mb-4">Daily Cumulative Revenue vs Baseline Target</h2>
                {trendAsc.length > 0 ? (
                  <Chart options={trendChart.options} series={trendChart.series} type="area" height={280} />
                ) : (
                  <p className="text-sm text-slate-400 dark:text-slate-500 text-center py-16">No daily entries recorded yet this month.</p>
                )}
              </div>
              <div className={`${GLASS} lg:col-span-2 p-6 flex flex-col`}>
                <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Single-Step Health</p>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 text-base mb-2">Target Achievement</h2>
                <Chart options={donutChart.options} series={donutChart.series} type="donut" height={240} />
                <p className="text-xs text-slate-500 dark:text-slate-400 text-center mt-2">
                  {perf ? `${formatVariance(perf.variance_amount)} vs full monthly target (Ks ${perf.full_monthly_target.toLocaleString()})` : 'No monthly target set for this business unit'}
                </p>
              </div>
            </section>

            {/* DAILY LOG + QUICK ACTIONS */}
            <section className="grid lg:grid-cols-5 gap-6">
              <div className={`${GLASS} lg:col-span-3 overflow-hidden`}>
                <div className="px-6 py-4 border-b border-slate-200/70 dark:border-slate-700/70">
                  <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Daily Log</p>
                  <h2 className="font-bold text-slate-900 dark:text-slate-100 text-base">Recent Entries</h2>
                </div>
                <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
                  Swipe to see all columns
                </p>
                <div className="overflow-x-auto max-h-96 overflow-y-auto">
                  <table className="w-full min-w-max text-sm">
                    <thead className="sticky top-0 bg-white/90 dark:bg-slate-800/90 backdrop-blur text-slate-500 dark:text-slate-400 text-[11px] uppercase tracking-wide">
                      <tr>
                        <th className="px-5 py-2.5 text-left">Date</th>
                        <th className="px-5 py-2.5 text-right">Revenue</th>
                        <th className="px-5 py-2.5 text-right">Other</th>
                        <th className="px-5 py-2.5 text-right">Net Daily Total</th>
                        <th className="px-5 py-2.5 text-left">Notes</th>
                        <th className="px-5 py-2.5 text-center">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {financialRows.map((r, i) => {
                        const rowStatus = (r.performance?.status || 'grey').toLowerCase();
                        const note = detail.financial_notes.find((n) => formatIsoShort(n.note_date) === r.report_date);
                        return (
                          <tr key={i} onClick={() => setDateIndex(i)} className={`cursor-pointer transition-colors ${i === dateIndex ? 'bg-brand-50/70 dark:bg-brand-500/10' : 'hover:bg-slate-50 dark:hover:bg-slate-800/60'}`}>
                            <td className="px-5 py-3 text-slate-700 dark:text-slate-300 font-medium">{r.report_date}</td>
                            <td className="px-5 py-3 text-right text-slate-700 dark:text-slate-300">{money(r.revenue)}</td>
                            <td className="px-5 py-3 text-right text-slate-500 dark:text-slate-400">{money(r.other_amount)}</td>
                            <td className="px-5 py-3 text-right font-semibold text-slate-900 dark:text-slate-100">{money((r.revenue || 0) + (r.other_amount || 0))}</td>
                            <td className="px-5 py-3 text-slate-500 dark:text-slate-400 max-w-[160px] truncate" title={note?.note_text || ''}>{note?.note_text || '—'}</td>
                            <td className="px-5 py-3 text-center">
                              <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_PILL[rowStatus]}`}>
                                {r.performance ? `${r.performance.achievement_pct}%` : '—'}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                      {financialRows.length === 0 && (
                        <tr><td colSpan={6} className="text-center text-sm text-slate-400 dark:text-slate-500 py-10">No daily entries recorded yet this month.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="lg:col-span-2 flex flex-col gap-4">
                <Link to={`/admin/business-unit/${selectedBuId}`} className={`${GLASS} p-5 flex items-center gap-4 hover:shadow-md transition-shadow group`}>
                  <div className="w-11 h-11 rounded-xl bg-brand-600/10 flex items-center justify-center flex-shrink-0 group-hover:bg-brand-600/20 transition-colors">
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-brand-600 dark:text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" /></svg>
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-slate-900 dark:text-slate-100">Adjust Monthly Target</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Override this BU's CEO-set revenue/cash-flow target</p>
                  </div>
                </Link>
                <button
                  type="button"
                  onClick={() => setBuSelectorOpen(true)}
                  className={`${GLASS} p-5 flex items-center gap-4 hover:shadow-md transition-shadow group text-left`}
                >
                  <div className="w-11 h-11 rounded-xl bg-emerald-500/10 flex items-center justify-center flex-shrink-0 group-hover:bg-emerald-500/20 transition-colors">
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-emerald-600 dark:text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M8 7h12m0 0l-4-4m4 4l-4 4M16 17H4m0 0l4 4m-4-4l4-4" /></svg>
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-slate-900 dark:text-slate-100">Switch Business</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Jump to another business unit's health dashboard</p>
                  </div>
                </button>
                <div className={`${GLASS} p-5 flex-1`}>
                  <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Qualitative Context</p>
                  <h3 className="font-bold text-slate-900 dark:text-slate-100 text-sm mb-3">Record Notes</h3>
                  <div className="space-y-2 max-h-40 overflow-y-auto">
                    {detail.financial_notes.length === 0 && <p className="text-xs text-slate-400 dark:text-slate-500 italic">No notes logged yet this month.</p>}
                    {detail.financial_notes.map((n, i) => (
                      <div key={i} className="rounded-xl bg-amber-500/5 border border-amber-500/10 dark:border-amber-500/20 px-3 py-2">
                        <p className="text-xs text-slate-700 dark:text-slate-300">{n.note_text}</p>
                        <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">{n.note_date}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </section>
          </>
        )}

        {/* ALERTS CENTER */}
        <section>
          <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Exception-First Monitoring</p>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Alerts Center</h2>
            </div>
            <span className={`text-xs font-bold px-3 py-1.5 rounded-full border ${ov.submission_compliance.submitted === ov.submission_compliance.total ? 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400'}`}>
              {ov.submission_compliance.submitted} of {ov.submission_compliance.total} BUs reported today ({ov.submission_compliance.pct}%)
            </span>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5 items-start">
            <div className={`${GLASS} p-6`}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-red-500/10 flex items-center justify-center flex-shrink-0">
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-4.5 h-4.5 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <h3 className="font-bold text-slate-900 dark:text-slate-100 text-[15px]">Business Performance Alerts</h3>
                </div>
                <span className={`text-xs font-bold px-2.5 py-1 rounded-full border ${ov.financial_alerts.length ? 'bg-red-500/10 border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400' : 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400'}`}>
                  {ov.financial_alerts.length} BU{ov.financial_alerts.length !== 1 ? 's' : ''}
                </span>
              </div>
              <p className="text-[11px] text-slate-500 dark:text-slate-400 mb-4 pl-[52px]">Missing weekday submission, or a net loss on the latest figures</p>
              {ov.financial_alerts.length > 0 ? (
                <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                  {ov.financial_alerts.map((a) => (
                    <Link key={a.bu_id} to={`/admin/business-unit/${a.bu_id}`} className="flex items-center gap-3 rounded-xl border border-red-500/10 dark:border-red-500/20 bg-red-500/5 hover:bg-red-500/10 px-3.5 py-2.5 transition-colors">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-red-700 dark:text-red-400 truncate">{a.bu_name}</p>
                        <p className="text-xs text-red-500/80 dark:text-red-400/80 mt-0.5">{a.reason}</p>
                      </div>
                    </Link>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-emerald-500/10 dark:border-emerald-500/20 bg-emerald-500/5 px-3.5 py-6 text-center">
                  <p className="text-xs font-semibold text-emerald-600 dark:text-emerald-400">All clear — no financial red flags</p>
                </div>
              )}
            </div>

            <div className={`${GLASS} p-6`}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-amber-500/10 flex items-center justify-center flex-shrink-0">
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-4.5 h-4.5 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                  </div>
                  <h3 className="font-bold text-slate-900 dark:text-slate-100 text-[15px]">Daily Activities Alerts</h3>
                </div>
                <span className={`text-xs font-bold px-2.5 py-1 rounded-full border ${ov.activity_alerts.length ? 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400' : 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400'}`}>
                  {ov.activity_alerts.length} BU{ov.activity_alerts.length !== 1 ? 's' : ''}
                </span>
              </div>
              <p className="text-[11px] text-slate-500 dark:text-slate-400 mb-4 pl-[52px]">No daily report submitted at all today</p>
              {ov.activity_alerts.length > 0 ? (
                <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                  {ov.activity_alerts.map((a) => (
                    <Link key={a.bu_id} to={`/admin/business-unit/${a.bu_id}`} className="flex items-center gap-3 rounded-xl border border-amber-500/10 dark:border-amber-500/20 bg-amber-500/5 hover:bg-amber-500/10 px-3.5 py-2.5 transition-colors">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-amber-700 dark:text-amber-400 truncate">{a.bu_name}</p>
                        <p className="text-xs text-amber-500/80 dark:text-amber-400/80 mt-0.5">{a.reason}</p>
                      </div>
                    </Link>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-emerald-500/10 dark:border-emerald-500/20 bg-emerald-500/5 px-3.5 py-6 text-center">
                  <p className="text-xs font-semibold text-emerald-600 dark:text-emerald-400">All clear — everyone reported today</p>
                </div>
              )}
            </div>

            <div className={`${GLASS} p-6`}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-rose-500/10 flex items-center justify-center flex-shrink-0">
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-4.5 h-4.5 text-rose-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
                    </svg>
                  </div>
                  <h3 className="font-bold text-slate-900 dark:text-slate-100 text-[15px]">Health and Fitness Alerts</h3>
                </div>
                <span className={`text-xs font-bold px-2.5 py-1 rounded-full border ${ov.critical_ceos.length ? 'bg-rose-500/10 border-rose-500/20 dark:border-rose-500/30 text-rose-600 dark:text-rose-400' : 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400'}`}>
                  {ov.critical_ceos.length} BU{ov.critical_ceos.length !== 1 ? 's' : ''}
                </span>
              </div>
              <p className="text-[11px] text-slate-500 dark:text-slate-400 mb-4 pl-[52px]">Sedentary steps, or heart rate / blood pressure out of range</p>
              {ov.critical_ceos.length > 0 ? (
                <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                  {ov.critical_ceos.map((w) => (
                    <Link key={w.bu_id} to={`/admin/business-unit/${w.bu_id}`} className="flex items-center gap-3 rounded-xl border border-rose-500/10 dark:border-rose-500/20 bg-rose-500/5 hover:bg-rose-500/10 px-3.5 py-2.5 transition-colors">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-rose-700 dark:text-rose-400 truncate">{w.bu_name}</p>
                        <p className="text-xs text-rose-500/80 dark:text-rose-400/80 mt-0.5">{w.risk_reason}</p>
                      </div>
                    </Link>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-emerald-500/10 dark:border-emerald-500/20 bg-emerald-500/5 px-3.5 py-6 text-center">
                  <p className="text-xs font-semibold text-emerald-600 dark:text-emerald-400">All clear — no critical health alerts</p>
                </div>
              )}
            </div>
          </div>
        </section>
        <Footer />
      </main>
    </div>
  );
}
