import { useSearchParams, Link } from 'react-router-dom';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import CeoProfileCard from '../components/CeoProfileCard';
import LastUpdated from '../components/LastUpdated';
import { validateAttachment } from '../utils/attachmentValidation';
import { localHour } from '../utils/format';
import { buildQuadrantMetrics } from '../utils/quadrantMetrics';
import { GLASS, PAGE_BG } from '../utils/glassTheme';
import { viewUploadUrl } from '../utils/safeUrl';
import CeoOpsSection from '../components/CeoOpsSection';
import Footer from '../components/Footer';
import useDailyReport from '../hooks/useDailyReport';
import QuadrantSnapshot from '../components/QuadrantSnapshot';
import MonthlyTargetSection from '../components/MonthlyTargetSection';
import { FinancialSection, ActivitySection, HealthSection, SocialSection } from '../components/DailyReportFormSections';
import { inputClass } from '../components/FormFields';

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
  meetings_count: '',
  site_visits: '',
  key_decisions: '',
  key_achievement: '',
  next_day_schedule: '',
  problems_today: '',
  help_needed: '',
  gym_percent: '',
  swimming_percent: '',
  cycle_percent: '',
  activity_other_notes: '',
  daily_steps: '',
  heart_rate: '',
  sleep_hours: '',
  bp_systolic: '',
  bp_diastolic: '',
  stress_level: '',
  energy_mood: '',
  weight_kg: '',
  is_sick: false,
  health_other_notes: '',
  event_flag: false,
  meeting_flag: false,
  golf_flag: false,
  social_other_notes: '',
};

// Form key → FormData name, in submit order. Serialised by iterating this
// map (useDailyReport) instead of 34 hand-written append() calls. The
// health_* names tell the API to store the exercise % on the health row.
const FIELD_MAP = {
  revenue: 'revenue',
  cash_flow: 'cash_flow',
  receivable: 'receivable',
  payable: 'payable',
  other_amount: 'other_amount',
  financial_other_notes: 'financial_other_notes',
  meetings_attended: 'meetings_attended',
  meetings_count: 'meetings_count',
  site_visits: 'site_visits',
  key_decisions: 'key_decisions',
  key_achievement: 'key_achievement',
  next_day_schedule: 'next_day_schedule',
  problems_today: 'problems_today',
  help_needed: 'help_needed',
  activity_other_notes: 'activity_other_notes',
  daily_steps: 'daily_steps',
  heart_rate: 'heart_rate',
  sleep_hours: 'sleep_hours',
  bp_systolic: 'bp_systolic',
  bp_diastolic: 'bp_diastolic',
  stress_level: 'stress_level',
  energy_mood: 'energy_mood',
  weight_kg: 'weight_kg',
  is_sick: 'is_sick',
  gym_percent: 'health_gym_percent',
  swimming_percent: 'health_swimming_percent',
  cycle_percent: 'health_cycle_percent',
  health_other_notes: 'health_other_notes',
  event_flag: 'event_flag',
  meeting_flag: 'meeting_flag',
  golf_flag: 'golf_flag',
  social_other_notes: 'social_other_notes',
};

