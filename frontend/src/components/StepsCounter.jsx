function classifyStatus(steps) {
  if (steps === '' || steps === null || steps === undefined) return null;
  const n = Number(steps);
  if (Number.isNaN(n) || n < 0) return null;
  if (n < 3000) return { tier: 'red', label: 'Sedentary', badgeClass: 'bg-red-500/10 border border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400' };
  if (n < 7000) return { tier: 'yellow', label: 'Moderate Activity', badgeClass: 'bg-amber-500/10 border border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400' };
  return { tier: 'green', label: 'Active & Healthy', badgeClass: 'bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' };
}

export default function StepsCounter({ value, onChange }) {
  const status = classifyStatus(value);

  return (
    <div className="sm:col-span-2">
      <div className="flex items-center justify-between mb-2.5">
        <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
          Daily Step Count <span className="text-slate-500 dark:text-slate-400 font-normal">(optional)</span>
        </p>
        <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${status ? status.badgeClass : 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400'}`}>
          {status ? `${Number(value).toLocaleString()} steps · ${status.label}` : 'No data yet'}
        </span>
      </div>
      <div className="relative mb-3">
        <input
          type="number"
          min="0"
          step="1"
          name="daily_steps"
          placeholder="e.g. 6500"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
        />
        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500 text-xs font-medium">steps</span>
      </div>
      <div className="grid grid-cols-3 gap-2.5">
        <div
          className={`steps-card rounded-xl border-2 border-red-500/20 dark:border-red-500/30 bg-red-500/5 px-3 py-3 text-center ${
            status?.tier === 'red' ? 'steps-card-active' : ''
          }`}
          id="steps-card-red"
        >
          <p className="text-[10px] font-bold uppercase tracking-wide text-red-600 dark:text-red-400">Sedentary</p>
          <p className="text-xs text-red-600/80 dark:text-red-400/80 mt-0.5">Under 3,000 steps</p>
        </div>
        <div
          className={`steps-card rounded-xl border-2 border-amber-500/20 dark:border-amber-500/30 bg-amber-500/5 px-3 py-3 text-center ${
            status?.tier === 'yellow' ? 'steps-card-active' : ''
          }`}
          id="steps-card-yellow"
        >
          <p className="text-[10px] font-bold uppercase tracking-wide text-amber-600 dark:text-amber-400">Moderate</p>
          <p className="text-xs text-amber-600/80 dark:text-amber-400/80 mt-0.5">3,000 – 6,999 steps</p>
        </div>
        <div
          className={`steps-card rounded-xl border-2 border-emerald-500/20 dark:border-emerald-500/30 bg-emerald-500/5 px-3 py-3 text-center ${
            status?.tier === 'green' ? 'steps-card-active' : ''
          }`}
          id="steps-card-green"
        >
          <p className="text-[10px] font-bold uppercase tracking-wide text-emerald-600 dark:text-emerald-400">Active</p>
          <p className="text-xs text-emerald-600/80 dark:text-emerald-400/80 mt-0.5">7,000+ steps</p>
        </div>
      </div>
    </div>
  );
}
