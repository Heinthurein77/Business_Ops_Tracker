import { Field, inputClass, primaryButtonClass } from './FormFields';
import { money } from '../utils/format';
import { formatVariance } from '../utils/quadrantMetrics';
import { PERFORMANCE_BADGE_CLASS, PERFORMANCE_LABEL } from '../utils/status';
import { GLASS } from '../utils/glassTheme';

/** The monthly-target section (F-M1) shared by the CEO and Head Office
 * daily-report pages. The two pages differ only in heading text, the
 * "locked" note, and the Chairman's override affordance, passed in as
 * props — the four-field form and the cumulative-performance display are
 * one implementation.
 *
 * - `showForm`: render the editable four-field form (CEO: no target yet;
 *   HO: HO staff with no target, or Chairman while overriding).
 * - `headerExtra`: node rendered at the right of the header (Chairman's
 *   perf badge + Override button).
 * - `onCancel`: when set, a Cancel button appears next to Save.
 * - `formNote` / `lockNote`: the italic hint under the form / under the
 *   locked target display. */
export default function MonthlyTargetSection({
  eyebrow = 'Monthly Target',
  title,
  subtitle,
  monthlyTarget,
  perf,
  targetForm,
  updateTargetField,
  targetSaving,
  targetError,
  onSubmit,
  showForm,
  headerExtra,
  formNote,
  formAlert,
  lockNote,
  onCancel,
}) {
  const targetFields = [
    { key: 'monthly_revenue_target', label: 'Monthly Revenue Target', placeholder: 'e.g. 3000000', min: '0' },
    { key: 'monthly_cash_flow_target', label: 'Monthly Cash Flow Target', placeholder: 'e.g. 1500000' },
    { key: 'monthly_max_receivable', label: 'Max Receivable Allowed', placeholder: 'e.g. 500000', min: '0' },
    { key: 'monthly_max_payable', label: 'Max Payable Allowed', placeholder: 'e.g. 500000', min: '0' },
  ];

  return (
    <section className={`mb-8 ${GLASS} overflow-hidden`}>
      <div className="px-6 py-4 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
            </svg>
          </div>
          <div>
            <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight">{title}</h2>
            {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
          </div>
        </div>
        {headerExtra}
      </div>

      <div className="p-6">
        {targetError && (
          <div className="mb-4 rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-2.5 text-xs text-red-700 dark:text-red-300">{targetError}</div>
        )}

        {showForm ? (
          <form onSubmit={onSubmit} className="space-y-4">
            {formAlert}
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {targetFields.map((f) => (
                <Field key={f.key} label={f.label}>
                  {(id) => (
                    <input
                      id={id}
                      type="number"
                      min={f.min}
                      step="0.01"
                      value={targetForm[f.key]}
                      onChange={(e) => updateTargetField(f.key, e.target.value)}
                      className={inputClass}
                      placeholder={f.placeholder}
                    />
                  )}
                </Field>
              ))}
            </div>
            {formNote && <p className="text-xs text-slate-500 italic">{formNote}</p>}
            <div className="flex justify-end gap-2">
              {onCancel && (
                <button type="button" onClick={onCancel} className="text-xs font-semibold text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 px-4 py-2">
                  Cancel
                </button>
              )}
              <button type="submit" disabled={targetSaving} className={primaryButtonClass}>
                {targetSaving ? 'Saving…' : 'Save Target'}
              </button>
            </div>
          </form>
        ) : monthlyTarget ? (
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
            <div>
              <p className="text-xs text-slate-500">Revenue Target</p>
              <p className="font-semibold text-slate-800 dark:text-slate-200">{money(monthlyTarget.monthly_revenue_target)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Cash Flow Target</p>
              <p className="font-semibold text-slate-800 dark:text-slate-200">{money(monthlyTarget.monthly_cash_flow_target)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Max Receivable</p>
              <p className="font-semibold text-slate-800 dark:text-slate-200">{money(monthlyTarget.monthly_max_receivable)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Max Payable</p>
              <p className="font-semibold text-slate-800 dark:text-slate-200">{money(monthlyTarget.monthly_max_payable)}</p>
            </div>
            {lockNote && (
              <p className="sm:col-span-2 lg:col-span-4 flex items-center gap-1.5 text-[11px] text-slate-500">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
                {lockNote}
              </p>
            )}
          </div>
        ) : (
          <p className="text-xs text-slate-500 italic">No monthly target set yet.</p>
        )}

        {perf && (
          <div className="mt-6 pt-6 border-t border-slate-200/70 dark:border-slate-800">
            <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Cumulative Performance vs Target &middot; Day {perf.day_number} of {perf.days_in_month}
              </p>
              <span className={`text-xs font-bold px-3 py-1 rounded-full border ${PERFORMANCE_BADGE_CLASS[perf.status]}`}>
                {PERFORMANCE_LABEL[perf.status]}
              </span>
            </div>
            <div className="mb-4">
              <div className="flex items-baseline justify-between mb-1.5">
                <p className="text-2xl font-bold text-slate-900 dark:text-slate-100 tabular-nums">
                  {perf.achievement_pct != null ? `${perf.achievement_pct}%` : '—'}
                </p>
                <p className="text-xs text-slate-500">of Expected MTD Target ({money(perf.expected_mtd_target)})</p>
              </div>
              <div className="h-2.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    perf.status === 'GREEN' ? 'bg-emerald-500' : perf.status === 'YELLOW' ? 'bg-amber-500' : 'bg-red-500'
                  }`}
                  style={{ width: `${Math.max(2, Math.min(100, perf.achievement_pct ?? 0))}%` }}
                />
              </div>
            </div>
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 text-sm mb-3">
              <div className="rounded-lg bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 px-4 py-3">
                <p className="text-xs text-slate-500">MTD Revenue</p>
                <p className="font-bold text-slate-800 dark:text-slate-200">{money(perf.mtd_revenue)}</p>
              </div>
              <div className="rounded-lg bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 px-4 py-3">
                <p className="text-xs text-slate-500">MTD Other Amount</p>
                <p className="font-bold text-slate-800 dark:text-slate-200">{money(perf.mtd_other_amount)}</p>
              </div>
              <div className="rounded-lg bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 px-4 py-3">
                <p className="text-xs text-slate-500">Cumulative Net Revenue</p>
                <p className="font-bold text-slate-800 dark:text-slate-200">{money(perf.cumulative_net_revenue)}</p>
              </div>
              <div className="rounded-lg bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 px-4 py-3">
                <p className="text-xs text-slate-500">Variance vs Monthly Target</p>
                <p className={`font-bold ${perf.variance_amount > 0 ? 'text-emerald-600 dark:text-emerald-400' : perf.variance_amount < 0 ? 'text-red-600 dark:text-red-400' : 'text-slate-700 dark:text-slate-200'}`}>
                  {formatVariance(perf.variance_amount)}
                </p>
              </div>
            </div>
            {perf.summary_notes?.length > 0 && (
              <ul className="space-y-1.5">
                {perf.summary_notes.map((flag, i) => (
                  <li key={i} className="text-xs text-slate-500 flex items-start gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600 mt-1 flex-shrink-0" />
                    {flag}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {monthlyTarget && !perf && (
          <p className="mt-4 text-xs text-slate-500 italic">Submit today&apos;s Business Performance below to see your performance against this target.</p>
        )}
      </div>
    </section>
  );
}