function mapForm(payload) {
  return {
    revenue: payload.form_financial?.revenue ?? '',
    cash_flow: payload.form_financial?.cash_flow ?? '',
    receivable: payload.form_financial?.receivable ?? '',
    payable: payload.form_financial?.payable ?? '',
    other_amount: payload.form_financial?.other_amount ?? '',
    financial_other_notes: payload.form_financial?.other_notes ?? '',
    meetings_attended: payload.form_activity?.meetings_attended ?? '',
    meetings_count: payload.form_activity?.meetings_count ?? '',
    site_visits: payload.form_activity?.site_visits ?? '',
    key_decisions: payload.form_activity?.key_decisions ?? '',
    key_achievement: payload.form_activity?.key_achievement ?? '',
    next_day_schedule: payload.form_activity?.next_day_schedule ?? '',
    problems_today: payload.form_activity?.problems_today ?? '',
    help_needed: payload.form_activity?.help_needed ?? '',
    // Exercise % is collected in the Health section now and stored on
    // the health row; fall back to the activity row's pre-move values
    // so days filed before the move still show their percents.
    gym_percent: payload.form_health?.gym_percent ?? payload.form_activity?.gym_percent ?? '',
    swimming_percent: payload.form_health?.swimming_percent ?? payload.form_activity?.swimming_percent ?? '',
    cycle_percent: payload.form_health?.cycle_percent ?? payload.form_activity?.cycle_percent ?? '',
    activity_other_notes: payload.form_activity?.other_notes ?? '',
    daily_steps: payload.form_health?.daily_steps ?? '',
    heart_rate: payload.form_health?.heart_rate ?? '',
    sleep_hours: payload.form_health?.sleep_hours ?? '',
    bp_systolic: payload.form_health?.bp_systolic ?? '',
    bp_diastolic: payload.form_health?.bp_diastolic ?? '',
    stress_level: payload.form_health?.stress_level ?? '',
    energy_mood: payload.form_health?.energy_mood ?? '',
    weight_kg: payload.form_health?.weight_kg ?? '',
    is_sick: payload.form_health?.is_sick ?? false,
    health_other_notes: payload.form_health?.other_notes ?? '',
    event_flag: payload.form_social?.event_flag ?? false,
    meeting_flag: payload.form_social?.meeting_flag ?? false,
    golf_flag: payload.form_social?.golf_flag ?? false,
    social_other_notes: payload.form_social?.other_notes ?? '',
  };
}

