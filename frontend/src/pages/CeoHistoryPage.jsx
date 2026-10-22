import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import ActivityMonthCalendar from '../components/ActivityMonthCalendar';
import Footer from '../components/Footer';
import { formatVariance, money } from '../utils/quadrantMetrics';
import { GLASS, PAGE_BG } from '../utils/glassTheme';
import { safeUrl } from '../utils/safeUrl';

const STATUS_BADGE_CLASS = {
  red: 'bg-red-500/10 border-red-500/20',
  yellow: 'bg-amber-500/10 border-amber-500/20',
  green: 'bg-emerald-500/10 border-emerald-500/20',
  grey: 'bg-slate-100 border-slate-200',
};
const STATUS_TEXT_CLASS = { red: 'text-red-600', yellow: 'text-amber-600', green: 'text-emerald-600', grey: 'text-slate-500' };
const STATUS_LABEL = { red: 'Critical', yellow: 'Warning', green: 'Good', grey: 'No Data' };

/** Split out of the old all-in-one CeoDashboardPage — a dedicated page for
 * browsing this CEO's own past submissions, mirroring the admin section's
 * separate Business Unit Detail page instead of cramming everything into
 * one long dashboard. */
export default function CeoHistoryPage() {
  const [data, setData] = useState(null);
  const [month, setMonth] = useState('');
  const [reviewIndex, setReviewIndex] = useState(0);
  const [selectedDay, setSelectedDay] = useState(null);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    api
      .ceoDashboard(month)
      .then((payload) => {
        setData(payload);
        setReviewIndex(0);
        setSelectedDay(null);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load history'));
  }, [month]);

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  const reviewDates = data.activity_rows;
  const currentReview = reviewDates[reviewIndex] || null;
  const financialForDay = currentReview ? data.financial_rows.find((f) => f.report_date === currentReview.report_date) : null;
  const healthForDay = currentReview ? data.health_rows.find((h) => h.report_date === currentReview.report_date) : null;
  const socialForDay = currentReview ? data.social_rows.find((s) => s.report_date === currentReview.report_date) : null;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 pb-24">
        {error && (
          <div className="mb-6 rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-sm text-red-700">{error}</div>
        )}

        <div className="mb-6 flex items-center justify-between flex-wrap gap-3">
          <div>
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">My History</p>
            <h1 className="font-bold text-slate-900 tracking-tight text-lg">{data.calendar_month_label}</h1>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setMonth(data.prev_month)}
              className="text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-2 rounded-lg transition-colors"
            >
              &larr; Previous Month
            </button>
            <button
              type="button"
              onClick={() => setMonth(data.next_month)}
              disabled={data.next_month > data.current_month}
              className="text-xs font-semibold bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:cursor-not-allowed text-slate-700 px-3 py-2 rounded-lg transition-colors"
            >
              Next Month &rarr;
            </button>
          </div>
        </div>

        <section className={`${GLASS} overflow-hidden mb-10`}>
          <div className="p-6 border-b border-slate-200/70">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Month Calendar</p>
            <h2 className="font-bold text-slate-900 tracking-tight text-lg">Fill &amp; Review by Day</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Your whole month at a glance — pick any day to review it, or fill a day you missed.
            </p>
          </div>
          <div className="p-4 sm:p-6">
            <ActivityMonthCalendar
              calendarWeeks={data.calendar_weeks}
              monthLabel={data.calendar_month_label}
              onPrevMonth={() => setMonth(data.prev_month)}
              onNextMonth={() => setMonth(data.next_month)}
              onToday={() => setMonth(data.current_month)}
              onDayClick={(day) => setSelectedDay(day)}
              selectedDate={selectedDay?.date_iso}
            />
          </div>
          {selectedDay && (
            <div className="px-6 py-5 border-t border-slate-200/70">
              <div className="flex items-start justify-between flex-wrap gap-3">
                <div className="min-w-0">
                  <p className="font-semibold text-slate-900">{selectedDay.date_display}</p>
                  {selectedDay.has_report || selectedDay.revenue != null ? (
                    <div className="mt-2 space-y-1.5 text-xs text-slate-600 max-w-2xl">
                      {selectedDay.meetings_attended && (
                        <p><span className="text-slate-400 font-medium">Meetings: </span><span className="whitespace-pre-wrap">{selectedDay.meetings_attended}</span>{selectedDay.meetings_count != null && <span className="text-slate-500"> ({selectedDay.meetings_count})</span>}</p>
                      )}
                      {selectedDay.site_visits && (
                        <p><span className="text-slate-400 font-medium">Site Visits: </span><span className="whitespace-pre-wrap">{selectedDay.site_visits}</span></p>
                      )}
                      {selectedDay.key_decisions && (
                        <p><span className="text-slate-400 font-medium">Key Decisions: </span><span className="whitespace-pre-wrap">{selectedDay.key_decisions}</span></p>
                      )}
                      {selectedDay.key_achievement && (
                        <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/25 px-3 py-2">
                          <p className="text-emerald-600 font-semibold">Key Achievement</p>
                          <p className="whitespace-pre-wrap text-slate-700">{selectedDay.key_achievement}</p>
                        </div>
                      )}
                      {selectedDay.next_day_schedule && (
                        <p><span className="text-slate-400 font-medium">Next Day Schedule: </span><span className="whitespace-pre-wrap">{selectedDay.next_day_schedule}</span></p>
                      )}
                      {selectedDay.problems_today && (
                        <p><span className="text-amber-600 font-medium">Problems: </span><span className="whitespace-pre-wrap">{selectedDay.problems_today}</span></p>
                      )}
                      {selectedDay.help_needed && !selectedDay.help_resolved_at && (
                        <div className="rounded-lg bg-red-500/10 border border-red-500/25 px-3 py-2">
                          <p className="text-red-600 font-semibold">Help Needed from HO / Chairman</p>
                          <p className="whitespace-pre-wrap text-slate-700">{selectedDay.help_needed}</p>
                        </div>
                      )}
                      {selectedDay.help_needed && selectedDay.help_resolved_at && (
                        <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/25 px-3 py-2">
                          <p className="text-emerald-600 font-semibold">
                            Response from HO / Chairman — resolved {selectedDay.help_resolved_at}
                            {selectedDay.help_resolved_by_username ? ` by ${selectedDay.help_resolved_by_username}` : ''}
                          </p>
                          <p className="whitespace-pre-wrap text-slate-700">{selectedDay.help_response || '—'}</p>
                        </div>
                      )}
                      {selectedDay.revenue != null && (
                        <p><span className="text-slate-400 font-medium">Revenue: </span><span className="font-semibold text-slate-700 tabular-nums">{money(selectedDay.revenue)}</span></p>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-400 italic mt-1">Nothing submitted for this day yet.</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => navigate(`/ceo?date=${selectedDay.date_iso}`)}
                  className="text-xs font-semibold bg-brand-600 hover:bg-brand-700 text-white px-4 py-2.5 rounded-lg transition-colors flex-shrink-0"
                >
                  {selectedDay.has_report ? 'Edit this day’s report' : 'Fill this day’s report'} &rarr;
                </button>
              </div>
            </div>
          )}
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 flex items-center justify-between flex-wrap gap-3">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 mb-1">Day-by-Day Review</p>
              <h2 className="font-bold text-slate-900 tracking-tight text-lg">Daily Snapshot</h2>
              <p className="text-xs text-slate-500 mt-0.5">Step through your own past submissions one day at a time</p>
            </div>
            {reviewDates.length > 0 && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setReviewIndex((i) => Math.min(i + 1, reviewDates.length - 1))}
                  disabled={reviewIndex >= reviewDates.length - 1}
                  className="text-xs font-semibold bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:cursor-not-allowed text-slate-700 px-3 py-2 rounded-lg transition-colors"
                >
                  &larr; Older
                </button>
                <select
                  value={reviewIndex}
                  onChange={(e) => setReviewIndex(Number(e.target.value))}
                  className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-800 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
                >
                  {reviewDates.map((r, i) => (
                    <option key={i} value={i}>{r.report_date}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setReviewIndex((i) => Math.max(i - 1, 0))}
                  disabled={reviewIndex <= 0}
                  className="text-xs font-semibold bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:cursor-not-allowed text-slate-700 px-3 py-2 rounded-lg transition-colors"
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
                <div className="rounded-xl border border-slate-200 bg-white p-4">
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
                      <div className="flex justify-between"><span className="text-slate-500">Revenue</span><span className="font-semibold text-slate-700 tabular-nums">{financialForDay.revenue != null ? money(financialForDay.revenue) : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Cash Flow</span><span className="font-semibold text-slate-700 tabular-nums">{financialForDay.cash_flow != null ? money(financialForDay.cash_flow) : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Receivable</span><span className="font-semibold text-slate-700 tabular-nums">{financialForDay.receivable != null ? money(financialForDay.receivable) : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Payable</span><span className="font-semibold text-slate-700 tabular-nums">{financialForDay.payable != null ? money(financialForDay.payable) : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Other Amount</span><span className="font-semibold text-slate-700 tabular-nums">{financialForDay.other_amount != null ? money(financialForDay.other_amount) : '—'}</span></div>
                    </div>
                  ) : (
                    <p className="text-xs text-slate-400 italic">No submission this day</p>
                  )}
                  {financialForDay?.performance && (
                    <div className="mt-3 pt-3 border-t border-slate-100">
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
                        <span className={financialForDay.performance.variance_amount > 0 ? 'text-emerald-600' : financialForDay.performance.variance_amount < 0 ? 'text-red-600' : 'text-slate-500'}>
                          {formatVariance(financialForDay.performance.variance_amount)} vs monthly target
                        </span>
                      </div>
                    </div>
                  )}
                </div>

                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Social Activity</p>
                  {socialForDay ? (
                    <div className="space-y-1.5 text-xs">
                      <div className="flex justify-between items-center"><span className="text-slate-500">Event</span><span className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${socialForDay.event_flag ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-600' : 'bg-slate-100 text-slate-500'}`}>{socialForDay.event_flag ? 'Yes' : 'No'}</span></div>
                      <div className="flex justify-between items-center"><span className="text-slate-500">Meeting</span><span className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${socialForDay.meeting_flag ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-600' : 'bg-slate-100 text-slate-500'}`}>{socialForDay.meeting_flag ? 'Yes' : 'No'}</span></div>
                      <div className="flex justify-between items-center"><span className="text-slate-500">Golf</span><span className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${socialForDay.golf_flag ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-600' : 'bg-slate-100 text-slate-500'}`}>{socialForDay.golf_flag ? 'Yes' : 'No'}</span></div>
                      {socialForDay.other_notes && (
                        <div className="pt-1.5 border-t border-slate-100">
                          <p className="text-slate-500 mb-0.5">Other</p>
                          <p className="text-slate-700 line-clamp-2">{socialForDay.other_notes}</p>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-400 italic">No submission this day</p>
                  )}
                </div>

                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Daily Activities</p>
                  <div className="space-y-2 text-xs">
                    <div>
                      <p className="text-slate-500 mb-0.5">Meetings{currentReview.meetings_count != null ? ` (${currentReview.meetings_count})` : ''}</p>
                      <p className="text-slate-700 line-clamp-2">{currentReview.meetings_attended || '—'}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 mb-0.5">Site Visits</p>
                      <p className="text-slate-700 line-clamp-2">{currentReview.site_visits || '—'}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 mb-0.5">Key Decisions</p>
                      <p className="text-slate-700 line-clamp-2">{currentReview.key_decisions || '—'}</p>
                    </div>
                    {currentReview.key_achievement && (
                      <div>
                        <p className="text-emerald-600 font-medium mb-0.5">Key Achievement</p>
                        <p className="text-slate-700 line-clamp-2">{currentReview.key_achievement}</p>
                      </div>
                    )}
                    <div>
                      <p className="text-slate-500 mb-0.5">Next Day Schedule</p>
                      <p className="text-slate-700 line-clamp-2">{currentReview.next_day_schedule || '—'}</p>
                    </div>
                    <div className="pt-1.5 border-t border-slate-100 flex justify-between">
                      <span className="text-slate-500">Gym / Swim / Cycle</span>
                      <span className="font-semibold text-slate-700">
                        {(healthForDay?.gym_percent ?? currentReview.gym_percent) != null ? `${healthForDay?.gym_percent ?? currentReview.gym_percent}%` : '—'} / {(healthForDay?.swimming_percent ?? currentReview.swimming_percent) != null ? `${healthForDay?.swimming_percent ?? currentReview.swimming_percent}%` : '—'} / {(healthForDay?.cycle_percent ?? currentReview.cycle_percent) != null ? `${healthForDay?.cycle_percent ?? currentReview.cycle_percent}%` : '—'}
                      </span>
                    </div>
                    {currentReview.other_notes && (
                      <div>
                        <p className="text-slate-500 mb-0.5">Other</p>
                        <p className="text-slate-700 line-clamp-2">{currentReview.other_notes}</p>
                      </div>
                    )}
                    {safeUrl(currentReview.document_url) && (
                      <a href={safeUrl(currentReview.document_url)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-600 hover:text-brand-700 pt-1">
                        View attachment
                      </a>
                    )}
                  </div>
                </div>

                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">Health and Fitness</p>
                  {healthForDay ? (
                    <div className="space-y-1.5 text-xs">
                      <div className="flex justify-between"><span className="text-slate-500">Steps</span><span className="font-semibold text-slate-700 tabular-nums">{healthForDay.daily_steps != null ? healthForDay.daily_steps.toLocaleString() : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Heart Rate</span><span className="font-semibold text-slate-700 tabular-nums">{healthForDay.heart_rate != null ? `${healthForDay.heart_rate} bpm` : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Sleep Time</span><span className="font-semibold text-slate-700 tabular-nums">{healthForDay.sleep_hours != null ? `${healthForDay.sleep_hours} hrs` : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Blood Pressure</span><span className="font-semibold text-slate-700 tabular-nums">{healthForDay.bp_systolic != null && healthForDay.bp_diastolic != null ? `${healthForDay.bp_systolic}/${healthForDay.bp_diastolic}` : '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Stress Level</span><span className="font-semibold text-slate-700">{healthForDay.stress_level || '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Energy / Mood</span><span className="font-semibold text-slate-700">{healthForDay.energy_mood || '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500">Weight</span><span className="font-semibold text-slate-700 tabular-nums">{healthForDay.weight_kg != null ? `${healthForDay.weight_kg} kg` : '—'}</span></div>
                      {healthForDay.is_sick && (
                        <p className="text-rose-600 font-semibold">Feeling sick this day</p>
                      )}
                      <div className="flex justify-between"><span className="text-slate-500">Gym / Swim / Cycle</span><span className="font-semibold text-slate-700">{healthForDay.gym_percent != null ? `${healthForDay.gym_percent}%` : '—'} / {healthForDay.swimming_percent != null ? `${healthForDay.swimming_percent}%` : '—'} / {healthForDay.cycle_percent != null ? `${healthForDay.cycle_percent}%` : '—'}</span></div>
                      {healthForDay.other_notes && (
                        <div className="pt-1.5 border-t border-slate-100">
                          <p className="text-slate-500 mb-0.5">Other</p>
                          <p className="text-slate-700 line-clamp-2">{healthForDay.other_notes}</p>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-400 italic">No submission this day</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-center text-sm text-slate-400 py-14">No submissions recorded yet.</p>
          )}
        </section>

        {data.recent_financials.length > 0 && (
          <section className={`mt-10 ${GLASS} overflow-hidden`}>
            <div className="px-6 py-4 border-b border-slate-200/70">
              <h2 className="font-bold text-slate-900 tracking-tight">Recent Business Performance History</h2>
            </div>
            <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 flex items-center gap-1">
              <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
              Swipe to see all columns
            </p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-max text-sm">
                <thead className="bg-white text-slate-500 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="px-6 py-3 text-left">Date</th>
                    <th className="px-6 py-3 text-right">Revenue</th>
                    <th className="px-6 py-3 text-right">Cash Flow</th>
                    <th className="px-6 py-3 text-right">Receivable</th>
                    <th className="px-6 py-3 text-right">Payable</th>
                    <th className="px-6 py-3 text-right">Other Amount</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.recent_financials.map((f, i) => (
                    <tr key={i} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-3 text-slate-600">{f.report_date}</td>
                      <td className="px-6 py-3 text-right font-medium text-slate-700">{f.revenue != null ? money(f.revenue) : '—'}</td>
                      <td className="px-6 py-3 text-right font-medium text-slate-700">{f.cash_flow != null ? money(f.cash_flow) : '—'}</td>
                      <td className="px-6 py-3 text-right font-medium text-slate-700">{f.receivable != null ? money(f.receivable) : '—'}</td>
                      <td className="px-6 py-3 text-right font-medium text-slate-700">{f.payable != null ? money(f.payable) : '—'}</td>
                      <td className="px-6 py-3 text-right font-medium text-slate-700">{f.other_amount != null ? money(f.other_amount) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        <Footer light />
      </main>
    </div>
  );
}
