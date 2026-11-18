import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import ActivityMonthCalendar from '../components/ActivityMonthCalendar';
import Footer from '../components/Footer';
import LastUpdated from '../components/LastUpdated';
import { money, localTodayISO } from '../utils/format';
import DayDetailPanel from '../components/DayDetailPanel';
import { GLASS, PAGE_BG } from '../utils/glassTheme';


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
  // F-12: when the history was last refreshed (LastUpdated, header).
  const [updatedAt, setUpdatedAt] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const payload = await api.ceoDashboard(month);
      setData(payload);
      setReviewIndex(0);
      setSelectedDay(null);
      setError('');
      setUpdatedAt(new Date());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load history');
    } finally {
      setRefreshing(false);
    }
  }, [month]);

  useEffect(() => { load(); }, [load]);

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

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
            <LastUpdated updatedAt={updatedAt} onRefresh={load} refreshing={refreshing} />
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
                  {/* PM-5: the Fill/Edit button used to render for any day,
                      including future days and days outside the 7-day edit
                      window — every one then 400d on submit. */}
                  {(() => {
                    const todayISO = localTodayISO();
                    const daysAgo = Math.floor((new Date(`${todayISO}T00:00:00`) - new Date(`${selectedDay.date_iso}T00:00:00`)) / 86400000);
                    const editable = daysAgo >= 0 && daysAgo <= 7;
                    const reason = daysAgo < 0
                      ? 'This day is in the future — reports cannot be filed ahead.'
                      : daysAgo > 7
                        ? 'Outside the 7-day edit window — contact Head Office; correction is manual.'
                        : '';
                    return (
                      <>
                        <button
                          type="button"
                          disabled={!editable}
                          title={editable ? '' : reason}
                          onClick={() => navigate(`/ceo?date=${selectedDay.date_iso}`)}
                          className="mt-3 text-xs font-semibold bg-brand-600 hover:bg-brand-700 disabled:bg-slate-300 disabled:cursor-not-allowed text-white px-4 py-2.5 rounded-lg transition-colors flex-shrink-0"
                        >
                          {selectedDay.has_report ? 'Edit this day’s report' : 'Fill this day’s report'} &rarr;
                        </button>
                        {!editable && <p className="text-xs text-slate-400 mt-2">{reason}</p>}
                      </>
                    );
                  })()}
                </div>
                <div className="min-w-0 flex-1">
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
              </div>
            </div>
          )}
        </section>

                <DayDetailPanel
          activityRows={data.activity_rows}
          financialRows={data.financial_rows}
          healthRows={data.health_rows}
          socialRows={data.social_rows}
          reviewIndex={reviewIndex}
          onReviewIndexChange={setReviewIndex}
          subtitle="Step through your own past submissions one day at a time"
          emptyText="No submissions recorded yet."
          showMeetingsCount
          showKeyAchievement
          gymFromHealth
          healthExtra={(healthForDay) => healthForDay ? (
                  <>
                    <div className="flex justify-between"><span className="text-slate-500">Stress Level</span><span className="font-semibold text-slate-700">{healthForDay.stress_level || '—'}</span></div>
                    <div className="flex justify-between"><span className="text-slate-500">Energy / Mood</span><span className="font-semibold text-slate-700">{healthForDay.energy_mood || '—'}</span></div>
                    <div className="flex justify-between"><span className="text-slate-500">Weight</span><span className="font-semibold text-slate-700 tabular-nums">{healthForDay.weight_kg != null ? healthForDay.weight_kg + ' kg' : '—'}</span></div>
                    {healthForDay.is_sick && (
                      <p className="text-rose-600 font-semibold">Feeling sick this day</p>
                    )}
                  </>
                ) : null}
        />


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
