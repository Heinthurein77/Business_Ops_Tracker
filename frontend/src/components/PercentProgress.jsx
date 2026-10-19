const BAR_COLORS = {
  brand: 'bg-brand-500',
  emerald: 'bg-emerald-500',
  amber: 'bg-amber-500',
};

export default function PercentProgress({ label, value, onChange, color = 'brand' }) {
  const numeric = value === '' || value === null || value === undefined ? null : Math.max(0, Math.min(100, Number(value) || 0));

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{label}</label>
        <span className="text-xs font-bold text-slate-500 dark:text-slate-400">{numeric === null ? 'No data' : `${numeric}%`}</span>
      </div>
      <input
        type="number"
        min="0"
        max="100"
        step="1"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="0"
        className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 px-3 py-2 text-sm mb-2 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
      />
      <div className="h-2.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
        <div className={`h-full rounded-full ${BAR_COLORS[color] || BAR_COLORS.brand} transition-all`} style={{ width: `${numeric || 0}%` }} />
      </div>
    </div>
  );
}
