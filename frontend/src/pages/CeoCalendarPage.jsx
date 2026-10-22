import { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import ActivityMonthCalendar from '../components/ActivityMonthCalendar';
import Footer from '../components/Footer';
import { money } from '../utils/quadrantMetrics';
import { GLASS, PAGE_BG } from '../utils/glassTheme';
import { safeUrl } from '../utils/safeUrl';

function DetailField({ label, value }) {
  return (
    <div>
      <p className="text-slate-500 mb-0.5">{label}</p>
      <p className="text-slate-700 whitespace-pre-wrap">{value || '—'}</p>
    </div>
  );
}

function DetailRow({ label, value }) {
  return (
    <div className="flex justify-between">
      <span className="text-slate-500">{label}</span>
      <span className="font-semibold text-slate-700 tabular-nums">{value}</span>
    </div>
  );
}

/** Read-only day detail for the executive calendar: everything the CEO
 * submitted that day, in the same four areas as their own dashboard. */
function DayDetail({ day, ceoName }) {
  if (!day) {
    return <p className="text-center text-sm text-slate-400 py-10">Select a day on the calendar to see its full activity.</p>;
  }

  const hasFinancial = day.revenue != null;
  const hasHealth = day.daily_steps != null || day.heart_rate != null || day.sleep_hours != null || day.bp_systolic != null
    || day.stress_level != null || day.energy_mood != null || day.weight_kg != null || day.is_sick;
  const hasSocial = day.event_flag != null;
  const hasAnything = day.has_report || hasFinancial || hasHealth || hasSocial;

  return (
    <div className="p-6">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-4">
        <p className="font-bold text-slate-900 tracking-tight">{day.date_display}</p>
        {day.has_report && (
          <p className="text-xs text-slate-500">
            {day.activity_submitted_by_username
              ? `Submitted by ${day.activity_submitted_by_username}`
              : 'Activity submitted'}
          </p>
        )}
      </div>

      {!hasAnything ? (
        <p className="text-sm text-slate-400 italic">No activity submitted for this day.</p>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Daily Activities</p>
            {day.has_report ? (
              <div className="space-y-2 text-xs">
                <DetailField label="Meetings" value={day.meetings_attended} />
                {day.meetings_count != null && (
                  <div className="flex justify-between">
                    <span className="text-slate-500">Meetings (count)</span>
                    <span className="font-semibold text-slate-700 tabular-nums">{day.meetings_count}</span>
                  </div>
                )}
                <DetailField label="Site Visits" value={day.site_visits} />
                <DetailField label="Key Decisions" value={day.key_decisions} />
                {day.key_achievement && (
                  <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/25 px-2.5 py-2">
                    <p className="text-emerald-700 font-semibold uppercase tracking-wide text-[10px] mb-0.5">Key Achievement</p>
                    <p className="text-slate-700 whitespace-pre-wrap">{day.key_achievement}</p>
                  </div>
                )}
                <DetailField label="Next Day Schedule" value={day.next_day_schedule} />
                {day.problems_today && (
                  <div className="rounded-lg bg-amber-500/10 border border-amber-500/25 px-2.5 py-2">
                    <p className="text-amber-700 font-semibold uppercase tracking-wide text-[10px] mb-0.5">Problems Today</p>
                    <p className="text-slate-700 whitespace-pre-wrap">{day.problems_today}</p>
                  </div>
                )}
                {day.help_needed && !day.help_resolved_at && (
                  <div className="rounded-lg bg-red-500/10 border border-red-500/30 px-2.5 py-2">
                    <p className="text-red-700 font-bold uppercase tracking-wide text-[10px] mb-0.5">Help Needed — HO / Chairman</p>
                    <p className="text-slate-700 whitespace-pre-wrap">{day.help_needed}</p>
                  </div>
                )}
                {day.help_needed && day.help_resolved_at && (
                  <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/30 px-2.5 py-2">
                    <p className="text-emerald-700 font-bold uppercase tracking-wide text-[10px] mb-0.5">Help Needed — HO / Chairman</p>
                    <p className="text-slate-700 whitespace-pre-wrap">{day.help_needed}</p>
                    <div className="mt-1.5 pt-1.5 border-t border-emerald-500/20">
                      <p className="text-emerald-700 font-semibold">
                        Resolved ✓ — {day.help_resolved_at}
                        {day.help_resolved_by_username ? ` by ${day.help_resolved_by_username}` : ''}
                      </p>
                      {day.help_response && <p className="text-slate-700 whitespace-pre-wrap mt-0.5">{day.help_response}</p>}
                    </div>
                  </div>
                )}
                {day.activity_other_notes && <DetailField label="Other Notes" value={day.activity_other_notes} />}
                <div className="pt-1.5 border-t border-slate-100 flex justify-between">
                  <span className="text-slate-500">Gym / Swim / Cycle</span>
                  <span className="font-semibold text-slate-700">
                    {(day.exercise_gym_percent ?? day.gym_percent) != null ? `${day.exercise_gym_percent ?? day.gym_percent}%` : '—'} /{' '}
                    {(day.exercise_swimming_percent ?? day.swimming_percent) != null ? `${day.exercise_swimming_percent ?? day.swimming_percent}%` : '—'} /{' '}
                    {(day.exercise_cycle_percent ?? day.cycle_percent) != null ? `${day.exercise_cycle_percent ?? day.cycle_percent}%` : '—'}
                  </span>
                </div>
                {safeUrl(day.document_url) && (
                  <a
                    href={safeUrl(day.document_url)}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-600 hover:text-brand-700 pt-1"
                  >
                    View attachment
                  </a>
                )}
              </div>
            ) : (
              <p className="text-xs text-slate-400 italic">No activity submitted{ceoName ? ` by ${ceoName}` : ''} this day</p>
            )}
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Business Performance</p>
            {hasFinancial ? (
              <div className="space-y-1.5 text-xs">
                <DetailRow label="Revenue" value={money(day.revenue)} />
                <DetailRow label="Cash Flow" value={day.cash_flow != null ? money(day.cash_flow) : '—'} />
                <DetailRow label="Receivable" value={day.receivable != null ? money(day.receivable) : '—'} />
                <DetailRow label="Payable" value={day.payable != null ? money(day.payable) : '—'} />
              </div>
            ) : (
              <p className="text-xs text-slate-400 italic">No financial data</p>
            )}
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Health and Fitness</p>
            {hasHealth ? (
              <div className="space-y-1.5 text-xs">
                <DetailRow label="Steps" value={day.daily_steps != null ? day.daily_steps.toLocaleString() : '—'} />
                <DetailRow label="Heart Rate" value={day.heart_rate != null ? `${day.heart_rate} bpm` : '—'} />
                <DetailRow label="Sleep Time" value={day.sleep_hours != null ? `${day.sleep_hours} hrs` : '—'} />
                <DetailRow
                  label="Blood Pressure"
                  value={day.bp_systolic != null && day.bp_diastolic != null ? `${day.bp_systolic}/${day.bp_diastolic}` : '—'}
                />
                <DetailRow label="Stress Level" value={day.stress_level || '—'} />
                <DetailRow label="Energy / Mood" value={day.energy_mood || '—'} />
                <DetailRow label="Weight" value={day.weight_kg != null ? `${day.weight_kg} kg` : '—'} />
                {day.is_sick && (
                  <p className="text-rose-600 font-semibold">Feeling sick this day</p>
                )}
              </div>
            ) : (
              <p className="text-xs text-slate-400 italic">No health data</p>
            )}
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Social Activity</p>
            {hasSocial ? (
              <div className="space-y-1.5 text-xs">
                {[
                  ['Event', day.event_flag],
                  ['Meeting', day.meeting_flag],
                  ['Golf', day.golf_flag],
                ].map(([label, flag]) => (
                  <div key={label} className="flex justify-between items-center">
                    <span className="text-slate-500">{label}</span>
                    <span
                      className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${
                        flag
                          ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-600'
                          : 'bg-slate-100 text-slate-500'
                      }`}
                    >
                      {flag ? 'Yes' : 'No'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-slate-400 italic">No social data</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Chairman/HO view of one unit's CEO activity month — pick a business
 * unit, browse the month Google-Calendar style, click any day for the
 * full submission. Read-only: executives never write a CEO's report. */
export default function CeoCalendarPage() {
  const [bus, setBus] = useState([]);
  const [buId, setBuId] = useState(null);
  const [month, setMonth] = useState('');
  const [data, setData] = useState(null);
  const [selectedDay, setSelectedDay] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api
      .adminBusinessUnits()
      .then((payload) => {
        const items = payload.items || [];
        setBus(items);
        if (items.length > 0) setBuId(items[0].id);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load business units'));
  }, []);

  useEffect(() => {
    if (buId == null) return;
    api
      .ceoActivityCalendar(buId, month)
      .then((payload) => {
        setData(payload);
        setSelectedDay(null);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load CEO calendar'));
  }, [buId, month]);

  const ceoName = data?.ceo ? data.ceo.full_name || data.ceo.username : null;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 pb-24">
        {error && (
          <div className="mb-6 rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-sm text-red-700">{error}</div>
        )}

        <div className="mb-6 flex items-end justify-between flex-wrap gap-3">
          <div>
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">CEO Activity Calendar</p>
            <h1 className="font-bold text-slate-900 tracking-tight text-lg">
              {data ? data.business_unit.name : 'CEO Calendar'}
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              {data
                ? data.ceo
                  ? `CEO: ${ceoName} (${data.ceo.username})`
                  : 'No active CEO assigned to this unit'
                : 'Loading…'}
            </p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            {data && (
              <a
                href={api.ceoMonthlySummaryUrl(buId, month || data.current_month)}
                className="text-xs font-semibold bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 px-3.5 py-2 rounded-lg hover:opacity-90 transition-opacity"
              >
                Monthly Summary PDF
              </a>
            )}
            <label className="flex items-center gap-2 text-xs font-medium text-slate-500">
              Business Unit
              <select
                value={buId ?? ''}
                onChange={(e) => {
                  setBuId(Number(e.target.value));
                  setMonth('');
                }}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-800 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              >
                {bus.map((bu) => (
                  <option key={bu.id} value={bu.id}>
                    {bu.code} — {bu.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        {!data ? (
          <div className="flex items-center justify-center py-24">
            <div className="w-8 h-8 border-2 border-slate-200 border-t-brand-600 rounded-full animate-spin" />
          </div>
        ) : (
          <>
            <ActivityMonthCalendar
              calendarWeeks={data.calendar_weeks}
              monthLabel={data.calendar_month_label}
              onPrevMonth={() => setMonth(data.prev_month)}
              onNextMonth={() => setMonth(data.next_month)}
              onToday={() => setMonth(data.current_month)}
              onDayClick={(day) => setSelectedDay(day)}
              selectedDate={selectedDay?.date_iso}
            />

            <section className={`${GLASS} overflow-hidden mt-6`}>
              <div className="px-6 py-4 border-b border-slate-200/70">
                <h2 className="font-bold text-slate-900 tracking-tight">Day Detail</h2>
              </div>
              <DayDetail day={selectedDay} ceoName={ceoName} />
            </section>
          </>
        )}
        <Footer light />
      </main>
    </div>
  );
}
