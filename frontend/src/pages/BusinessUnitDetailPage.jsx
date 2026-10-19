import { Fragment, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import { useAuth } from '../context/AuthContext';
import ThemeToggle from '../components/ThemeToggle';
import QuadrantStatusCircle from '../components/QuadrantStatusCircle';
import Footer from '../components/Footer';
import {
  BU_QUADRANTS,
  buildQuadrantMetrics,
  formatVariance,
  money,
  QUADRANT_ACCENT,
  QUADRANT_BADGE_CLASS,
  QUADRANT_ICON,
  QUADRANT_ICON_CLASS,
  QUADRANT_STATUS_LABEL,
} from '../utils/quadrantMetrics';
import { GLASS, PAGE_BG } from '../utils/glassTheme';
import { safeUrl } from '../utils/safeUrl';

const emptyTargetForm = {
  monthly_revenue_target: '',
  monthly_cash_flow_target: '',
  monthly_max_receivable: '',
  monthly_max_payable: '',
};

const STATUS_BADGE_CLASS = {
  red: 'bg-red-500/10 border-red-500/20 dark:border-red-500/30',
  yellow: 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30',
  green: 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30',
  grey: 'bg-slate-50 dark:bg-slate-900 border-slate-200 dark:border-slate-700',
};
const STATUS_TEXT_CLASS = { red: 'text-red-600 dark:text-red-400', yellow: 'text-amber-600 dark:text-amber-400', green: 'text-emerald-600 dark:text-emerald-400', grey: 'text-slate-500 dark:text-slate-400' };
const STATUS_LABEL = { red: 'Critical', yellow: 'Warning', green: 'Good', grey: 'No Data' };

export default function BusinessUnitDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const [data, setData] = useState(null);
  const [expandedRow, setExpandedRow] = useState(null);
  const [reviewIndex, setReviewIndex] = useState(0);
  const [targetEditing, setTargetEditing] = useState(false);
  const [targetForm, setTargetForm] = useState(emptyTargetForm);
  const [targetSaving, setTargetSaving] = useState(false);
  const [targetError, setTargetError] = useState('');

  useEffect(() => {
    setData(null);
    setReviewIndex(0);
    setTargetEditing(false);
    setTargetError('');
    api.businessUnitDetail(id).then(setData);
  }, [id]);

  function updateTargetField(key, value) {
    setTargetForm((f) => ({ ...f, [key]: value }));
  }

  function handleBackToDashboard() {
    // Return to whichever admin page actually linked here (Executive
    // Dashboard, Comparison, or Operations) instead of always resetting to
    // /admin — history.state.idx is react-router's own in-app position
    // counter, so this only falls back to /admin when there's truly no
    // in-app page to go back to (e.g. this URL was opened directly).
    if (window.history.state && window.history.state.idx > 0) {
      navigate(-1);
    } else {
      navigate('/admin');
    }
  }

  async function handleOverrideTarget(e) {
    e.preventDefault();
    setTargetError('');
    const { monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, monthly_max_payable } = targetForm;
    if ([monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, monthly_max_payable].some((v) => v === '')) {
      setTargetError('All four target fields are required.');
      return;
    }
    setTargetSaving(true);
    try {
      const targetMonth = data.today.slice(0, 7) + '-01';
      await api.overrideMonthlyTarget(id, {
        target_month: targetMonth,
        monthly_revenue_target: Number(monthly_revenue_target),
        monthly_cash_flow_target: Number(monthly_cash_flow_target),
        monthly_max_receivable: Number(monthly_max_receivable),
        monthly_max_payable: Number(monthly_max_payable),
      });
      setTargetEditing(false);
      setData(await api.businessUnitDetail(id));
    } catch (err) {
      setTargetError(err instanceof ApiError ? err.message : 'Failed to save monthly target');
    } finally {
      setTargetSaving(false);
    }
  }

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 dark:border-t-brand-400 rounded-full animate-spin" />
      </div>
    );
  }

  const reviewDates = data.activity_rows;
  const currentReview = reviewDates[reviewIndex] || null;
  const financialForDay = currentReview ? data.financial_rows.find((f) => f.report_date === currentReview.report_date) : null;
  const healthForDay = currentReview ? data.health_rows.find((h) => h.report_date === currentReview.report_date) : null;
  const socialForDay = currentReview ? data.social_rows.find((s) => s.report_date === currentReview.report_date) : null;

  // CEO Input Data — same 4-quadrant Business Performance/Social
  // Activity/Daily Activities/Health and Fitness snapshot the Executive
  // Dashboard and the CEO's own dashboard show, built from this page's
  // latest-ever rows + today's cumulative benchmark (`data.cumulative_performance`,
  // already evaluated as of today regardless of whether this BU has
  // submitted today) — so all three pages read as one visual language and
  // never disagree about the same BU's current status.
  const latestActivityRow = data.activity_rows[0] || {};
  const latestHealthRow = data.health_rows[0] || {};
  const latestSocialRow = data.social_rows[0] || {};
  const perf = data.cumulative_performance;
  const budgetStatus = (perf?.status || 'grey').toLowerCase();
  const quadrantMetrics = buildQuadrantMetrics({
    financial_status: data.financial_status,
    financial_reason: data.financial_reason,
    social_status: data.social_status,
    social_reason: data.social_reason,
    activity_status: data.activity_status,
    activity_reason: data.activity_reason,
    health_status: data.health_status,
    health_reason: data.health_reason,
    cumulative_net_revenue: perf?.cumulative_net_revenue,
    mtd_revenue: perf?.mtd_revenue,
    mtd_other_amount: perf?.mtd_other_amount,
    achievement_pct: perf?.achievement_pct,
    variance_amount: perf?.variance_amount,
    budget_status: budgetStatus,
    event_flag: latestSocialRow.event_flag,
    meeting_flag: latestSocialRow.meeting_flag,
    golf_flag: latestSocialRow.golf_flag,
    social_other_notes: latestSocialRow.other_notes,
    monthly_event_count: data.monthly_event_count,
    monthly_meeting_count: data.monthly_meeting_count,
    monthly_golf_count: data.monthly_golf_count,
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
  });

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-900 dark:text-slate-100 antialiased`}>
      <header className="bg-white dark:bg-slate-950 text-slate-900 dark:text-white sticky top-0 z-40 shadow-sm dark:shadow-xl border-b border-slate-200 dark:border-white/5 ">
        <div className="max-w-[1300px] mx-auto px-4 sm:px-6 py-2.5 sm:py-3 flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-lg bg-gradient-to-br from-brand-500 to-indigo-400 flex items-center justify-center shadow-lg shadow-brand-500/30 flex-shrink-0">
              <span className="text-xs font-extrabold text-white">{data.bu.code.slice(-2)}</span>
            </div>
            <div className="min-w-0">
              <p className="text-xs sm:text-sm font-bold tracking-tight leading-none truncate">{data.bu.name}</p>
              <p className="text-[10.5px] sm:text-[11px] text-slate-600 dark:text-slate-400 mt-1 truncate">CEO: {data.ceo_username}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 sm:gap-2.5">
            <button type="button" onClick={handleBackToDashboard} className="flex items-center gap-1.5 text-xs font-semibold bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 px-2.5 sm:px-3 py-1.5 rounded-lg transition-colors border border-slate-200 dark:border-slate-700 whitespace-nowrap">
              <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
              </svg>
              <span className="hidden sm:inline">Back to Dashboard</span>
              <span className="sm:hidden">Back</span>
            </button>
            <ThemeToggle iconOnly className="bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 px-2.5 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700" />
            <button onClick={() => logout()} className="text-xs font-semibold bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 px-2.5 sm:px-3 py-1.5 rounded-lg transition-colors border border-slate-200 dark:border-slate-700 whitespace-nowrap">
              Sign Out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-[1300px] mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-8">
        {data.ceo_profile && (
          <section className={`${GLASS} px-4 sm:px-5 py-3 sm:py-3.5 flex items-center gap-3.5 flex-wrap`}>
            {safeUrl(data.ceo_profile.profile_pic_url) ? (
              <img src={safeUrl(data.ceo_profile.profile_pic_url)} alt="" className="w-11 h-11 rounded-full object-cover border border-slate-200 dark:border-slate-700 flex-shrink-0" />
            ) : (
              <div className="w-11 h-11 rounded-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center flex-shrink-0">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-slate-700 dark:text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </div>
            )}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-bold text-slate-900 dark:text-white truncate">{data.ceo_profile.full_name || data.ceo_username}</p>
                {data.ceo_profile.is_profile_completed ? (
                  data.ceo_profile.executive_title && (
                    <span className="text-[11px] font-medium text-brand-700 dark:text-brand-300 bg-brand-500/10 border border-brand-500/20 dark:border-brand-500/25 px-2 py-0.5 rounded-full">{data.ceo_profile.executive_title}</span>
                  )
                ) : (
                  <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400">Profile Pending</span>
                )}
              </div>
              {data.ceo_profile.is_profile_completed ? (
                <>
                  <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1 text-[11px] text-slate-600 dark:text-slate-400">
                    <span>{data.ceo_profile.phone_number || '—'}</span>
                    {data.ceo_profile.email && <span>{data.ceo_profile.email}</span>}
                    <span>
                      {data.ceo_profile.date_of_birth || '—'}
                      {data.ceo_profile.age != null && ` · ${data.ceo_profile.age} years old`}
                    </span>
                    <span className="truncate max-w-xs" title={data.ceo_profile.address || ''}>{data.ceo_profile.address || '—'}</span>
                  </div>
                  {data.ceo_profile.business_details && (
                    <p className="text-[11px] text-slate-500 mt-1.5 line-clamp-2" title={data.ceo_profile.business_details}>
                      <span className="text-slate-600 dark:text-slate-400 font-semibold">About: </span>
                      {data.ceo_profile.business_details}
                    </p>
                  )}
                </>
              ) : (
                <p className="text-[11px] text-slate-500 mt-1">This CEO hasn't completed their mandatory profile yet.</p>
              )}
            </div>
          </section>
        )}

        {/* CEO INPUT DATA — same 4-quadrant circle + detail cards used on the
            Executive Dashboard and the CEO's own dashboard, so all three
            read as one consistent visual language for the same BU. */}
        <section className={`${GLASS} p-6`}>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">CEO Submitted Data</p>
          <h2 className="font-bold text-slate-900 dark:text-slate-100 text-base mb-5">4-Quadrant Snapshot — {data.bu.name}</h2>
          <div className="grid lg:grid-cols-12 gap-6 items-start">
            <div className="lg:col-span-3 flex flex-col items-center gap-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-6">
              <QuadrantStatusCircle
                financial={data.financial_status}
                social={data.social_status}
                activity={data.activity_status}
                health={data.health_status}
                size={240}
                label={data.bu.name}
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
                  data.submitted_today ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                }`}
              >
                {data.submitted_today ? 'Reported today' : 'No report submitted today'}
              </div>
              {data.submission_events_today?.length > 1 && (
                <div className="w-full rounded-lg bg-slate-50 dark:bg-slate-900/40 px-3 py-2.5">
                  <p className="text-[11px] font-semibold text-slate-600 dark:text-slate-400 mb-1.5 text-center">Updated {data.submission_events_today.length}&times; today</p>
                  <ul className="space-y-1">
                    {data.submission_events_today.map((ev, i) => (
                      <li key={i} className="text-[10px] leading-snug">
                        <span className="font-medium text-slate-600 dark:text-slate-300">{ev.time_display}</span>
                        {ev.changes.length > 0 ? (
                          <span className="text-slate-500 dark:text-slate-500">
                            {' — '}
                            {ev.changes.map((c, j) => (
                              <span key={j}>
                                {j > 0 && ', '}
                                {c.field} {c.old}&rarr;{c.new}
                              </span>
                            ))}
                          </span>
                        ) : (
                          <span className="text-slate-400 dark:text-slate-500 italic"> — initial submission</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            <div className="lg:col-span-9 grid sm:grid-cols-2 gap-4">
              {BU_QUADRANTS.map((q) => {
                const m = quadrantMetrics[q.key];
                const badgeClass = QUADRANT_BADGE_CLASS[m.status] || QUADRANT_BADGE_CLASS.unknown;
                return (
                  <div key={q.key} className="relative overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 pt-4 shadow-[0_1px_3px_rgba(15,23,42,0.04)] dark:shadow-[0_1px_3px_rgba(0,0,0,0.25)] hover:shadow-lg hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-0.5 transition-all duration-200">
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
                            : 'bg-slate-100 dark:bg-slate-900 text-slate-500 dark:text-slate-400'
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

        <section className={`${GLASS} p-5 sm:p-6`}>
          <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">CEO-Set Benchmark</p>
              <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">
                {data.cumulative_performance
                  ? `Monthly Target vs Cumulative Net Revenue · Day ${data.cumulative_performance.day_number} of ${data.cumulative_performance.days_in_month}`
                  : 'Monthly Target vs Cumulative Net Revenue'}
              </h2>
            </div>
            <div className="flex items-center gap-2">
              {data.cumulative_performance && !targetEditing && (
                <span className={`text-xs font-bold px-3 py-1 rounded-full border ${STATUS_BADGE_CLASS[data.cumulative_performance.status.toLowerCase()]} ${STATUS_TEXT_CLASS[data.cumulative_performance.status.toLowerCase()]}`}>
                  {STATUS_LABEL[data.cumulative_performance.status.toLowerCase()]}
                </span>
              )}
              {!targetEditing && (
                <button
                  type="button"
                  onClick={() => {
                    setTargetForm(
                      data.monthly_target
                        ? {
                            monthly_revenue_target: data.monthly_target.monthly_revenue_target,
                            monthly_cash_flow_target: data.monthly_target.monthly_cash_flow_target,
                            monthly_max_receivable: data.monthly_target.monthly_max_receivable,
                            monthly_max_payable: data.monthly_target.monthly_max_payable,
                          }
                        : emptyTargetForm
                    );
                    setTargetError('');
                    setTargetEditing(true);
                  }}
                  className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300"
                >
                  {data.monthly_target ? 'Override Target' : 'Set Target'}
                </button>
              )}
            </div>
          </div>

          {targetError && (
            <div className="mb-4 rounded-lg bg-red-500/10 border border-red-500/20 dark:border-red-500/30 px-4 py-2.5 text-xs text-red-700 dark:text-red-300">{targetError}</div>
          )}

          {targetEditing ? (
            <form onSubmit={handleOverrideTarget} className="space-y-4">
              {data.monthly_target && (
                <p className="text-xs text-amber-400/90 bg-amber-500/10 border border-amber-500/20 dark:border-amber-500/30 rounded-lg px-3 py-2">
                  This BU's target is normally locked once set by the CEO. Saving here overrides it as Head Office.
                </p>
              )}
              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <div>
                  <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Monthly Revenue Target</label>
                  <input type="number" min="0" step="0.01" value={targetForm.monthly_revenue_target}
                    onChange={(e) => updateTargetField('monthly_revenue_target', e.target.value)}
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-slate-100 placeholder:text-slate-500 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent" placeholder="e.g. 3000000" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Monthly Cash Flow Target</label>
                  <input type="number" step="0.01" value={targetForm.monthly_cash_flow_target}
                    onChange={(e) => updateTargetField('monthly_cash_flow_target', e.target.value)}
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-slate-100 placeholder:text-slate-500 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent" placeholder="e.g. 1500000" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Max Receivable Allowed</label>
                  <input type="number" min="0" step="0.01" value={targetForm.monthly_max_receivable}
                    onChange={(e) => updateTargetField('monthly_max_receivable', e.target.value)}
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-slate-100 placeholder:text-slate-500 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent" placeholder="e.g. 500000" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Max Payable Allowed</label>
                  <input type="number" min="0" step="0.01" value={targetForm.monthly_max_payable}
                    onChange={(e) => updateTargetField('monthly_max_payable', e.target.value)}
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-slate-100 placeholder:text-slate-500 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent" placeholder="e.g. 500000" />
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button type="button" onClick={() => { setTargetEditing(false); setTargetError(''); }} className="text-xs font-semibold text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 px-4 py-2">
                  Cancel
                </button>
                <button type="submit" disabled={targetSaving} className="text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white px-4 py-2 rounded-lg transition-colors disabled:opacity-50">
                  {targetSaving ? 'Saving…' : 'Save Target'}
                </button>
              </div>
            </form>
          ) : data.monthly_target ? (
            <>
              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs mb-4">
                <div>
                  <p className="text-slate-500">Revenue Target</p>
                  <p className="font-semibold text-slate-800 dark:text-slate-200">{money(data.monthly_target.monthly_revenue_target)}/mo</p>
                </div>
                <div>
                  <p className="text-slate-500">Cash Flow Target</p>
                  <p className="font-semibold text-slate-800 dark:text-slate-200">{money(data.monthly_target.monthly_cash_flow_target)}/mo</p>
                </div>
                <div>
                  <p className="text-slate-500">Max Receivable</p>
                  <p className="font-semibold text-slate-800 dark:text-slate-200">{money(data.monthly_target.monthly_max_receivable)}</p>
                </div>
                <div>
                  <p className="text-slate-500">Max Payable</p>
                  <p className="font-semibold text-slate-800 dark:text-slate-200">{money(data.monthly_target.monthly_max_payable)}</p>
                </div>
              </div>
              {data.cumulative_performance ? (
                <div className="pt-4 border-t border-slate-100 dark:border-slate-800">
                  <div className="mb-3">
                    <div className="flex items-baseline justify-between mb-1.5">
                      <p className="text-xl font-bold text-slate-900 dark:text-slate-100 tabular-nums">
                        {data.cumulative_performance.achievement_pct != null ? `${data.cumulative_performance.achievement_pct}%` : '—'}
                      </p>
                      <p className="text-[11px] text-slate-500">of Expected MTD Target ({money(data.cumulative_performance.expected_mtd_target)})</p>
                    </div>
                    <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          data.cumulative_performance.status === 'GREEN' ? 'bg-emerald-500' : data.cumulative_performance.status === 'YELLOW' ? 'bg-amber-500' : 'bg-red-500'
                        }`}
                        style={{ width: `${Math.max(2, Math.min(100, data.cumulative_performance.achievement_pct ?? 0))}%` }}
                      />
                    </div>
                  </div>
                  <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs mb-3">
                    <div className="rounded-lg bg-white dark:bg-slate-900 px-3 py-2.5">
                      <p className="text-slate-500">MTD Revenue</p>
                      <p className="font-bold text-slate-900 dark:text-slate-100 text-base">{money(data.cumulative_performance.mtd_revenue)}</p>
                    </div>
                    <div className="rounded-lg bg-white dark:bg-slate-900 px-3 py-2.5">
                      <p className="text-slate-500">MTD Other Amount</p>
                      <p className="font-bold text-slate-900 dark:text-slate-100 text-base">{money(data.cumulative_performance.mtd_other_amount)}</p>
                    </div>
                    <div className="rounded-lg bg-white dark:bg-slate-900 px-3 py-2.5">
                      <p className="text-slate-500">Cumulative Net Revenue</p>
                      <p className="font-bold text-slate-900 dark:text-slate-100 text-base">{money(data.cumulative_performance.cumulative_net_revenue)}</p>
                    </div>
                    <div className="rounded-lg bg-white dark:bg-slate-900 px-3 py-2.5">
                      <p className="text-slate-500">Variance vs Monthly Target</p>
                      <p className={`font-bold text-base ${data.cumulative_performance.variance_amount > 0 ? 'text-emerald-600 dark:text-emerald-400' : data.cumulative_performance.variance_amount < 0 ? 'text-red-600 dark:text-red-400' : 'text-slate-800 dark:text-slate-200'}`}>
                        {formatVariance(data.cumulative_performance.variance_amount)}
                      </p>
                    </div>
                  </div>
                  {data.cumulative_performance.summary_notes.length > 0 && (
                    <ul className="space-y-1">
                      {data.cumulative_performance.summary_notes.map((flag, i) => (
                        <li key={i} className="text-xs text-slate-600 dark:text-slate-400 flex items-start gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600 mt-1 flex-shrink-0" />
                          {flag}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <p className="text-xs text-slate-500 italic pt-2 border-t border-slate-100 dark:border-slate-800">No revenue submitted yet this month to evaluate against this target.</p>
              )}
            </>
          ) : (
            <p className="text-xs text-slate-500 italic">No monthly target set yet for this business unit.</p>
          )}
        </section>

        {/* RECORD NOTES (read-only — CEOs add these from their own dashboard) */}
        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-100 dark:border-slate-800">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Qualitative Context</p>
            <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Record Notes</h2>
            <p className="text-xs text-slate-500 mt-0.5">Dated notes the CEO has logged this month</p>
          </div>
          <div className="p-6">
            {data.financial_notes.length > 0 ? (
              <ul className="space-y-2.5">
                {data.financial_notes.map((n, i) => (
                  <li key={i} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-4 py-2.5">
                    <p className="text-sm text-slate-800 dark:text-slate-200 whitespace-pre-wrap">{n.note_text}</p>
                    <p className="text-[11px] text-slate-500 mt-1">{n.note_date} &middot; logged {n.created_at}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-slate-500 italic">No notes logged yet this month.</p>
            )}
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between flex-wrap gap-3">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Day-by-Day Review</p>
              <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Daily Snapshot</h2>
              <p className="text-xs text-slate-500 mt-0.5">Step through past submissions one day at a time</p>
            </div>
            {reviewDates.length > 0 && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setReviewIndex((i) => Math.min(i + 1, reviewDates.length - 1))}
                  disabled={reviewIndex >= reviewDates.length - 1}
                  className="text-xs font-semibold bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-800 dark:text-slate-200 px-3 py-2 rounded-lg transition-colors"
                >
                  &larr; Older
                </button>
                <select
                  value={reviewIndex}
                  onChange={(e) => setReviewIndex(Number(e.target.value))}
                  className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm font-semibold text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                >
                  {reviewDates.map((r, i) => (
                    <option key={i} value={i}>{r.report_date}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setReviewIndex((i) => Math.max(i - 1, 0))}
                  disabled={reviewIndex <= 0}
                  className="text-xs font-semibold bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-800 dark:text-slate-200 px-3 py-2 rounded-lg transition-colors"
                >
                  Newer &rarr;
                </button>
              </div>
            )}
          </div>

          {currentReview ? (
            <div className="p-6">
              <p className="text-xs text-slate-500 mb-4">Submitted at {currentReview.submitted_at || '—'}</p>
              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
                  <div className="flex items-center justify-between mb-3">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Business Performance</p>
                    {financialForDay && (
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE_CLASS[financialForDay.status]} ${STATUS_TEXT_CLASS[financialForDay.status]}`}>
                        {STATUS_LABEL[financialForDay.status]}
                      </span>
                    )}
                  </div>
                  {financialForDay ? (
                    <div className="space-y-1.5 text-xs">
                      <div className="flex justify-between"><span className="text-slate-500">Revenue</span><span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{financialForDay.revenue != null ? money(financialForDay.revenue) : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Cash Flow</span><span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{financialForDay.cash_flow != null ? money(financialForDay.cash_flow) : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Receivable</span><span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{financialForDay.receivable != null ? money(financialForDay.receivable) : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Payable</span><span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{financialForDay.payable != null ? money(financialForDay.payable) : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Other Amount</span><span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{financialForDay.other_amount != null ? money(financialForDay.other_amount) : '—'}</span></div>
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500 italic">No submission this day</p>
                  )}
                  {financialForDay?.performance && (
                    <div className="mt-3 pt-3 border-t border-slate-200/70 dark:border-slate-800">
                      <div className="flex items-center justify-between mb-1.5">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Cumulative vs Target</p>
                        <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE_CLASS[financialForDay.performance.status.toLowerCase()]} ${STATUS_TEXT_CLASS[financialForDay.performance.status.toLowerCase()]}`}>
                          {STATUS_LABEL[financialForDay.performance.status.toLowerCase()]}
                        </span>
                      </div>
                      <div className="flex justify-between text-xs">
                        <span className="text-slate-500">Achievement {financialForDay.performance.achievement_pct != null ? `${financialForDay.performance.achievement_pct}%` : '—'}</span>
                        <span className="text-slate-500">MTD {money(financialForDay.performance.cumulative_net_revenue)}</span>
                      </div>
                      <div className="flex justify-between text-[11px] mt-1">
                        <span className={financialForDay.performance.variance_amount > 0 ? 'text-emerald-600 dark:text-emerald-400' : financialForDay.performance.variance_amount < 0 ? 'text-red-600 dark:text-red-400' : 'text-slate-500'}>
                          {formatVariance(financialForDay.performance.variance_amount)} vs monthly target
                        </span>
                      </div>
                    </div>
                  )}
                </div>

                <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Social Activity</p>
                  {socialForDay ? (
                    <div className="space-y-1.5 text-xs">
                      <div className="flex justify-between items-center"><span className="text-slate-500">Event</span><span className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${socialForDay.event_flag ? 'bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-500'}`}>{socialForDay.event_flag ? 'Yes' : 'No'}</span></div>
                      <div className="flex justify-between items-center"><span className="text-slate-500">Meeting</span><span className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${socialForDay.meeting_flag ? 'bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-500'}`}>{socialForDay.meeting_flag ? 'Yes' : 'No'}</span></div>
                      <div className="flex justify-between items-center"><span className="text-slate-500">Golf</span><span className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${socialForDay.golf_flag ? 'bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-500'}`}>{socialForDay.golf_flag ? 'Yes' : 'No'}</span></div>
                      {socialForDay.other_notes && (
                        <div className="pt-1.5 border-t border-slate-200 dark:border-slate-700">
                          <p className="text-slate-500 mb-0.5">Other</p>
                          <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{socialForDay.other_notes}</p>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500 italic">No submission this day</p>
                  )}
                </div>

                <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Daily Activities</p>
                  <div className="space-y-2 text-xs">
                    <div>
                      <p className="text-slate-500 mb-0.5">Meetings</p>
                      <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{currentReview.meetings_attended || '—'}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 mb-0.5">Site Visits</p>
                      <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{currentReview.site_visits || '—'}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 mb-0.5">Key Decisions</p>
                      <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{currentReview.key_decisions || '—'}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 mb-0.5">Next Day Schedule</p>
                      <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{currentReview.next_day_schedule || '—'}</p>
                    </div>
                    <div className="pt-1.5 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                      <span className="text-slate-500">Gym / Swim / Cycle</span>
                      <span className="font-semibold text-slate-800 dark:text-slate-200">
                        {currentReview.gym_percent != null ? `${currentReview.gym_percent}%` : '—'} / {currentReview.swimming_percent != null ? `${currentReview.swimming_percent}%` : '—'} / {currentReview.cycle_percent != null ? `${currentReview.cycle_percent}%` : '—'}
                      </span>
                    </div>
                    {currentReview.other_notes && (
                      <div>
                        <p className="text-slate-500 mb-0.5">Other</p>
                        <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{currentReview.other_notes}</p>
                      </div>
                    )}
                    {safeUrl(currentReview.document_url) && (
                      <a href={safeUrl(currentReview.document_url)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300 pt-1">
                        View attachment
                      </a>
                    )}
                  </div>
                </div>

                <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Health and Fitness</p>
                  {healthForDay ? (
                    <div className="space-y-1.5 text-xs">
                      <div className="flex justify-between"><span className="text-slate-500">Steps</span><span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{healthForDay.daily_steps != null ? healthForDay.daily_steps.toLocaleString() : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Heart Rate</span><span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{healthForDay.heart_rate != null ? `${healthForDay.heart_rate} bpm` : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Sleep Time</span><span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{healthForDay.sleep_hours != null ? `${healthForDay.sleep_hours} hrs` : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Blood Pressure</span><span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{healthForDay.bp_systolic != null && healthForDay.bp_diastolic != null ? `${healthForDay.bp_systolic}/${healthForDay.bp_diastolic}` : '—'}</span></div>
                      {healthForDay.other_notes && (
                        <div className="pt-1.5 border-t border-slate-200 dark:border-slate-700">
                          <p className="text-slate-500 mb-0.5">Other</p>
                          <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{healthForDay.other_notes}</p>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500 italic">No submission this day</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-center text-sm text-slate-500 py-14">No submissions recorded yet for this business unit.</p>
          )}
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-100 dark:border-slate-800">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Business Performance</p>
            <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Business Performance History</h2>
            <p className="text-xs text-slate-500 mt-0.5">Last 30 submissions</p>
          </div>
          <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="sticky top-0 z-[5] bg-white dark:bg-slate-900 text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Date</th>
                  <th className="px-6 py-3 text-left">Submitted At</th>
                  <th className="px-6 py-3 text-right">Revenue</th>
                  <th className="px-6 py-3 text-right">Cash Flow</th>
                  <th className="px-6 py-3 text-right">Receivable</th>
                  <th className="px-6 py-3 text-right">Payable</th>
                  <th className="px-6 py-3 text-right">Other Amount</th>
                  <th className="px-6 py-3 text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {data.financial_rows.map((f, i) => (
                  <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors">
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{f.report_date}</td>
                    <td className="px-6 py-3 text-slate-500 dark:text-slate-400">{f.submitted_at || '—'}</td>
                    <td className="px-6 py-3 text-right font-medium tabular-nums text-slate-800 dark:text-slate-200">{f.revenue != null ? f.revenue.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '—'}</td>
                    <td className="px-6 py-3 text-right font-medium tabular-nums text-slate-800 dark:text-slate-200">{f.cash_flow != null ? f.cash_flow.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '—'}</td>
                    <td className="px-6 py-3 text-right font-medium tabular-nums text-slate-800 dark:text-slate-200">{f.receivable != null ? f.receivable.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '—'}</td>
                    <td className="px-6 py-3 text-right font-medium tabular-nums text-slate-800 dark:text-slate-200">{f.payable != null ? f.payable.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '—'}</td>
                    <td className="px-6 py-3 text-right font-medium tabular-nums text-slate-800 dark:text-slate-200">{f.other_amount != null ? f.other_amount.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '—'}</td>
                    <td className="px-6 py-3 text-center">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE_CLASS[f.status]} ${STATUS_TEXT_CLASS[f.status]}`}>
                        {STATUS_LABEL[f.status]}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.financial_rows.length === 0 && <p className="text-center text-sm text-slate-500 py-10">No financial submissions recorded yet.</p>}
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-100 dark:border-slate-800">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Daily Activities</p>
            <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Daily Activities History</h2>
            <p className="text-xs text-slate-500 mt-0.5">Last 30 submissions &middot; click a row to expand</p>
          </div>
          <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto max-h-[32rem] overflow-y-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="sticky top-0 z-[5] bg-white dark:bg-slate-900 text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Date</th>
                  <th className="px-6 py-3 text-left">Submitted At</th>
                  <th className="px-6 py-3 text-left">Meetings (excerpt)</th>
                  <th className="px-6 py-3 text-center">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {data.activity_rows.map((a, i) => (
                  <Fragment key={i}>
                    <tr className="hover:bg-slate-50 dark:hover:bg-slate-900 cursor-pointer transition-colors" onClick={() => setExpandedRow(expandedRow === i ? null : i)}>
                      <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{a.report_date}</td>
                      <td className="px-6 py-3 text-slate-500">{a.submitted_at || '—'}</td>
                      <td className="px-6 py-3 text-slate-500 max-w-sm truncate">{a.meetings_attended || '—'}</td>
                      <td className="px-6 py-3 text-center">
                        <button type="button" className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300">
                          {expandedRow === i ? 'Hide' : 'View'}
                        </button>
                      </td>
                    </tr>
                    {expandedRow === i && (
                      <tr className="fade-in">
                        <td colSpan={4} className="px-6 py-5 bg-white dark:bg-slate-900">
                          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
                            <div>
                              <p className="text-slate-500 font-semibold uppercase tracking-wide text-[10px] mb-1">Meetings Attended</p>
                              <p className="text-slate-700 dark:text-slate-300">{a.meetings_attended || '—'}</p>
                            </div>
                            <div>
                              <p className="text-slate-500 font-semibold uppercase tracking-wide text-[10px] mb-1">Site Visits / Travel</p>
                              <p className="text-slate-700 dark:text-slate-300">{a.site_visits || '—'}</p>
                            </div>
                            <div>
                              <p className="text-slate-500 font-semibold uppercase tracking-wide text-[10px] mb-1">Key Decisions Made</p>
                              <p className="text-slate-700 dark:text-slate-300">{a.key_decisions || '—'}</p>
                            </div>
                            <div>
                              <p className="text-slate-500 font-semibold uppercase tracking-wide text-[10px] mb-1">Next Day Schedule</p>
                              <p className="text-slate-700 dark:text-slate-300">{a.next_day_schedule || '—'}</p>
                            </div>
                            <div>
                              <p className="text-slate-500 font-semibold uppercase tracking-wide text-[10px] mb-1">Gym / Swimming / Cycle</p>
                              <p className="text-slate-700 dark:text-slate-300">
                                {a.gym_percent != null ? `${a.gym_percent}%` : '—'} / {a.swimming_percent != null ? `${a.swimming_percent}%` : '—'} / {a.cycle_percent != null ? `${a.cycle_percent}%` : '—'}
                              </p>
                            </div>
                          </div>
                          {safeUrl(a.document_url) && (
                            <a href={safeUrl(a.document_url)} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300">
                              View attachment
                            </a>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {data.activity_rows.length === 0 && <p className="text-center text-sm text-slate-500 py-10">No activity submissions recorded yet.</p>}
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-100 dark:border-slate-800">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Health and Fitness</p>
            <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Health and Fitness History</h2>
            <p className="text-xs text-slate-500 mt-0.5">Last 30 submissions</p>
          </div>
          <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="sticky top-0 z-[5] bg-white dark:bg-slate-900 text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Date</th>
                  <th className="px-6 py-3 text-left">Submitted At</th>
                  <th className="px-6 py-3 text-left">Steps</th>
                  <th className="px-6 py-3 text-left">Heart Rate</th>
                  <th className="px-6 py-3 text-left">Sleep Time</th>
                  <th className="px-6 py-3 text-left">Blood Pressure</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {data.health_rows.map((h, i) => (
                  <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors">
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{h.report_date}</td>
                    <td className="px-6 py-3 text-slate-500 dark:text-slate-400">{h.submitted_at || '—'}</td>
                    <td className={`px-6 py-3 font-medium ${h.step_status === 'red' ? 'text-red-600 dark:text-red-400' : h.step_status === 'yellow' ? 'text-amber-600 dark:text-amber-400' : h.step_status === 'green' ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500'}`}>
                      {h.daily_steps != null ? h.daily_steps.toLocaleString() : '—'}
                    </td>
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{h.heart_rate != null ? `${h.heart_rate} bpm` : '—'}</td>
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{h.sleep_hours != null ? `${h.sleep_hours} hrs` : '—'}</td>
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{h.bp_systolic != null && h.bp_diastolic != null ? `${h.bp_systolic}/${h.bp_diastolic}` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.health_rows.length === 0 && <p className="text-center text-sm text-slate-500 py-10">No health submissions recorded yet.</p>}
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-100 dark:border-slate-800">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Social Activity</p>
            <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Social Activity History</h2>
            <p className="text-xs text-slate-500 mt-0.5">Last 30 submissions</p>
          </div>
          <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="sticky top-0 z-[5] bg-white dark:bg-slate-900 text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Date</th>
                  <th className="px-6 py-3 text-left">Submitted At</th>
                  <th className="px-6 py-3 text-center">Event</th>
                  <th className="px-6 py-3 text-center">Meeting</th>
                  <th className="px-6 py-3 text-center">Golf</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {data.social_rows.map((s, i) => (
                  <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors">
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{s.report_date}</td>
                    <td className="px-6 py-3 text-slate-500 dark:text-slate-400">{s.submitted_at || '—'}</td>
                    <td className="px-6 py-3 text-center">
                      <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${s.event_flag ? 'bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-500'}`}>{s.event_flag ? 'Yes' : 'No'}</span>
                    </td>
                    <td className="px-6 py-3 text-center">
                      <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${s.meeting_flag ? 'bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-500'}`}>{s.meeting_flag ? 'Yes' : 'No'}</span>
                    </td>
                    <td className="px-6 py-3 text-center">
                      <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${s.golf_flag ? 'bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-500'}`}>{s.golf_flag ? 'Yes' : 'No'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.social_rows.length === 0 && <p className="text-center text-sm text-slate-500 py-10">No social submissions recorded yet.</p>}
          </div>
        </section>
        <Footer />
      </main>
    </div>
  );
}
