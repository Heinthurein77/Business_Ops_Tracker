import { Fragment, useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import StepsCounter from '../components/StepsCounter';
import YesNoToggle from '../components/YesNoToggle';
import PercentProgress from '../components/PercentProgress';
import QuadrantStatusCircle from '../components/QuadrantStatusCircle';
import Footer from '../components/Footer';
import { useAuth } from '../context/AuthContext';
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
import { validateAttachment } from '../utils/attachmentValidation';

const inputClass =
  'w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 placeholder:text-slate-400 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent';

const STATUS_BADGE_CLASS = {
  red: 'bg-red-500/10 border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400',
  yellow: 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400',
  green: 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400',
  grey: 'bg-slate-50 dark:bg-slate-900 border-slate-200 dark:border-slate-700 text-slate-500',
};
const STATUS_LABEL = { red: 'Critical', yellow: 'Warning', green: 'Good', grey: 'No Data' };

const PERFORMANCE_BADGE_CLASS = {
  RED: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
  YELLOW: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  GREEN: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
};
const PERFORMANCE_LABEL = { RED: 'Critical', YELLOW: 'Warning', GREEN: 'On Track' };

// Same "Cumulative Health" stat-card wording/palette as Executive Dashboard's
// KPI row (AdminDashboardPage), kept under its own name so it doesn't
// collide with this file's own STATUS_LABEL (different wording, used by the
// quadrant cards below).
const KPI_HEALTH_LABEL = { green: 'Optimal', yellow: 'Needs Attention', red: 'Critical Warning', grey: 'No Target Set' };
const KPI_HEALTH_PILL = {
  green: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20 dark:border-emerald-500/30',
  yellow: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20 dark:border-amber-500/30',
  red: 'bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20 dark:border-red-500/30',
  grey: 'bg-slate-500/10 text-slate-500 dark:text-slate-400 border-slate-500/20 dark:border-slate-500/30',
};

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

/** One dashboard for Head Office's own daily report — same shape as a CEO's
 * single dashboard (snapshot + submission form + history all on one page).
 * HO Staff sees the editable form; Chairman sees the same page read-only
 * (mirroring how a CEO's own dashboard vs. the Chairman/HO's Business Unit
 * Detail page relate, but collapsed into one route since both roles already
 * share this sidebar/section). */
export default function HeadOfficePage() {
  const { user } = useAuth();
  const isHo = user?.role === 'HO';

  const [data, setData] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [attachment, setAttachment] = useState(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reviewIndex, setReviewIndex] = useState(0);
  const [targetEditing, setTargetEditing] = useState(false);
  const [targetForm, setTargetForm] = useState(emptyTargetForm);
  const [targetSaving, setTargetSaving] = useState(false);
  const [targetError, setTargetError] = useState('');

  async function load() {
    setTargetForm(emptyTargetForm);
    setTargetEditing(false);
    setTargetError('');
    setReviewIndex(0);
    if (isHo) {
      const payload = await api.hoDashboard();
      setData(payload);
      setForm({
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
      });
    } else {
      setData(await api.headOfficeReport());
    }
  }

  useEffect(() => {
    load().catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load dashboard'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isHo]);

  function updateField(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function updateTargetField(key, value) {
    setTargetForm((f) => ({ ...f, [key]: value }));
  }

  function handleAttachmentChange(e) {
    const file = e.target.files?.[0] || null;
    const validationError = validateAttachment(file);
    if (validationError) {
      setError(validationError);
      e.target.value = '';
      setAttachment(null);
      return;
    }
    setError('');
    setAttachment(file);
  }

  function currentMonthStart() {
    const iso = isHo ? data.report_date : data.today;
    return iso.slice(0, 7) + '-01';
  }

  async function handleSetTarget(e) {
    e.preventDefault();
    setTargetError('');
    const { monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, monthly_max_payable } = targetForm;
    if ([monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, monthly_max_payable].some((v) => v === '')) {
      setTargetError('All four target fields are required.');
      return;
    }
    setTargetSaving(true);
    try {
      await api.setHoMonthlyTarget({
        target_month: currentMonthStart(),
        monthly_revenue_target: Number(monthly_revenue_target),
        monthly_cash_flow_target: Number(monthly_cash_flow_target),
        monthly_max_receivable: Number(monthly_max_receivable),
        monthly_max_payable: Number(monthly_max_payable),
      });
      await load();
    } catch (err) {
      setTargetError(err instanceof ApiError ? err.message : 'Failed to save monthly target');
    } finally {
      setTargetSaving(false);
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
      await api.overrideHoMonthlyTarget({
        target_month: currentMonthStart(),
        monthly_revenue_target: Number(monthly_revenue_target),
        monthly_cash_flow_target: Number(monthly_cash_flow_target),
        monthly_max_receivable: Number(monthly_max_receivable),
        monthly_max_payable: Number(monthly_max_payable),
      });
      setTargetEditing(false);
      await load();
    } catch (err) {
      setTargetError(err instanceof ApiError ? err.message : 'Failed to save monthly target');
    } finally {
      setTargetSaving(false);
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');

    if (form.revenue === '' || form.cash_flow === '' || form.receivable === '' || form.payable === '') {
      setError('All Business Performance fields (Revenue, Cash Flow, Receivable, Payable) are mandatory.');
      return;
    }

    const formData = new FormData();
    formData.append('revenue', form.revenue);
    formData.append('cash_flow', form.cash_flow);
    formData.append('receivable', form.receivable);
    formData.append('payable', form.payable);
    formData.append('other_amount', form.other_amount);
    formData.append('financial_other_notes', form.financial_other_notes);
    formData.append('meetings_attended', form.meetings_attended);
    formData.append('site_visits', form.site_visits);
    formData.append('key_decisions', form.key_decisions);
    formData.append('next_day_schedule', form.next_day_schedule);
    formData.append('gym_percent', form.gym_percent);
    formData.append('swimming_percent', form.swimming_percent);
    formData.append('cycle_percent', form.cycle_percent);
    formData.append('activity_other_notes', form.activity_other_notes);
    formData.append('daily_steps', form.daily_steps);
    formData.append('heart_rate', form.heart_rate);
    formData.append('sleep_hours', form.sleep_hours);
    formData.append('bp_systolic', form.bp_systolic);
    formData.append('bp_diastolic', form.bp_diastolic);
    formData.append('health_other_notes', form.health_other_notes);
    formData.append('event_flag', form.event_flag ? 'true' : 'false');
    formData.append('meeting_flag', form.meeting_flag ? 'true' : 'false');
    formData.append('golf_flag', form.golf_flag ? 'true' : 'false');
    formData.append('social_other_notes', form.social_other_notes);
    if (attachment) formData.append('attachment', attachment);

    setSubmitting(true);
    try {
      await api.submitHoReport(formData);
      setSuccess(true);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to submit report');
    } finally {
      setSubmitting(false);
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
  const reportDateDisplay = isHo
    ? data.report_date_display
    : new Date(data.today + 'T00:00:00').toLocaleDateString('en-GB', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' });
  const submittedToday = isHo ? data.already_submitted_today : data.submitted_today;
  const localHour = new Date().getHours();
  const greeting = localHour < 12 ? 'Good Morning' : localHour < 17 ? 'Good Afternoon' : 'Good Evening';

  // Same buildQuadrantMetrics()/QuadrantStatusCircle design the CEO
  // dashboard and Business Unit Detail pages use — now that HO has a
  // monthly target, Financial uses the identical MTD-vs-target framing.
  const quadrantMetrics = buildQuadrantMetrics({
    financial_status: snapshot.financial_status,
    financial_reason: snapshot.financial_reason,
    cumulative_net_revenue: snapshot.cumulative_net_revenue,
    mtd_revenue: snapshot.mtd_revenue,
    mtd_other_amount: snapshot.mtd_other_amount,
    achievement_pct: snapshot.achievement_pct,
    variance_amount: snapshot.variance_amount,
    budget_status: snapshot.budget_status,
    financial_other_notes: snapshot.financial_other_notes,
    social_status: snapshot.social_status,
    social_reason: snapshot.social_reason,
    activity_status: snapshot.activity_status,
    activity_reason: snapshot.activity_reason,
    health_status: snapshot.health_status,
    health_reason: snapshot.health_reason,
    event_flag: snapshot.event_flag,
    meeting_flag: snapshot.meeting_flag,
    golf_flag: snapshot.golf_flag,
    social_other_notes: snapshot.social_other_notes,
    monthly_event_count: snapshot.monthly_event_count,
    monthly_meeting_count: snapshot.monthly_meeting_count,
    monthly_golf_count: snapshot.monthly_golf_count,
    gym_percent: snapshot.gym_percent,
    swimming_percent: snapshot.swimming_percent,
    cycle_percent: snapshot.cycle_percent,
    activity_other_notes: snapshot.activity_other_notes,
    daily_steps: snapshot.daily_steps,
    heart_rate: snapshot.heart_rate,
    sleep_hours: snapshot.sleep_hours,
    bp_systolic: snapshot.bp_systolic,
    bp_diastolic: snapshot.bp_diastolic,
    health_other_notes: snapshot.health_other_notes,
  });

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
              <span className={`inline-flex items-center gap-1.5 text-sm font-bold px-3 py-1.5 rounded-full border mt-2 w-fit ${KPI_HEALTH_PILL[data.budget_status || 'grey']}`}>
                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: { green: '#10B981', yellow: '#F59E0B', red: '#EF4444', grey: '#94a3b8' }[data.budget_status || 'grey'] }} />
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
            <span>Head Office hasn't submitted today's report yet.</span>
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
              You already submitted today's report at {data.submitted_at_today?.split(', ')[1]}. Submitting again will update it.
            </span>
          </div>
        )}
        {isHo && !submittedToday && !success && new Date().getHours() >= 15 && (
          <div className={`${GLASS} px-5 py-4 text-sm text-amber-800 dark:text-amber-300 flex items-center gap-3 flex-wrap sm:flex-nowrap border-amber-500/30 dark:border-amber-500/30`}>
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            <span className="flex-1">
              <strong>Reminder:</strong> You haven't submitted today's report yet, and it's getting late in the day. Chairman visibility into Head Office depends on it.
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

        {/* SNAPSHOT — same 4-Quadrant Snapshot circle + cards design as the
            CEO's own dashboard and the Admin Dashboard's BU preview (boxed
            circle card, size 240, "reported today" pill), now with a real
            Financial quadrant (daily figures, no monthly target). */}
        <section className={`${GLASS} p-6`}>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">{isHo ? 'Executive Snapshot' : 'HO Submitted Data'}</p>
          <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg mb-5">{isHo ? 'My Status at a Glance' : `4-Quadrant Snapshot — ${data.ho_username}`}</h2>
          <div className="grid lg:grid-cols-12 gap-6 items-start">
            <div className="lg:col-span-3 flex flex-col items-center gap-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-6">
              <QuadrantStatusCircle
                financial={snapshot.financial_status}
                social={snapshot.social_status}
                activity={snapshot.activity_status}
                health={snapshot.health_status}
                size={240}
                {...(!isHo && { label: 'HO' })}
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
                  submittedToday ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                }`}
              >
                {submittedToday ? 'Reported today' : 'No report submitted today'}
              </div>
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

        {/* MONTHLY TARGET — HO sets it once a month (locked after that);
            Chairman can always override, mirroring the business-unit
            benchmark pattern on Business Unit Detail. */}
        <section className={`${GLASS} overflow-hidden`}>
          <div className="px-6 py-4 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-3">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">{isHo ? 'Monthly Target' : 'HO-Set Benchmark'}</p>
              <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">
                {perf ? `Monthly Target vs Cumulative Net Revenue · Day ${perf.day_number} of ${perf.days_in_month}` : 'Monthly Target vs Cumulative Net Revenue'}
              </h2>
            </div>
            {!isHo && (
              <div className="flex items-center gap-2">
                {perf && !targetEditing && (
                  <span className={`text-xs font-bold px-3 py-1 rounded-full border ${STATUS_BADGE_CLASS[perf.status.toLowerCase()]}`}>
                    {STATUS_LABEL[perf.status.toLowerCase()]}
                  </span>
                )}
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
            )}
          </div>

          <div className="p-6">
            {targetError && (
              <div className="mb-4 rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-2.5 text-xs text-red-700 dark:text-red-300">{targetError}</div>
            )}

            {(isHo ? !monthlyTarget : targetEditing) ? (
              <form onSubmit={isHo ? handleSetTarget : handleOverrideTarget} className="space-y-4">
                {!isHo && monthlyTarget && (
                  <p className="text-xs text-amber-400/90 bg-amber-500/10 border border-amber-500/20 dark:border-amber-500/30 rounded-lg px-3 py-2">
                    This target is normally locked once set by HO Staff. Saving here overrides it as Chairman.
                  </p>
                )}
                <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Monthly Revenue Target</label>
                    <input type="number" min="0" step="0.01" value={targetForm.monthly_revenue_target}
                      onChange={(e) => updateTargetField('monthly_revenue_target', e.target.value)}
                      className={inputClass} placeholder="e.g. 3000000" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Monthly Cash Flow Target</label>
                    <input type="number" step="0.01" value={targetForm.monthly_cash_flow_target}
                      onChange={(e) => updateTargetField('monthly_cash_flow_target', e.target.value)}
                      className={inputClass} placeholder="e.g. 1500000" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Max Receivable Allowed</label>
                    <input type="number" min="0" step="0.01" value={targetForm.monthly_max_receivable}
                      onChange={(e) => updateTargetField('monthly_max_receivable', e.target.value)}
                      className={inputClass} placeholder="e.g. 500000" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Max Payable Allowed</label>
                    <input type="number" min="0" step="0.01" value={targetForm.monthly_max_payable}
                      onChange={(e) => updateTargetField('monthly_max_payable', e.target.value)}
                      className={inputClass} placeholder="e.g. 500000" />
                  </div>
                </div>
                {isHo && <p className="text-xs text-slate-500 italic">This can only be set once for the month — double-check the numbers before saving.</p>}
                <div className="flex justify-end gap-2">
                  {!isHo && (
                    <button type="button" onClick={() => { setTargetEditing(false); setTargetError(''); }} className="text-xs font-semibold text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 px-4 py-2">
                      Cancel
                    </button>
                  )}
                  <button type="submit" disabled={targetSaving} className="text-xs font-semibold bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded-lg transition-colors disabled:opacity-50">
                    {targetSaving ? 'Saving…' : 'Save Target'}
                  </button>
                </div>
              </form>
            ) : monthlyTarget ? (
              <>
                <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs mb-4">
                  <div>
                    <p className="text-slate-500">Revenue Target</p>
                    <p className="font-semibold text-slate-800 dark:text-slate-200">{money(monthlyTarget.monthly_revenue_target)}/mo</p>
                  </div>
                  <div>
                    <p className="text-slate-500">Cash Flow Target</p>
                    <p className="font-semibold text-slate-800 dark:text-slate-200">{money(monthlyTarget.monthly_cash_flow_target)}/mo</p>
                  </div>
                  <div>
                    <p className="text-slate-500">Max Receivable</p>
                    <p className="font-semibold text-slate-800 dark:text-slate-200">{money(monthlyTarget.monthly_max_receivable)}</p>
                  </div>
                  <div>
                    <p className="text-slate-500">Max Payable</p>
                    <p className="font-semibold text-slate-800 dark:text-slate-200">{money(monthlyTarget.monthly_max_payable)}</p>
                  </div>
                  {isHo && (
                    <p className="sm:col-span-2 lg:col-span-4 flex items-center gap-1.5 text-[11px] text-slate-500">
                      <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                      </svg>
                      Locked for this month — contact Chairman if it needs to change.
                    </p>
                  )}
                </div>
                {perf ? (
                  <div className="pt-4 border-t border-slate-100 dark:border-slate-800">
                    <div className="mb-3">
                      <div className="flex items-baseline justify-between mb-1.5">
                        <p className="text-xl font-bold text-slate-900 dark:text-slate-100 tabular-nums">
                          {perf.achievement_pct != null ? `${perf.achievement_pct}%` : '—'}
                        </p>
                        <p className="text-[11px] text-slate-500">of Expected MTD Target ({money(perf.expected_mtd_target)})</p>
                      </div>
                      <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${
                            perf.status === 'GREEN' ? 'bg-emerald-500' : perf.status === 'YELLOW' ? 'bg-amber-500' : 'bg-red-500'
                          }`}
                          style={{ width: `${Math.max(2, Math.min(100, perf.achievement_pct ?? 0))}%` }}
                        />
                      </div>
                    </div>
                    <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs mb-3">
                      <div className="rounded-lg bg-white dark:bg-slate-900 px-3 py-2.5">
                        <p className="text-slate-500">MTD Revenue</p>
                        <p className="font-bold text-slate-900 dark:text-slate-100 text-base">{money(perf.mtd_revenue)}</p>
                      </div>
                      <div className="rounded-lg bg-white dark:bg-slate-900 px-3 py-2.5">
                        <p className="text-slate-500">MTD Other Amount</p>
                        <p className="font-bold text-slate-900 dark:text-slate-100 text-base">{money(perf.mtd_other_amount)}</p>
                      </div>
                      <div className="rounded-lg bg-white dark:bg-slate-900 px-3 py-2.5">
                        <p className="text-slate-500">Cumulative Net Revenue</p>
                        <p className="font-bold text-slate-900 dark:text-slate-100 text-base">{money(perf.cumulative_net_revenue)}</p>
                      </div>
                      <div className="rounded-lg bg-white dark:bg-slate-900 px-3 py-2.5">
                        <p className="text-slate-500">Variance vs Monthly Target</p>
                        <p className={`font-bold text-base ${perf.variance_amount > 0 ? 'text-emerald-600 dark:text-emerald-400' : perf.variance_amount < 0 ? 'text-red-600 dark:text-red-400' : 'text-slate-800 dark:text-slate-200'}`}>
                          {formatVariance(perf.variance_amount)}
                        </p>
                      </div>
                    </div>
                    {perf.summary_notes.length > 0 && (
                      <ul className="space-y-1">
                        {perf.summary_notes.map((flag, i) => (
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
              <p className="text-xs text-slate-500 italic">No monthly target set yet for Head Office.</p>
            )}
          </div>
        </section>

        {/* SUBMISSION FORM — HO Staff only */}
        {isHo && (
          <form id="daily-report-form" onSubmit={handleSubmit} className="space-y-8">
            {/* BUSINESS PERFORMANCE BLOCK */}
            <section className={`${GLASS} overflow-hidden`}>
              <div className="px-6 py-4 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600 dark:text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight">Business Performance</h2>
                </div>
                <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400">Mandatory</span>
              </div>
              <div className="p-6 grid sm:grid-cols-2 lg:grid-cols-5 gap-5">
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">Revenue</label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                    <input type="number" step="0.01" min="0" value={form.revenue} onChange={(e) => updateField('revenue', e.target.value)} className={`${inputClass} pl-7`} placeholder="0.00" />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">Cash Flow</label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                    <input type="number" step="0.01" value={form.cash_flow} onChange={(e) => updateField('cash_flow', e.target.value)} className={`${inputClass} pl-7`} placeholder="0.00" />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">Receivable</label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                    <input type="number" step="0.01" min="0" value={form.receivable} onChange={(e) => updateField('receivable', e.target.value)} className={`${inputClass} pl-7`} placeholder="0.00" />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">Payable</label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                    <input type="number" step="0.01" min="0" value={form.payable} onChange={(e) => updateField('payable', e.target.value)} className={`${inputClass} pl-7`} placeholder="0.00" />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                    Other Amount <span className="text-slate-400 font-normal">(optional)</span>
                  </label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                    <input type="number" step="0.01" value={form.other_amount} onChange={(e) => updateField('other_amount', e.target.value)} className={`${inputClass} pl-7`} placeholder="0.00" />
                  </div>
                </div>
              </div>
              <div className="px-6 pb-6">
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                  Other <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <textarea rows={2} placeholder="Anything else about today's financials..." value={form.financial_other_notes} onChange={(e) => updateField('financial_other_notes', e.target.value)} className={inputClass} />
              </div>
            </section>

            <section className={`${GLASS} overflow-hidden`}>
              <div className="px-6 py-4 border-b border-slate-200/70 dark:border-slate-800 flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600 dark:text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight">Daily Activities</h2>
              </div>
              <div className="p-6 grid sm:grid-cols-2 gap-5">
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">Meetings Attended</label>
                  <textarea rows={4} placeholder="Describe key meetings attended today..." value={form.meetings_attended} onChange={(e) => updateField('meetings_attended', e.target.value)} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">Site Visits / Travel</label>
                  <textarea rows={4} placeholder="Describe site visits or travel undertaken..." value={form.site_visits} onChange={(e) => updateField('site_visits', e.target.value)} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">Key Decisions Made</label>
                  <textarea rows={4} placeholder="Summarize key decisions made today..." value={form.key_decisions} onChange={(e) => updateField('key_decisions', e.target.value)} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">Next Day Schedule</label>
                  <textarea rows={4} placeholder="Outline tomorrow's schedule..." value={form.next_day_schedule} onChange={(e) => updateField('next_day_schedule', e.target.value)} className={inputClass} />
                </div>
              </div>
              <div className="px-6 pb-6 grid sm:grid-cols-3 gap-5">
                <PercentProgress label="Gym %" value={form.gym_percent} onChange={(v) => updateField('gym_percent', v)} color="brand" />
                <PercentProgress label="Swimming %" value={form.swimming_percent} onChange={(v) => updateField('swimming_percent', v)} color="emerald" />
                <PercentProgress label="Cycle %" value={form.cycle_percent} onChange={(v) => updateField('cycle_percent', v)} color="amber" />
              </div>
              <div className="px-6 pb-6">
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                  Other <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <textarea rows={2} placeholder="Anything else about today's activity..." value={form.activity_other_notes} onChange={(e) => updateField('activity_other_notes', e.target.value)} className={inputClass} />
              </div>
              <div className="px-6 pb-6">
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                  Supporting Document <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <input type="file" onChange={handleAttachmentChange} className="block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-brand-500/10 file:text-brand-600 hover:file:bg-brand-500/20" />
                {safeUrl(data.activity?.document_url) && (
                  <p className="mt-2 text-xs text-slate-500">
                    Current file: <a href={safeUrl(data.activity.document_url)} target="_blank" rel="noreferrer" className="text-brand-600 dark:text-brand-400 hover:underline">View attachment</a>
                  </p>
                )}
              </div>
            </section>

            <section className={`${GLASS} overflow-hidden`}>
              <div className="px-6 py-4 border-b border-slate-200/70 dark:border-slate-800 flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600 dark:text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
                  </svg>
                </div>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight">Health and Fitness</h2>
              </div>
              <div className="p-6 grid sm:grid-cols-2 lg:grid-cols-4 gap-8">
                <StepsCounter value={form.daily_steps} onChange={(v) => updateField('daily_steps', v)} />
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                    Heart Rate <span className="text-slate-400 font-normal">(bpm, optional)</span>
                  </label>
                  <div className="relative">
                    <input type="number" min="0" value={form.heart_rate} onChange={(e) => updateField('heart_rate', e.target.value)} placeholder="e.g. 72" className={`${inputClass} pr-14`} />
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs font-medium">bpm</span>
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                    Sleep Time <span className="text-slate-400 font-normal">(hours, optional)</span>
                  </label>
                  <div className="relative">
                    <input type="number" min="0" max="24" step="0.5" value={form.sleep_hours} onChange={(e) => updateField('sleep_hours', e.target.value)} placeholder="e.g. 7.5" className={`${inputClass} pr-12`} />
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs font-medium">hrs</span>
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                    Blood Pressure <span className="text-slate-400 font-normal">(mmHg, optional)</span>
                  </label>
                  <div className="flex items-center gap-2">
                    <input type="number" min="0" value={form.bp_systolic} onChange={(e) => updateField('bp_systolic', e.target.value)} placeholder="120" className={inputClass} />
                    <span className="text-slate-400 text-sm font-medium flex-shrink-0">/</span>
                    <input type="number" min="0" value={form.bp_diastolic} onChange={(e) => updateField('bp_diastolic', e.target.value)} placeholder="80" className={inputClass} />
                  </div>
                </div>
              </div>
              <div className="px-6 pb-6">
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                  Other <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <textarea rows={2} placeholder="Anything else about today's health/well-being..." value={form.health_other_notes} onChange={(e) => updateField('health_other_notes', e.target.value)} className={inputClass} />
              </div>
            </section>

            <section className={`${GLASS} overflow-hidden`}>
              <div className="px-6 py-4 border-b border-slate-200/70 dark:border-slate-800 flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600 dark:text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-2.13a4 4 0 10-4-4 4 4 0 004 4zm6 0a4 4 0 10-4-4" />
                  </svg>
                </div>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight">Social Activity</h2>
              </div>
              <div className="p-6 grid sm:grid-cols-3 gap-5">
                <YesNoToggle label="Event Attended" value={form.event_flag} onChange={(v) => updateField('event_flag', v)} />
                <YesNoToggle label="Meeting Attended" value={form.meeting_flag} onChange={(v) => updateField('meeting_flag', v)} />
                <YesNoToggle label="Golf" value={form.golf_flag} onChange={(v) => updateField('golf_flag', v)} />
              </div>
              <div className="px-6 pb-6">
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                  Other <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <textarea rows={2} placeholder="Anything else about today's social engagement..." value={form.social_other_notes} onChange={(e) => updateField('social_other_notes', e.target.value)} className={inputClass} />
              </div>
            </section>

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
        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between flex-wrap gap-3">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Day-by-Day Review</p>
              <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Daily Snapshot</h2>
              <p className="text-xs text-slate-500 mt-0.5">Step through past submissions one day at a time</p>
            </div>
            {data.activity_rows.length > 0 && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setReviewIndex((i) => Math.min(i + 1, data.activity_rows.length - 1))}
                  disabled={reviewIndex >= data.activity_rows.length - 1}
                  className="text-xs font-semibold bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-800 dark:text-slate-200 px-3 py-2 rounded-lg transition-colors"
                >
                  &larr; Older
                </button>
                <select
                  value={reviewIndex}
                  onChange={(e) => setReviewIndex(Number(e.target.value))}
                  className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm font-semibold text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                >
                  {data.activity_rows.map((r, i) => (
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

          {(() => {
            const currentReview = data.activity_rows[reviewIndex] || null;
            if (!currentReview) {
              return <p className="text-center text-sm text-slate-500 py-14">No submissions recorded yet for Head Office.</p>;
            }
            const financialForDay = data.financial_rows.find((f) => f.report_date === currentReview.report_date);
            const healthForDay = data.health_rows.find((h) => h.report_date === currentReview.report_date);
            const socialForDay = data.social_rows.find((s) => s.report_date === currentReview.report_date);
            return (
              <div className="p-6">
                <p className="text-xs text-slate-500 mb-4">Submitted at {currentReview.submitted_at || '—'}</p>
                <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
                    <div className="flex items-center justify-between mb-3">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Business Performance</p>
                      {financialForDay && (
                        <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE_CLASS[financialForDay.status]}`}>
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
                          <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE_CLASS[financialForDay.performance.status.toLowerCase()]}`}>
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
            );
          })()}
        </section>

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
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE_CLASS[f.status]}`}>
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
            {safeUrl(a.document_url) && (
              <a href={safeUrl(a.document_url)} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300">
                View attachment
              </a>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}
