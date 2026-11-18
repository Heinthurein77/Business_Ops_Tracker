import { Fragment, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import LastUpdated from '../components/LastUpdated';
import { validateAttachment } from '../utils/attachmentValidation';
import { money, formatDateLong, localHour, localTodayISO } from '../utils/format';
import { formatVariance, buildQuadrantMetrics } from '../utils/quadrantMetrics';
import { GLASS, PAGE_BG } from '../utils/glassTheme';
import { viewUploadUrl } from '../utils/safeUrl';
import { useAuth } from '../context/AuthContext';
import Footer from '../components/Footer';
import StatusPill from '../components/StatusPill';
import { KPI_HEALTH_LABEL, STATUS_COLOR } from '../utils/status';
import useDailyReport from '../hooks/useDailyReport';
import QuadrantSnapshot from '../components/QuadrantSnapshot';
import MonthlyTargetSection from '../components/MonthlyTargetSection';
import DayDetailPanel from '../components/DayDetailPanel';
import { FinancialSection, ActivitySection, HealthSection, SocialSection } from '../components/DailyReportFormSections';

const emptyTargetForm = {
  monthly_revenue_target: '',
  monthly_cash_flow_target: '',
  monthly_max_receivable: '',
  monthly_max_payable: '',
};

const emptyForm = {
  revenue: '',
  cash_flow: '',
  receivable: '',
  payable: '',
  other_amount: '',
  financial_other_notes: '',
  meetings_attended: '',
  site_visits: '',
  key_decisions: '',
  next_day_schedule: '',
  gym_percent: '',
  swimming_percent: '',
  cycle_percent: '',
  activity_other_notes: '',
  daily_steps: '',
  heart_rate: '',
  sleep_hours: '',
  bp_systolic: '',
  bp_diastolic: '',
  health_other_notes: '',
  event_flag: false,
  meeting_flag: false,
  golf_flag: false,
  social_other_notes: '',
};

// Form key → FormData name, in submit order — serialised by iterating
// this map (useDailyReport) instead of 25 hand-written append() calls.
const FIELD_MAP = {
  revenue: 'revenue',
  cash_flow: 'cash_flow',
  receivable: 'receivable',
  payable: 'payable',
  other_amount: 'other_amount',
  financial_other_notes: 'financial_other_notes',
  meetings_attended: 'meetings_attended',
  site_visits: 'site_visits',
  key_decisions: 'key_decisions',
  next_day_schedule: 'next_day_schedule',
  gym_percent: 'gym_percent',
  swimming_percent: 'swimming_percent',
  cycle_percent: 'cycle_percent',
  activity_other_notes: 'activity_other_notes',
  daily_steps: 'daily_steps',
  heart_rate: 'heart_rate',
  sleep_hours: 'sleep_hours',
  bp_systolic: 'bp_systolic',
  bp_diastolic: 'bp_diastolic',
  health_other_notes: 'health_other_notes',
  event_flag: 'event_flag',
  meeting_flag: 'meeting_flag',
  golf_flag: 'golf_flag',
  social_other_notes: 'social_other_notes',
};

function mapForm(payload) {
  return {
    revenue: payload.financial?.revenue ?? '',
    cash_flow: payload.financial?.cash_flow ?? '',
    receivable: payload.financial?.receivable ?? '',
    payable: payload.financial?.payable ?? '',
    other_amount: payload.financial?.other_amount ?? '',
    financial_other_notes: payload.financial?.other_notes ?? '',
    meetings_attended: payload.activity?.meetings_attended ?? '',
    site_visits: payload.activity?.site_visits ?? '',
    key_decisions: payload.activity?.key_decisions ?? '',
    next_day_schedule: payload.activity?.next_day_schedule ?? '',
    gym_percent: payload.activity?.gym_percent ?? '',
    swimming_percent: payload.activity?.swimming_percent ?? '',
    cycle_percent: payload.activity?.cycle_percent ?? '',
    activity_other_notes: payload.activity?.other_notes ?? '',
    daily_steps: payload.health?.daily_steps ?? '',
    heart_rate: payload.health?.heart_rate ?? '',
    sleep_hours: payload.health?.sleep_hours ?? '',
    bp_systolic: payload.health?.bp_systolic ?? '',
    bp_diastolic: payload.health?.bp_diastolic ?? '',
    health_other_notes: payload.health?.other_notes ?? '',
    event_flag: payload.social?.event_flag ?? false,
    meeting_flag: payload.social?.meeting_flag ?? false,
    golf_flag: payload.social?.golf_flag ?? false,
    social_other_notes: payload.social?.other_notes ?? '',
  };
}

/** One dashboard for Head Office's own daily report — same shape as a CEO's
 * single dashboard (snapshot + submission form + history all on one page).
 * HO Staff sees the editable form; Chairman sees the same page read-only
 * (mirroring how a CEO's own dashboard vs. the Chairman/HO's Business Unit
 * Detail page relate, but collapsed into one route since both roles already
 * share this sidebar/section). */
export default function HeadOfficePage() {
  const { user } = useAuth();
  const isHo = user?.role === 'HO';
  // PM-m22: same 7-day backfill affordance as CEOs — /admin/head-office
  // (or an HO user) can open ?date=YYYY-MM-DD to file/correct a past day.
  const [searchParams] = useSearchParams();
  const editDate = searchParams.get('date') || '';

  const [reviewIndex, setReviewIndex] = useState(0);
  const [targetEditing, setTargetEditing] = useState(false);

  const {
    data, form, error, refreshError, success, submitting,
    updatedAt, refreshing, targetForm, setTargetForm, targetSaving, targetError, setTargetError,
    load, updateField, updateTargetField, handleAttachmentChange,
    handleSetTarget, handleSubmit, reloadAfterSave,
  } = useDailyReport({
    emptyForm,
    emptyTargetForm,
    draftKey: 'ho-daily-report',
    fetchDashboard: () => (isHo ? api.hoDashboard(editDate) : api.headOfficeReport()),
    mapForm,
    // HO drafts are stamped with the form date (today, or the ?date=
    // backfill day) — not the UTC date.
    draftDate: (payload) => payload.form_date || localTodayISO(),
    isFormEmpty: (f) => f.revenue === '' && f.cash_flow === '',
    fieldMap: FIELD_MAP,
    submitReport: (fd) => api.submitHoReport(fd, editDate || undefined),
    setTargetApi: (payload) => api.setHoMonthlyTarget(payload),
    targetMonth: (d) => (isHo ? d.report_date : d.today).slice(0, 7) + '-01',
    onLoadExtra: () => {
      setTargetForm(emptyTargetForm);
      setTargetEditing(false);
      setTargetError('');
      setReviewIndex(0);
    },
    loadDeps: [isHo, editDate],
  });

  // Chairman-only: override the HO-set monthly target. Kept page-local
  // (the hook's set-target path is the HO-staff one); a reload failure
  // after a successful override is reported as a refresh problem, not a
  // save failure (F-m3), via the hook's reloadAfterSave.
  async function handleOverrideTarget(e) {
    e.preventDefault();
    setTargetError('');
    const { monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, monthly_max_payable } = targetForm;
    if ([monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, monthly_max_payable].some((v) => v === '')) {
      setTargetError('All four target fields are required.');
      return;
    }
    try {
      await api.overrideHoMonthlyTarget({
        target_month: (isHo ? data.report_date : data.today).slice(0, 7) + '-01',
        monthly_revenue_target: Number(monthly_revenue_target),
        monthly_cash_flow_target: Number(monthly_cash_flow_target),
        monthly_max_receivable: Number(monthly_max_receivable),
        monthly_max_payable: Number(monthly_max_payable),
      });
      setTargetEditing(false);
      await reloadAfterSave();
    } catch (err) {
      setTargetError(err instanceof ApiError ? err.message : 'Failed to save monthly target');
    }
  }

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  // Normalize the two backend shapes (HO's own /api/ho/dashboard vs.
  // Chairman's read-only /api/admin/head-office) into one set of fields the
  // rest of this page renders from.
  const snapshot = isHo ? data.quadrant : data;
  const reportDateDisplay = isHo ? data.report_date_display : formatDateLong(data.today);
  const submittedToday = isHo ? data.already_submitted_today : data.submitted_today;
  const localHourNow = localHour();
  const greeting = localHourNow < 12 ? 'Good Morning' : localHourNow < 17 ? 'Good Afternoon' : 'Good Evening';
  const quadrantMetrics = buildQuadrantMetrics(snapshot);
  const perf = data.cumulative_performance;
  const monthlyTarget = data.monthly_target;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 pb-24 space-y-6 fade-in">
        {isHo ? (
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Head Office</p>
              <h1 className="font-bold text-slate-900 dark:text-slate-100 text-xl tracking-tight">My Daily Report</h1>
              <p className="text-xs text-slate-500 mt-0.5">{reportDateDisplay}</p>
            </div>
            {submittedToday ? (
              data.submitted_at_today && <p className="text-xs text-emerald-600 dark:text-emerald-400">Submitted today at {data.submitted_at_today.split(', ')[1]}</p>
            ) : (
              <p className="text-xs text-amber-600 dark:text-amber-400">Not submitted yet today</p>
            )}
            {/* SM-5: visible freshness + manual refresh for this dashboard. */}
            <LastUpdated updatedAt={updatedAt} onRefresh={load} refreshing={refreshing} />
          </div>
        ) : (
          // Same greeting top-bar treatment as Executive Dashboard, so
          // Chairman's view of HO reads as the same "kind" of page as their
          // view of any business unit.
          <section className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
            <div>
              <h1 className="font-bold text-slate-900 dark:text-slate-100 text-xl tracking-tight">{greeting}, {user?.username} 👋</h1>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{reportDateDisplay} &middot; Head Office</p>
            </div>
          </section>
        )}

        {!isHo && (
          <section className="grid sm:grid-cols-2 lg:grid-cols-4 gap-5">
            <div className={`${GLASS} p-5`}>
              <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">MTD Revenue</p>
              <p className="font-bold text-slate-900 dark:text-slate-100 text-2xl mt-2">{money(data.mtd_revenue)}</p>
              <p className="text-[11px] font-medium mt-1.5 text-brand-600 dark:text-brand-400">Month-to-date</p>
            </div>
            <div className={`${GLASS} p-5`}>
              <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Cash Flow</p>
              <p className="font-bold text-slate-900 dark:text-slate-100 text-2xl mt-2">{money(data.cash_flow)}</p>
              <p className="text-[11px] font-medium mt-1.5 text-emerald-600 dark:text-emerald-400">{reportDateDisplay}</p>
            </div>
            <div className={`${GLASS} p-5`}>
              <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Receivable</p>
              <p className="font-bold text-slate-900 dark:text-slate-100 text-2xl mt-2">{money(data.receivable)}</p>
              <p className="text-[11px] font-medium mt-1.5 text-amber-600 dark:text-amber-400">{reportDateDisplay}</p>
            </div>
            <div className={`${GLASS} p-5 flex flex-col justify-between`}>
              <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Cumulative Health</p>
              <span className="inline-flex items-center gap-1.5 text-sm font-bold px-3 py-1.5 rounded-full border mt-2 w-fit">
                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: STATUS_COLOR[data.budget_status || 'grey'] }} />
                {data.achievement_pct != null ? `${data.achievement_pct}%` : '—'} &middot; {KPI_HEALTH_LABEL[data.budget_status || 'grey']}
              </span>
              <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1.5">vs Monthly Target: {data.variance_amount != null ? formatVariance(data.variance_amount) : '—'}</p>
            </div>
          </section>
        )}

        {!isHo && !submittedToday && (
          <div className={`${GLASS} px-5 py-4 text-sm text-red-700 dark:text-red-400 flex items-center gap-3 border-red-500/20 dark:border-red-500/30`}>
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-red-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <span>Head Office hasn&apos;t submitted today&apos;s report yet.</span>
          </div>
        )}
        {isHo && data.form_is_today === false && (
          <div className="rounded-xl bg-brand-500/10 border border-brand-500/20 px-5 py-4 text-sm text-slate-700 flex items-center gap-3 flex-wrap sm:flex-nowrap">
            <span className="flex-1">
              You’re filling the report for <strong>{reportDateDisplay}</strong>, opened for a past day.
              Saving updates that day — not today.
            </span>
            <Link
              to="/admin/head-office"
              className="shrink-0 text-xs font-bold text-brand-700 bg-brand-500/15 hover:bg-brand-500/25 px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap"
            >
              Back to today
            </Link>
          </div>
        )}
        {isHo && success && (
          <div className={`${GLASS} px-5 py-4 text-sm text-emerald-700 dark:text-emerald-400 flex items-center gap-3`}>
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-emerald-600 dark:text-emerald-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            <span>Your daily report for {reportDateDisplay} has been submitted successfully.</span>
          </div>
        )}
        {refreshError && (
          <div className={`${GLASS} px-5 py-4 text-sm text-amber-700 dark:text-amber-300 flex items-center gap-3`}>
            <span>{refreshError}</span>
          </div>
        )}
        {error && (
          <div className={`${GLASS} px-5 py-4 text-sm text-red-700 dark:text-red-400 flex items-center gap-3`}>
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-red-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>{error}</span>
          </div>
        )}
        {isHo && data.already_submitted_today && !success && (
          <div className={`${GLASS} px-5 py-4 text-sm text-slate-600 dark:text-slate-400 flex items-center gap-3`}>
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-slate-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>
              You already submitted today&apos;s report at {data.submitted_at_today?.split(', ')[1]}. Submitting again will update it.
            </span>
          </div>
        )}
        {isHo && !submittedToday && !success && localHourNow >= 15 && (
          <div className={`${GLASS} px-5 py-4 text-sm text-amber-800 dark:text-amber-300 flex items-center gap-3 flex-wrap sm:flex-nowrap border-amber-500/30 dark:border-amber-500/30`}>
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            <span className="flex-1">
              <strong>Reminder:</strong> You haven&apos;t submitted today&apos;s report yet, and it&apos;s getting late in the day. Chairman visibility into Head Office depends on it.
            </span>
            <a href="#daily-report-form" className="shrink-0 text-xs font-bold text-amber-900 dark:text-amber-200 bg-amber-200/70 dark:bg-amber-500/20 hover:bg-amber-200 dark:hover:bg-amber-500/30 px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap">
              Submit Now ↓
            </a>
          </div>
        )}
        {data.submission_events_today?.length > 1 && (
          <div className={`${GLASS} px-4 py-3`}>
            <p className="text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2">Updated {data.submission_events_today.length}&times; today</p>
            <ul className="space-y-1.5">
              {data.submission_events_today.map((ev, i) => (
                <li key={i} className="text-xs text-slate-600 dark:text-slate-400 flex flex-wrap gap-x-1.5">
                  <span className="font-medium text-slate-700 dark:text-slate-300 flex-shrink-0">{ev.time_display}</span>
                  {ev.changes.length > 0 ? (
                    <span>
                      {ev.changes.map((c, j) => (
                        <span key={j}>
                          {j > 0 && ', '}
                          {c.field} {c.old} &rarr; {c.new}
                        </span>
                      ))}
                    </span>
                  ) : (
                    <span className="text-slate-400 dark:text-slate-500 italic">initial submission</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        <QuadrantSnapshot
          quadrant={snapshot}
          quadrantMetrics={quadrantMetrics}
          businessUnitName={isHo ? undefined : data.ho_username}
          alreadySubmitted={submittedToday}
          eyebrow={isHo ? 'Executive Snapshot' : 'HO Submitted Data'}
          title={isHo ? 'My Status at a Glance' : `4-Quadrant Snapshot — ${data.ho_username}`}
        />

        <MonthlyTargetSection
          eyebrow={isHo ? 'Monthly Target' : 'HO-Set Benchmark'}
          title={perf ? `Monthly Target vs Cumulative Net Revenue · Day ${perf.day_number} of ${perf.days_in_month}` : 'Monthly Target vs Cumulative Net Revenue'}
          monthlyTarget={monthlyTarget}
          perf={perf}
          targetForm={targetForm}
          updateTargetField={updateTargetField}
          targetSaving={targetSaving}
          targetError={targetError}
          onSubmit={isHo ? handleSetTarget : handleOverrideTarget}
          showForm={isHo ? !monthlyTarget : targetEditing}
          formNote={isHo ? 'This can only be set once for the month — double-check the numbers before saving.' : undefined}
          formAlert={!isHo && monthlyTarget ? (
            <p className="text-xs text-amber-400/90 bg-amber-500/10 border border-amber-500/20 dark:border-amber-500/30 rounded-lg px-3 py-2">
              This target is normally locked once set by HO Staff. Saving here overrides it as Chairman.
            </p>
          ) : undefined}
          lockNote={isHo ? 'Locked for this month — contact Chairman if it needs to change.' : undefined}
          onCancel={!isHo ? () => { setTargetEditing(false); setTargetError(''); } : undefined}
          headerExtra={!isHo ? (
            <div className="flex items-center gap-2">
              {perf && !targetEditing && <StatusPill status={perf.status.toLowerCase()} />}
              {!targetEditing && (
                <button
                  type="button"
                  onClick={() => {
                    setTargetForm(
                      monthlyTarget
                        ? {
                            monthly_revenue_target: monthlyTarget.monthly_revenue_target,
                            monthly_cash_flow_target: monthlyTarget.monthly_cash_flow_target,
                            monthly_max_receivable: monthlyTarget.monthly_max_receivable,
                            monthly_max_payable: monthlyTarget.monthly_max_payable,
                          }
                        : emptyTargetForm
                    );
                    setTargetError('');
                    setTargetEditing(true);
                  }}
                  className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300"
                >
                  {monthlyTarget ? 'Override Target' : 'Set Target'}
                </button>
              )}
            </div>
          ) : undefined}
        />

        {/* SUBMISSION FORM — HO Staff only */}
        {isHo && (
          <form
            id="daily-report-form"
            onSubmit={(e) => handleSubmit(e, {
              validate: (f) => (f.revenue === '' || f.cash_flow === '' || f.receivable === '' || f.payable === '')
                ? 'All Business Performance fields (Revenue, Cash Flow, Receivable, Payable) are mandatory.'
                : null,
            })}
            className="space-y-8"
          >
            <FinancialSection form={form} updateField={updateField} />
            <ActivitySection
              form={form}
              updateField={updateField}
              data={data}
              showPercents
              scheduleLabel="Next Day Schedule"
              schedulePlaceholder="Outline tomorrow's schedule..."
              attachmentUrl={viewUploadUrl(data.activity?.document_url)}
              onAttachmentChange={(e) => handleAttachmentChange(e, validateAttachment)}
            />
            <HealthSection form={form} updateField={updateField} />
            <SocialSection form={form} updateField={updateField} />

            <div className="flex items-center justify-end gap-3">
              <p className="text-xs text-slate-500 mr-auto">All entries are recorded against {reportDateDisplay}.</p>
              <button
                type="submit"
                disabled={submitting}
                className="rounded-xl bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-500 hover:to-indigo-500 text-white font-semibold px-8 py-3 text-sm shadow-lg shadow-brand-600/30 transition-all disabled:opacity-50"
              >
                {submitting ? 'Submitting…' : data.already_submitted_today ? 'Update Daily Report' : 'Submit Daily Report'}
              </button>
            </div>
          </form>
        )}

        {/* DAY-BY-DAY REVIEW — steps through past submissions across all 4
            areas at once, same widget as Business Unit Detail / CEO Dashboard. */}
        <DayDetailPanel
          activityRows={data.activity_rows || []}
          financialRows={data.financial_rows || []}
          healthRows={data.health_rows || []}
          socialRows={data.social_rows || []}
          reviewIndex={reviewIndex}
          onReviewIndexChange={setReviewIndex}
          emptyText="No submissions recorded yet for Head Office."
        />

        {/* HISTORY — shared by both roles */}
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
                      <StatusPill status={f.status} />
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
            <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Activities History</h2>
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
                  <HistoryRow key={i} a={a} />
                ))}
              </tbody>
            </table>
            {data.activity_rows.length === 0 && <p className="text-center text-sm text-slate-500 py-10">No activity submissions recorded yet.</p>}
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-100 dark:border-slate-800">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Health and Fitness</p>
            <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Health History</h2>
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
                    <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{h.daily_steps != null ? h.daily_steps.toLocaleString() : '—'}</td>
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
            <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Social History</h2>
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

function HistoryRow({ a }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Fragment>
      <tr className="hover:bg-slate-50 dark:hover:bg-slate-900 cursor-pointer transition-colors" onClick={() => setExpanded((v) => !v)}>
        <td className="px-6 py-3 text-slate-700 dark:text-slate-300">{a.report_date}</td>
        <td className="px-6 py-3 text-slate-500">{a.submitted_at || '—'}</td>
        <td className="px-6 py-3 text-slate-500 max-w-sm truncate">{a.meetings_attended || '—'}</td>
        <td className="px-6 py-3 text-center">
          <button type="button" className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300">
            {expanded ? 'Hide' : 'View'}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="fade-in">
          <td colSpan={4} className="px-6 py-5 bg-white dark:bg-slate-900">
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
              <div><p className="text-slate-500 font-semibold uppercase tracking-wide text-[10px] mb-1">Meetings Attended</p><p className="text-slate-700 dark:text-slate-300">{a.meetings_attended || '—'}</p></div>
              <div><p className="text-slate-500 font-semibold uppercase tracking-wide text-[10px] mb-1">Site Visits / Travel</p><p className="text-slate-700 dark:text-slate-300">{a.site_visits || '—'}</p></div>
              <div><p className="text-slate-500 font-semibold uppercase tracking-wide text-[10px] mb-1">Key Decisions Made</p><p className="text-slate-700 dark:text-slate-300">{a.key_decisions || '—'}</p></div>
              <div><p className="text-slate-500 font-semibold uppercase tracking-wide text-[10px] mb-1">Next Day Schedule</p><p className="text-slate-700 dark:text-slate-300">{a.next_day_schedule || '—'}</p></div>
            </div>
            {viewUploadUrl(a.document_url) && (
              <a href={viewUploadUrl(a.document_url)} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300">
                View attachment
              </a>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}
