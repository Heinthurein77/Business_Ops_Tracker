import { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import CeoProfileCard from '../components/CeoProfileCard';
import StepsCounter from '../components/StepsCounter';
import YesNoToggle from '../components/YesNoToggle';
import PercentProgress from '../components/PercentProgress';
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
import { validateAttachment } from '../utils/attachmentValidation';

const emptyTargetForm = {
  monthly_revenue_target: '',
  monthly_cash_flow_target: '',
  monthly_max_receivable: '',
  monthly_max_payable: '',
};

const PERFORMANCE_BADGE_CLASS = {
  RED: 'bg-red-500/10 border-red-500/20 text-red-600',
  YELLOW: 'bg-amber-500/10 border-amber-500/20 text-amber-600',
  GREEN: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600',
};
const PERFORMANCE_LABEL = { RED: 'Critical', YELLOW: 'Warning', GREEN: 'On Track' };

const inputClass =
  'w-full rounded-lg border border-slate-200 bg-white text-slate-800 placeholder:text-slate-400 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent';

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

export default function CeoDashboardPage() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [attachment, setAttachment] = useState(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [targetForm, setTargetForm] = useState(emptyTargetForm);
  const [targetSaving, setTargetSaving] = useState(false);
  const [targetError, setTargetError] = useState('');
  const [noteText, setNoteText] = useState('');
  const [noteSubmitting, setNoteSubmitting] = useState(false);
  const [noteError, setNoteError] = useState('');

  async function load() {
    const payload = await api.ceoDashboard();
    setData(payload);
    setTargetForm(emptyTargetForm);
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
  }

  useEffect(() => {
    load().catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load dashboard'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      await api.setMonthlyTarget({
        target_month: data.current_month + '-01',
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
      await api.submitReport(formData);
      setSuccess(true);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to submit report');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleAddNote(e) {
    e.preventDefault();
    setNoteError('');
    if (!noteText.trim()) {
      setNoteError('Note cannot be empty.');
      return;
    }
    setNoteSubmitting(true);
    try {
      await api.addFinancialNote(noteText.trim());
      setNoteText('');
      await load();
    } catch (err) {
      setNoteError(err instanceof ApiError ? err.message : 'Failed to save note');
    } finally {
      setNoteSubmitting(false);
    }
  }

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  const quadrantMetrics = buildQuadrantMetrics(data.quadrant);

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 pb-24">
        <div className="mb-6 flex items-center justify-between flex-wrap gap-2">
          <p className="text-sm font-semibold text-slate-900">{data.report_date_display}</p>
          {data.submitted_at_today ? (
            <p className="text-xs text-emerald-600">Submitted today at {data.submitted_at_today.split(', ')[1]}</p>
          ) : (
            <p className="text-xs text-slate-500">Financials mandatory</p>
          )}
        </div>
        {data.submission_events_today?.length > 1 && (
          <div className="mb-6 rounded-xl border border-slate-200 bg-white px-4 py-3">
            <p className="text-xs font-semibold text-slate-700 mb-2">Updated {data.submission_events_today.length}&times; today</p>
            <ul className="space-y-1.5">
              {data.submission_events_today.map((ev, i) => (
                <li key={i} className="text-xs text-slate-600 flex flex-wrap gap-x-1.5">
                  <span className="font-medium text-slate-700 flex-shrink-0">{ev.time_display}</span>
                  {ev.changes.length > 0 ? (
                    <span className="text-slate-500">
                      {ev.changes.map((c, j) => (
                        <span key={j}>
                          {j > 0 && ', '}
                          {c.field} {c.old} &rarr; {c.new}
                        </span>
                      ))}
                    </span>
                  ) : (
                    <span className="text-slate-400 italic">initial submission</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {success && (
          <div className="mb-6 rounded-xl bg-emerald-500/10 border border-emerald-500/20 px-5 py-4 text-sm text-emerald-700 flex items-center gap-3">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-emerald-600 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            <span>Your daily report for {data.report_date_display} has been submitted successfully.</span>
          </div>
        )}
        {error && (
          <div className="mb-6 rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-sm text-red-700 flex items-center gap-3">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-red-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>{error}</span>
          </div>
        )}
        {data.already_submitted_today && !success && (
          <div className="mb-6 rounded-xl bg-slate-100 border border-slate-200 px-5 py-4 text-sm text-slate-600 flex items-center gap-3">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-slate-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>
              You already submitted today's report at {data.submitted_at_today?.split(', ')[1]}. Submitting again will update it.
            </span>
          </div>
        )}
        {!data.already_submitted_today && !success && new Date().getHours() >= 15 && (
          <div className="mb-6 rounded-xl bg-amber-50 border border-amber-200 px-5 py-4 text-sm text-amber-800 flex items-center gap-3 flex-wrap sm:flex-nowrap">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            <span className="flex-1">
              <strong>Reminder:</strong> You haven't submitted today's report yet, and it's getting late in the day. Chairman visibility into {data.business_unit?.name} depends on it.
            </span>
            <a href="#daily-report-form" className="shrink-0 text-xs font-bold text-amber-900 bg-amber-200/70 hover:bg-amber-200 px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap">
              Submit Now ↓
            </a>
          </div>
        )}
        {/* MY PROFILE — same CEO Profile card design the Chairman/HO see on
            the Executive Dashboard, so both dashboards read as one design. */}
        <div className="mb-8">
          <CeoProfileCard
            ceoProfile={data.ceo_profile}
            subtitle={data.ceo_profile ? `CEO of ${data.business_unit?.name}` : ''}
            glassClass={GLASS}
          />
        </div>

        {/* MY BUSINESS AT A GLANCE — same 4-quadrant circle + cards the
            Chairman/HO see on the Admin Dashboard for this unit, scoped to
            just this CEO's own BU. */}
        <section className={`mb-8 ${GLASS} p-6`}>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Executive Snapshot</p>
          <h2 className="font-bold text-slate-900 tracking-tight text-lg mb-5">My Business at a Glance</h2>
          <div className="grid lg:grid-cols-12 gap-6 items-start">
            <div className="lg:col-span-3 flex flex-col items-center gap-5 rounded-xl border border-slate-200 bg-white p-6">
              <QuadrantStatusCircle
                financial={data.quadrant.financial_status}
                social={data.quadrant.social_status}
                activity={data.quadrant.activity_status}
                health={data.quadrant.health_status}
                size={240}
                label={data.business_unit?.name}
                showLabels
              />
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px] text-slate-500">
                <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-emerald-500" />Good</span>
                <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-amber-500" />Warning</span>
                <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-red-500" />Critical</span>
                <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-slate-300" />No Data</span>
              </div>
              <div
                className={`w-full text-center rounded-xl px-3 py-2.5 text-xs font-semibold ${
                  data.already_submitted_today ? 'bg-emerald-500/10 text-emerald-600' : 'bg-amber-500/10 text-amber-600'
                }`}
              >
                {data.already_submitted_today ? 'Reported today' : 'No report submitted today'}
              </div>
            </div>

            <div className="lg:col-span-9 grid sm:grid-cols-2 gap-4">
              {BU_QUADRANTS.map((q) => {
                const m = quadrantMetrics[q.key];
                const badgeClass = QUADRANT_BADGE_CLASS[m.status] || QUADRANT_BADGE_CLASS.unknown;
                return (
                  <div key={q.key} className="relative overflow-hidden rounded-xl border border-slate-200 bg-white p-5 pt-4 shadow-[0_1px_3px_rgba(15,23,42,0.04)] hover:shadow-lg hover:border-slate-300 hover:-translate-y-0.5 transition-all duration-200">
                    <span className={`absolute top-0 left-0 right-0 h-1 ${QUADRANT_ACCENT[m.status] || QUADRANT_ACCENT.unknown}`} />
                    <div className="flex items-start justify-between gap-2 mb-3 flex-wrap">
                      <div className="flex items-center gap-2.5 min-w-0">
                        <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${QUADRANT_ICON_CLASS[m.status] || QUADRANT_ICON_CLASS.unknown}`}>
                          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d={QUADRANT_ICON[q.key]} />
                          </svg>
                        </div>
                        <h3 className="text-[15px] font-bold text-slate-900 whitespace-nowrap">{q.label}</h3>
                      </div>
                      <span className={`text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border flex-shrink-0 ${badgeClass}`}>
                        {QUADRANT_STATUS_LABEL[m.status] || QUADRANT_STATUS_LABEL.unknown}
                      </span>
                    </div>
                    {m.displayValue && <p className="font-bold text-slate-800 text-xl tracking-tight mb-3">{m.displayValue}</p>}
                    {m.reason && (
                      <div
                        className={`flex items-start gap-1.5 rounded-lg px-2.5 py-2 mb-3 text-[11.5px] leading-snug ${
                          m.status === 'critical'
                            ? 'bg-red-500/10 text-red-700 font-medium'
                            : m.status === 'warning'
                            ? 'bg-amber-500/10 text-amber-700'
                            : 'bg-slate-100 text-slate-500'
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
                    <ul className="space-y-2 text-[13px] border-t border-slate-100 pt-3">
                      {m.subMetrics.map((sm) => (
                        <li key={sm.label} className="flex items-start justify-between gap-3">
                          <span className="text-slate-500 flex-shrink-0">{sm.label}</span>
                          <span className="font-semibold text-slate-700 flex items-center gap-1.5 min-w-0 text-right break-words justify-end">
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

        {/* MONTHLY TARGET & DAILY PERFORMANCE */}
        <section className={`mb-8 ${GLASS} overflow-hidden`}>
          <div className="px-6 py-4 border-b border-slate-200/70 flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
                </svg>
              </div>
              <div>
                <h2 className="font-bold text-slate-900 tracking-tight">Monthly Target</h2>
                <p className="text-xs text-slate-500">Set once at the start of the month — locked after that; contact Head Office to change it</p>
              </div>
            </div>
          </div>

          <div className="p-6">
            {targetError && (
              <div className="mb-4 rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-2.5 text-xs text-red-700">{targetError}</div>
            )}

            {!data.monthly_target ? (
              <form onSubmit={handleSetTarget} className="space-y-4">
                <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">Monthly Revenue Target</label>
                    <input type="number" min="0" step="0.01" value={targetForm.monthly_revenue_target}
                      onChange={(e) => updateTargetField('monthly_revenue_target', e.target.value)}
                      className={inputClass} placeholder="e.g. 3000000" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">Monthly Cash Flow Target</label>
                    <input type="number" step="0.01" value={targetForm.monthly_cash_flow_target}
                      onChange={(e) => updateTargetField('monthly_cash_flow_target', e.target.value)}
                      className={inputClass} placeholder="e.g. 1500000" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">Max Receivable Allowed</label>
                    <input type="number" min="0" step="0.01" value={targetForm.monthly_max_receivable}
                      onChange={(e) => updateTargetField('monthly_max_receivable', e.target.value)}
                      className={inputClass} placeholder="e.g. 500000" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">Max Payable Allowed</label>
                    <input type="number" min="0" step="0.01" value={targetForm.monthly_max_payable}
                      onChange={(e) => updateTargetField('monthly_max_payable', e.target.value)}
                      className={inputClass} placeholder="e.g. 500000" />
                  </div>
                </div>
                <p className="text-xs text-slate-500 italic">
                  This can only be set once for the month — double-check the numbers before saving.
                </p>
                <div className="flex justify-end gap-2">
                  <button type="submit" disabled={targetSaving} className="text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white px-4 py-2 rounded-lg transition-colors disabled:opacity-50">
                    {targetSaving ? 'Saving…' : 'Save Target'}
                  </button>
                </div>
              </form>
            ) : (
              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
                <div>
                  <p className="text-xs text-slate-500">Revenue Target</p>
                  <p className="font-semibold text-slate-800">{money(data.monthly_target.monthly_revenue_target)}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500">Cash Flow Target</p>
                  <p className="font-semibold text-slate-800">{money(data.monthly_target.monthly_cash_flow_target)}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500">Max Receivable</p>
                  <p className="font-semibold text-slate-800">{money(data.monthly_target.monthly_max_receivable)}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500">Max Payable</p>
                  <p className="font-semibold text-slate-800">{money(data.monthly_target.monthly_max_payable)}</p>
                </div>
                <p className="sm:col-span-2 lg:col-span-4 flex items-center gap-1.5 text-[11px] text-slate-500">
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                  Locked for this month — contact Head Office if it needs to change.
                </p>
              </div>
            )}

            {data.cumulative_performance && (
              <div className="mt-6 pt-6 border-t border-slate-200/70">
                <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Cumulative Performance vs Target &middot; Day {data.cumulative_performance.day_number} of {data.cumulative_performance.days_in_month}
                  </p>
                  <span className={`text-xs font-bold px-3 py-1 rounded-full border ${PERFORMANCE_BADGE_CLASS[data.cumulative_performance.status]}`}>
                    {PERFORMANCE_LABEL[data.cumulative_performance.status]}
                  </span>
                </div>

                <div className="mb-4">
                  <div className="flex items-baseline justify-between mb-1.5">
                    <p className="text-2xl font-bold text-slate-900 tabular-nums">
                      {data.cumulative_performance.achievement_pct != null ? `${data.cumulative_performance.achievement_pct}%` : '—'}
                    </p>
                    <p className="text-xs text-slate-500">of Expected MTD Target ({money(data.cumulative_performance.expected_mtd_target)})</p>
                  </div>
                  <div className="h-2.5 rounded-full bg-slate-100 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${
                        data.cumulative_performance.status === 'GREEN' ? 'bg-emerald-500' : data.cumulative_performance.status === 'YELLOW' ? 'bg-amber-500' : 'bg-red-500'
                      }`}
                      style={{ width: `${Math.max(2, Math.min(100, data.cumulative_performance.achievement_pct ?? 0))}%` }}
                    />
                  </div>
                </div>

                <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 text-sm mb-3">
                  <div className="rounded-lg bg-slate-50 border border-slate-100 px-4 py-3">
                    <p className="text-xs text-slate-500">MTD Revenue</p>
                    <p className="font-bold text-slate-800">{money(data.cumulative_performance.mtd_revenue)}</p>
                  </div>
                  <div className="rounded-lg bg-slate-50 border border-slate-100 px-4 py-3">
                    <p className="text-xs text-slate-500">MTD Other Amount</p>
                    <p className="font-bold text-slate-800">{money(data.cumulative_performance.mtd_other_amount)}</p>
                  </div>
                  <div className="rounded-lg bg-slate-50 border border-slate-100 px-4 py-3">
                    <p className="text-xs text-slate-500">Cumulative Net Revenue</p>
                    <p className="font-bold text-slate-800">{money(data.cumulative_performance.cumulative_net_revenue)}</p>
                  </div>
                  <div className="rounded-lg bg-slate-50 border border-slate-100 px-4 py-3">
                    <p className="text-xs text-slate-500">Variance vs Monthly Target</p>
                    <p className={`font-bold ${data.cumulative_performance.variance_amount > 0 ? 'text-emerald-600' : data.cumulative_performance.variance_amount < 0 ? 'text-red-600' : 'text-slate-700'}`}>
                      {formatVariance(data.cumulative_performance.variance_amount)}
                    </p>
                  </div>
                </div>
                {data.cumulative_performance.summary_notes.length > 0 && (
                  <ul className="space-y-1.5">
                    {data.cumulative_performance.summary_notes.map((flag, i) => (
                      <li key={i} className="text-xs text-slate-500 flex items-start gap-2">
                        <span className="w-1.5 h-1.5 rounded-full bg-slate-300 mt-1 flex-shrink-0" />
                        {flag}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {data.monthly_target && !data.cumulative_performance && (
              <p className="mt-4 text-xs text-slate-500 italic">Submit today's Business Performance below to see your performance against this target.</p>
            )}
          </div>
        </section>

        {/* RECORD NOTES */}
        <section className={`mb-8 ${GLASS} overflow-hidden`}>
          <div className="px-6 py-4 border-b border-slate-200/70">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Qualitative Context</p>
            <h2 className="font-bold text-slate-900 tracking-tight">Record Notes</h2>
            <p className="text-xs text-slate-500 mt-0.5">Add dated notes throughout the month — e.g. a big client payment, an outage that cut trading days</p>
          </div>
          <div className="p-6">
            <form onSubmit={handleAddNote} className="flex items-start gap-3 mb-5">
              <textarea
                rows={2}
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                placeholder="e.g. Received early payment from major client; power outage cut two days of trading..."
                className={`flex-1 ${inputClass}`}
              />
              <button
                type="submit"
                disabled={noteSubmitting}
                className="text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white px-4 py-2.5 rounded-lg transition-colors disabled:opacity-50 flex-shrink-0"
              >
                {noteSubmitting ? 'Saving…' : 'Add Note'}
              </button>
            </form>
            {noteError && <p className="text-xs text-red-600 mb-4">{noteError}</p>}

            {data.financial_notes.length > 0 ? (
              <ul className="space-y-2.5">
                {data.financial_notes.map((n, i) => (
                  <li key={i} className="rounded-xl bg-amber-500/5 border border-amber-500/10 px-4 py-2.5">
                    <p className="text-sm text-slate-700 whitespace-pre-wrap">{n.note_text}</p>
                    <p className="text-[11px] text-slate-400 mt-1">{n.note_date} &middot; logged {n.created_at}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-slate-400 italic">No notes logged yet this month.</p>
            )}
          </div>
        </section>

        <form id="daily-report-form" onSubmit={handleSubmit} className="space-y-8">
          {/* FINANCIAL BLOCK */}
          <section className={`${GLASS} overflow-hidden`}>
            <div className="px-6 py-4 border-b border-slate-200/70 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <h2 className="font-bold text-slate-900 tracking-tight">Business Performance</h2>
              </div>
              <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-red-500/10 border border-red-500/20 text-red-600">Mandatory</span>
            </div>
            <div className="p-6 grid sm:grid-cols-2 lg:grid-cols-5 gap-5">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Revenue</label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={form.revenue}
                    onChange={(e) => updateField('revenue', e.target.value)}
                    className="w-full rounded-lg border border-slate-200 bg-white text-slate-800 pl-7 pr-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                    placeholder="0.00"
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Cash Flow</label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                  <input
                    type="number"
                    step="0.01"
                    value={form.cash_flow}
                    onChange={(e) => updateField('cash_flow', e.target.value)}
                    className="w-full rounded-lg border border-slate-200 bg-white text-slate-800 pl-7 pr-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                    placeholder="0.00"
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Receivable</label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={form.receivable}
                    onChange={(e) => updateField('receivable', e.target.value)}
                    className="w-full rounded-lg border border-slate-200 bg-white text-slate-800 pl-7 pr-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                    placeholder="0.00"
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Payable</label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={form.payable}
                    onChange={(e) => updateField('payable', e.target.value)}
                    className="w-full rounded-lg border border-slate-200 bg-white text-slate-800 pl-7 pr-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                    placeholder="0.00"
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Other Amount <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
                  <input
                    type="number"
                    step="0.01"
                    value={form.other_amount}
                    onChange={(e) => updateField('other_amount', e.target.value)}
                    className="w-full rounded-lg border border-slate-200 bg-white text-slate-800 pl-7 pr-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                    placeholder="0.00"
                  />
                </div>
              </div>
            </div>
            <div className="px-6 pb-6">
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Other <span className="text-slate-400 font-normal">(optional — anything else worth noting today)</span>
              </label>
              <textarea
                rows={2}
                placeholder="Anything else about today's financials..."
                value={form.financial_other_notes}
                onChange={(e) => updateField('financial_other_notes', e.target.value)}
                className={inputClass}
              />
            </div>
          </section>

          {/* CEO ACTIVITY BLOCK */}
          <section className={`${GLASS} overflow-hidden`}>
            <div className="px-6 py-4 border-b border-slate-200/70 flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <h2 className="font-bold text-slate-900 tracking-tight">Daily Activities</h2>
            </div>
            <div className="p-6 grid sm:grid-cols-2 gap-5">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Meetings Attended</label>
                <textarea
                  rows={4}
                  placeholder="Describe key meetings attended today..."
                  value={form.meetings_attended}
                  onChange={(e) => updateField('meetings_attended', e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Site Visits / Travel</label>
                <textarea
                  rows={4}
                  placeholder="Describe site visits or travel undertaken..."
                  value={form.site_visits}
                  onChange={(e) => updateField('site_visits', e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Key Decisions Made</label>
                <textarea
                  rows={4}
                  placeholder="Summarize key decisions made today..."
                  value={form.key_decisions}
                  onChange={(e) => updateField('key_decisions', e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Next Day Personal Schedule</label>
                <textarea
                  rows={4}
                  placeholder="Outline tomorrow's personal schedule..."
                  value={form.next_day_schedule}
                  onChange={(e) => updateField('next_day_schedule', e.target.value)}
                  className={inputClass}
                />
              </div>
            </div>
            <div className="px-6 pb-6 grid sm:grid-cols-3 gap-5">
              <PercentProgress label="Gym %" value={form.gym_percent} onChange={(v) => updateField('gym_percent', v)} color="brand" />
              <PercentProgress label="Swimming %" value={form.swimming_percent} onChange={(v) => updateField('swimming_percent', v)} color="emerald" />
              <PercentProgress label="Cycle %" value={form.cycle_percent} onChange={(v) => updateField('cycle_percent', v)} color="amber" />
            </div>
            <div className="px-6 pb-6">
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Other <span className="text-slate-400 font-normal">(optional — anything else worth noting today)</span>
              </label>
              <textarea
                rows={2}
                placeholder="Anything else about today's activity..."
                value={form.activity_other_notes}
                onChange={(e) => updateField('activity_other_notes', e.target.value)}
                className={inputClass}
              />
            </div>
            <div className="px-6 pb-6">
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Supporting Document <span className="text-slate-400 font-normal">(optional — minutes, photos, receipts)</span>
              </label>
              <input
                type="file"
                onChange={handleAttachmentChange}
                className="block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-brand-500/10 file:text-brand-600 hover:file:bg-brand-500/20"
              />
              {safeUrl(data.activity?.document_url) && (
                <p className="mt-2 text-xs text-slate-500">
                  Current file:{' '}
                  <a href={safeUrl(data.activity.document_url)} target="_blank" rel="noreferrer" className="text-brand-600 hover:text-brand-700 underline">
                    View attachment
                  </a>
                </p>
              )}
            </div>
          </section>

          {/* CEO PERSONAL HEALTH BLOCK */}
          <section className={`${GLASS} overflow-hidden`}>
            <div className="px-6 py-4 border-b border-slate-200/70 flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
                </svg>
              </div>
              <h2 className="font-bold text-slate-900 tracking-tight">Health and Fitness</h2>
            </div>
            <div className="p-6 grid sm:grid-cols-2 lg:grid-cols-4 gap-8">
              <StepsCounter value={form.daily_steps} onChange={(v) => updateField('daily_steps', v)} />
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Heart Rate <span className="text-slate-400 font-normal">(bpm, optional)</span>
                </label>
                <div className="relative">
                  <input
                    type="number"
                    min="0"
                    value={form.heart_rate}
                    onChange={(e) => updateField('heart_rate', e.target.value)}
                    placeholder="e.g. 72"
                    className="w-full rounded-lg border border-slate-200 bg-white text-slate-800 pl-3 pr-14 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs font-medium">bpm</span>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Sleep Time <span className="text-slate-400 font-normal">(hours, optional)</span>
                </label>
                <div className="relative">
                  <input
                    type="number"
                    min="0"
                    max="24"
                    step="0.5"
                    value={form.sleep_hours}
                    onChange={(e) => updateField('sleep_hours', e.target.value)}
                    placeholder="e.g. 7.5"
                    className="w-full rounded-lg border border-slate-200 bg-white text-slate-800 pl-3 pr-12 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs font-medium">hrs</span>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Blood Pressure <span className="text-slate-400 font-normal">(mmHg, optional)</span>
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="0"
                    value={form.bp_systolic}
                    onChange={(e) => updateField('bp_systolic', e.target.value)}
                    placeholder="120"
                    className="w-full rounded-lg border border-slate-200 bg-white text-slate-800 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                  />
                  <span className="text-slate-400 text-sm font-medium flex-shrink-0">/</span>
                  <input
                    type="number"
                    min="0"
                    value={form.bp_diastolic}
                    onChange={(e) => updateField('bp_diastolic', e.target.value)}
                    placeholder="80"
                    className="w-full rounded-lg border border-slate-200 bg-white text-slate-800 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                  />
                </div>
              </div>
            </div>
            <div className="px-6 pb-6">
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Other <span className="text-slate-400 font-normal">(optional — anything else worth noting today)</span>
              </label>
              <textarea
                rows={2}
                placeholder="Anything else about today's health/well-being..."
                value={form.health_other_notes}
                onChange={(e) => updateField('health_other_notes', e.target.value)}
                className={inputClass}
              />
            </div>
          </section>

          {/* CEO SOCIAL BLOCK */}
          <section className={`${GLASS} overflow-hidden`}>
            <div className="px-6 py-4 border-b border-slate-200/70 flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-2.13a4 4 0 10-4-4 4 4 0 004 4zm6 0a4 4 0 10-4-4" />
                </svg>
              </div>
              <h2 className="font-bold text-slate-900 tracking-tight">Social Activity</h2>
            </div>
            <div className="p-6 grid sm:grid-cols-3 gap-5">
              <YesNoToggle label="Event Attended" value={form.event_flag} onChange={(v) => updateField('event_flag', v)} />
              <YesNoToggle label="Meeting Attended" value={form.meeting_flag} onChange={(v) => updateField('meeting_flag', v)} />
              <YesNoToggle label="Golf" value={form.golf_flag} onChange={(v) => updateField('golf_flag', v)} />
            </div>
            <div className="px-6 pb-6">
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Other <span className="text-slate-400 font-normal">(optional — anything else worth noting today)</span>
              </label>
              <textarea
                rows={2}
                placeholder="Anything else about today's social engagement..."
                value={form.social_other_notes}
                onChange={(e) => updateField('social_other_notes', e.target.value)}
                className={inputClass}
              />
            </div>
          </section>

          <div className="flex items-center justify-end gap-3">
            <p className="text-xs text-slate-500 mr-auto">
              All entries are recorded against {data.report_date_display} for {data.business_unit?.name}.
            </p>
            <button
              type="submit"
              disabled={submitting}
              className="rounded-xl bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-500 hover:to-indigo-500 text-white font-semibold px-8 py-3 text-sm shadow-lg shadow-brand-600/30 transition-all disabled:opacity-50"
            >
              {submitting ? 'Submitting…' : data.already_submitted_today ? 'Update Daily Report' : 'Submit Daily Report'}
            </button>
          </div>
        </form>
        <Footer light />
      </main>
    </div>
  );
}
