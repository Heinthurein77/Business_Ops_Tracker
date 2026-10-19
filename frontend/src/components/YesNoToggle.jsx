export default function YesNoToggle({ label, value, onChange }) {
  return (
    <div>
      <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">{label}</p>
      <div className="inline-flex rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden w-full">
        <button
          type="button"
          onClick={() => onChange(true)}
          className={`flex-1 px-4 py-2.5 text-sm font-semibold transition-colors ${value ? 'bg-emerald-600 text-white' : 'bg-white/70 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'}`}
        >
          Yes
        </button>
        <button
          type="button"
          onClick={() => onChange(false)}
          className={`flex-1 px-4 py-2.5 text-sm font-semibold transition-colors border-l border-slate-200 dark:border-slate-700 ${!value ? 'bg-slate-600 text-white' : 'bg-white/70 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'}`}
        >
          No
        </button>
      </div>
    </div>
  );
}
