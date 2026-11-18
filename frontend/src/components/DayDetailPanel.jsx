import StatusPill from './StatusPill';
import { formatVariance } from '../utils/quadrantMetrics';
import { money } from '../utils/format';
import { GLASS } from '../utils/glassTheme';
import { viewUploadUrl } from '../utils/safeUrl';

/** The "Daily Snapshot" day-by-day review panel (F-M1) — previously
 * copy-pasted in BusinessUnitDetailPage, CeoHistoryPage and HeadOfficePage
 * with only `dark:` variants and two small extras differing. All three
 * feed it the same row shape: `activityRows` (the day list, each with
 * `report_date`/`submitted_at`), plus `financialRows`/`healthRows`/
 * `socialRows` keyed by `report_date`.
 *
 * Props:
 * - `reviewIndex`, `onReviewIndexChange`: controlled step-through state.
 * - `subtitle`: "Step through past submissions one day at a time" etc.
 * - `emptyText`: shown when there are no rows at all.
 * - `showMeetingsCount`, `showKeyAchievement`: CEO-history extras.
 * - `gymFromHealth`: when true, Gym/Swim/Cycle come from the health row
 *   (CeoHistory) instead of the activity row (BU Detail / HO).
 * - `healthExtra`: optional node rendered inside the Health card
 *   (CeoHistory's stress/energy/weight/sick rows).
 */
export default function DayDetailPanel({
  activityRows = [],
  financialRows = [],
  healthRows = [],
  socialRows = [],
  reviewIndex,
  onReviewIndexChange,
  subtitle = 'Step through past submissions one day at a time',
  emptyText = 'No submissions recorded yet.',
  showMeetingsCount = false,
  showKeyAchievement = false,
  gymFromHealth = false,
  healthExtra = null,
}) {
  const currentReview = activityRows[reviewIndex] || null;
  const financialForDay = currentReview ? financialRows.find((f) => f.report_date === currentReview.report_date) : null;
  const healthForDay = currentReview ? healthRows.find((h) => h.report_date === currentReview.report_date) : null;
  const socialForDay = currentReview ? socialRows.find((s) => s.report_date === currentReview.report_date) : null;
  const gym = gymFromHealth ? healthForDay?.gym_percent ?? currentReview?.gym_percent : currentReview?.gym_percent;
  const swim = gymFromHealth ? healthForDay?.swimming_percent ?? currentReview?.swimming_percent : currentReview?.swimming_percent;
  const cycle = gymFromHealth ? healthForDay?.cycle_percent ?? currentReview?.cycle_percent : currentReview?.cycle_percent;

  return (
    <section className={`${GLASS} overflow-hidden`}>
      <div className="p-6 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between flex-wrap gap-3">
        <div>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Day-by-Day Review</p>
          <h2 className="font-bold text-slate-900 dark:text-white tracking-tight text-lg">Daily Snapshot</h2>
          <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>
        </div>
        {activityRows.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onReviewIndexChange(Math.min(reviewIndex + 1, activityRows.length - 1))}
              disabled={reviewIndex >= activityRows.length - 1}
              className="text-xs font-semibold bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-800 dark:text-slate-200 px-3 py-2 rounded-lg transition-colors"
            >
              &larr; Older
            </button>
            <select
              value={reviewIndex}
              onChange={(e) => onReviewIndexChange(Number(e.target.value))}
              aria-label="Review day"
              className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm font-semibold text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
            >
              {activityRows.map((r, i) => (
                <option key={i} value={i}>{r.report_date}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => onReviewIndexChange(Math.max(reviewIndex - 1, 0))}
              disabled={reviewIndex <= 0}
              className="text-xs font-semibold bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-800 dark:text-slate-200 px-3 py-2 rounded-lg transition-colors"
            >
              Newer &rarr;
            </button>
          </div>
        )}
      </div>

      {!currentReview ? (
        <p className="text-center text-sm text-slate-500 py-14">{emptyText}</p>
      ) : (
        <div className="p-6">
          <p className="text-xs text-slate-500 mb-4">Submitted at {currentReview.submitted_at || '—'}</p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
              <div className="flex items-center justify-between mb-3">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Business Performance</p>
                {financialForDay && <StatusPill status={financialForDay.status} />}
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
                    <StatusPill status={financialForDay.performance.status?.toLowerCase()} />
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
                  {['event_flag', 'meeting_flag', 'golf_flag'].map((key, i) => (
                    <div key={key} className="flex justify-between items-center">
                      <span className="text-slate-500">{['Event', 'Meeting', 'Golf'][i]}</span>
                      <span className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${socialForDay[key] ? 'bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-500'}`}>{socialForDay[key] ? 'Yes' : 'No'}</span>
                    </div>
                  ))}
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
                  <p className="text-slate-500 mb-0.5">Meetings{showMeetingsCount && currentReview.meetings_count != null ? ` (${currentReview.meetings_count})` : ''}</p>
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
                {showKeyAchievement && currentReview.key_achievement && (
                  <div>
                    <p className="text-emerald-600 font-medium mb-0.5">Key Achievement</p>
                    <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{currentReview.key_achievement}</p>
                  </div>
                )}
                <div>
                  <p className="text-slate-500 mb-0.5">Next Day Schedule</p>
                  <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{currentReview.next_day_schedule || '—'}</p>
                </div>
                <div className="pt-1.5 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                  <span className="text-slate-500">Gym / Swim / Cycle</span>
                  <span className="font-semibold text-slate-800 dark:text-slate-200">
                    {gym != null ? `${gym}%` : '—'} / {swim != null ? `${swim}%` : '—'} / {cycle != null ? `${cycle}%` : '—'}
                  </span>
                </div>
                {currentReview.other_notes && (
                  <div>
                    <p className="text-slate-500 mb-0.5">Other</p>
                    <p className="text-slate-700 dark:text-slate-300 line-clamp-2">{currentReview.other_notes}</p>
                  </div>
                )}
                {viewUploadUrl(currentReview.document_url) && (
                  <a href={viewUploadUrl(currentReview.document_url)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300 pt-1">
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
                  {healthExtra && (typeof healthExtra === 'function' ? healthExtra(healthForDay) : healthExtra)}
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
      )}
    </section>
  );
}
