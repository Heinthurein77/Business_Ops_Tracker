import Chart from 'react-apexcharts';
import QuadrantStatusCircle from './QuadrantStatusCircle';
import { BU_QUADRANTS, QUADRANT_ACCENT, QUADRANT_BADGE_CLASS, QUADRANT_ICON, QUADRANT_ICON_CLASS, QUADRANT_STATUS_LABEL } from '../utils/quadrantMetrics';
import { GLASS } from '../utils/glassTheme';

/** The "My Business at a Glance" 4-quadrant snapshot (F-M1) — previously
 * copy-pasted in CeoDashboardPage and HeadOfficePage (and again in
 * BusinessUnitDetailPage's own variant). `quadrantMetrics` is the output
 * of `buildQuadrantMetrics(...)`; `quadrant` is the raw payload for the
 * status circle. `financialTrend` is optional (CEO only). The `dark:`
 * variants are inert on CEO pages (no `dark` class there). */
export default function QuadrantSnapshot({ quadrant, quadrantMetrics, businessUnitName, alreadySubmitted, financialTrend, title = 'My Business at a Glance', eyebrow = 'Executive Snapshot' }) {
  return (
    <section className={`mb-8 ${GLASS} p-6`}>
      <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">{eyebrow}</p>
      <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg mb-5">{title}</h2>
      <div className="grid lg:grid-cols-12 gap-6 items-start">
        <div className="lg:col-span-3 flex flex-col items-center gap-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-6">
          <QuadrantStatusCircle
            financial={quadrant.financial_status}
            social={quadrant.social_status}
            activity={quadrant.activity_status}
            health={quadrant.health_status}
            size={240}
            label={businessUnitName}
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
              alreadySubmitted ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
            }`}
          >
            {alreadySubmitted ? 'Reported today' : 'No report submitted today'}
          </div>
        </div>

        <div className="lg:col-span-9 grid sm:grid-cols-2 gap-4">
          {BU_QUADRANTS.map((q) => {
            const m = quadrantMetrics[q.key];
            const badgeClass = QUADRANT_BADGE_CLASS[m.status] || QUADRANT_BADGE_CLASS.unknown;
            return (
              <div key={q.key} className="relative overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 pt-4 shadow-[0_1px_3px_rgba(15,23,42,0.04)] hover:shadow-lg hover:border-slate-300 hover:-translate-y-0.5 transition-all duration-200">
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
                        : 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400'
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
      {financialTrend && (
        <div className="mt-6 pt-5 border-t border-slate-100 dark:border-slate-800">
          <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.08em] mb-3">14-Day Trend</p>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { key: 'revenue', label: 'Revenue', color: '#6366f1' },
              { key: 'cash_flow', label: 'Cash Flow', color: '#0ea5e9' },
              { key: 'receivable', label: 'Receivable', color: '#f59e0b' },
              { key: 'payable', label: 'Payable', color: '#f97316' },
            ].map((t) => (
              <div key={t.key} className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-3.5">
                <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1">{t.label}</p>
                <Chart
                  options={{
                    chart: { type: 'area', sparkline: { enabled: true }, fontFamily: 'Inter, sans-serif' },
                    colors: [t.color],
                    stroke: { curve: 'smooth', width: 2 },
                    fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0.02 } },
                    tooltip: { enabled: false },
                  }}
                  series={[{ data: financialTrend[t.key] || [] }]}
                  type="area"
                  height={52}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