export default function CeoDashboardPage() {
  const [searchParams] = useSearchParams();
  // Set when the page is opened from the History calendar for one specific
  // day (/ceo?date=YYYY-MM-DD): the report form then edits that date's
  // rows instead of today's. Absent = the long-standing today behavior.
  const editDate = searchParams.get('date') || '';

  const {
    data, form, error, refreshError, success, submitting,
    updatedAt, refreshing, targetForm, targetSaving, targetError,
    noteText, setNoteText, noteSubmitting, noteError,
    load, updateField, updateTargetField, handleAttachmentChange,
    handleSetTarget, handleSubmit, handleAddNote,
  } = useDailyReport({
    emptyForm,
    emptyTargetForm,
    draftKey: 'ceo-daily-report',
    fetchDashboard: () => api.ceoDashboard('', editDate),
    mapForm,
    draftDate: (payload) => payload.form_date,
    isFormEmpty: (f) => f.revenue === '' && f.cash_flow === '',
    fieldMap: FIELD_MAP,
    submitReport: (fd) => api.submitReport(fd),
    setTargetApi: (payload) => api.setMonthlyTarget(payload),
    targetMonth: (d) => `${d.current_month}-01`,
    extraSubmitFields: (fd, d) => {
      // Editing a specific day (opened from the History calendar): file
      // the report against that date. Today-flow sends nothing, as before.
      if (d && !d.form_is_today) fd.append('report_date', d.form_date);
    },
    addNoteApi: (text) => api.addFinancialNote(text),
  });

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
          <p className="text-base font-bold tracking-tight text-slate-900 dark:text-slate-100">{data.report_date_display}</p>
          <div className="flex items-center gap-3 flex-wrap">
            {/* SM-5: visible freshness + manual refresh for this dashboard. */}
            <LastUpdated updatedAt={updatedAt} onRefresh={load} refreshing={refreshing} />
            {data.form_submitted_at ? (
              <p className="text-xs font-semibold text-emerald-700 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 rounded-full">
                {data.form_is_today
                  ? `Submitted today at ${data.form_submitted_at.split(', ')[1]}`
                  : `Report saved for ${data.form_date_display}`}
              </p>
            ) : (
              <p className="text-xs font-medium text-slate-500 dark:text-slate-400 bg-slate-500/10 px-2.5 py-1 rounded-full">Financials mandatory</p>
            )}
          </div>
        </div>
        {!data.form_is_today && (
          <div className="mb-6 rounded-xl bg-brand-500/10 border border-brand-500/20 px-5 py-4 text-sm text-slate-700 flex items-center gap-3 flex-wrap sm:flex-nowrap">
            <span className="flex-1">
              You’re filling the report for <strong>{data.form_date_display}</strong>, opened from your History calendar.
              Saving updates that day — not today.
            </span>
            <Link
              to="/ceo"
              className="shrink-0 text-xs font-bold text-brand-700 bg-brand-500/15 hover:bg-brand-500/25 px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap"
            >
              Back to today
            </Link>
          </div>
        )}
        {data.form_is_today && data.submission_events_today?.length > 1 && (
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
            <span>Your daily report for {data.form_date_display} has been submitted successfully.</span>
          </div>
        )}
        {refreshError && (
          <div className="mb-6 rounded-xl bg-amber-500/10 border border-amber-500/20 px-5 py-4 text-sm text-amber-700 flex items-center gap-3">
            <span>{refreshError}</span>
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
        {data.form_already_submitted && !success && (
          <div className="mb-6 rounded-xl bg-slate-100 border border-slate-200 px-5 py-4 text-sm text-slate-600 flex items-center gap-3">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-slate-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>
              {data.form_is_today ? (
                <>You already submitted today&apos;s report at {data.form_submitted_at?.split(', ')[1]}. Submitting again will update it.</>
              ) : (
                <>You already saved a report for {data.form_date_display}. Submitting again will update it.</>
              )}
            </span>
          </div>
        )}
        {!data.form_already_submitted && !success && data.form_is_today && localHour() >= 15 && (
          <div className="mb-6 rounded-xl bg-amber-50 border border-amber-200 px-5 py-4 text-sm text-amber-800 flex items-center gap-3 flex-wrap sm:flex-nowrap">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            <span className="flex-1">
              <strong>Reminder:</strong> You haven&apos;t submitted today&apos;s report yet, and it&apos;s getting late in the day. Chairman visibility into {data.business_unit?.name} depends on it.
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

        <QuadrantSnapshot
          quadrant={data.quadrant}
          quadrantMetrics={quadrantMetrics}
          businessUnitName={data.business_unit?.name}
          alreadySubmitted={data.already_submitted_today}
          financialTrend={data.financial_trend}
        />

        <MonthlyTargetSection
          title="Monthly Target"
          subtitle="Set once at the start of the month — locked after that; contact Head Office to change it"
          monthlyTarget={data.monthly_target}
          perf={data.cumulative_performance}
          targetForm={targetForm}
          updateTargetField={updateTargetField}
          targetSaving={targetSaving}
          targetError={targetError}
          onSubmit={handleSetTarget}
          showForm={!data.monthly_target}
          formNote="This can only be set once for the month — double-check the numbers before saving."
          lockNote="Locked for this month — contact Head Office if it needs to change."
        />

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
                id="financial-note"
                aria-label="Add a record note"
                rows={2}
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                placeholder="e.g. Received early payment from major client; power outage cut two days of trading..."
                className={`flex-1 ${inputClass}`}
              />
              <button
                type="submit"
                disabled={noteSubmitting}
                className="text-xs font-semibold bg-brand-600 hover:bg-brand-700 text-white px-4 py-2.5 rounded-lg transition-colors disabled:opacity-50 flex-shrink-0"
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
            showMeetingsCount
            showKeyAchievement
            showProblems
            showHelp
            attachmentUrl={viewUploadUrl(data.activity?.document_url)}
            onAttachmentChange={(e) => handleAttachmentChange(e, validateAttachment)}
          />
          <HealthSection form={form} updateField={updateField} showChips showWeight showSick showPercents />
          <SocialSection form={form} updateField={updateField} />

          <div className="flex items-center justify-end gap-3">
            <p className="text-xs text-slate-500 mr-auto">
              All entries are recorded against {data.form_date_display} for {data.business_unit?.name}.
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
        <CeoOpsSection />
        <Footer light />
      </main>
    </div>
  );
}
