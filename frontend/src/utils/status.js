/** One status vocabulary (F-m1). Previously every page redefined the same
 * red/yellow/green/grey keys with conflicting labels — "Good" vs "Optimal"
 * vs "Active" for green — and duplicated the hex/class maps. The canonical
 * label set is the quadrant one (Good / Warning / Critical / No Data);
 * the two genuinely different vocabularies (KPI health, activity level)
 * keep their own names so a new reader can't confuse them for the same
 * thing. */

export const STATUS_LABEL = { green: 'Good', yellow: 'Warning', red: 'Critical', grey: 'No Data' };
// The backend also sends `unknown`; treat it as grey.
export const STATUS_LABEL_BY_KEY = { ...STATUS_LABEL, unknown: 'No Data' };

export const STATUS_PILL_CLASS = {
  green: 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400',
  yellow: 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400',
  red: 'bg-red-500/10 border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400',
  grey: 'bg-slate-500/10 border-slate-500/20 dark:border-slate-500/30 text-slate-500 dark:text-slate-400',
  unknown: 'bg-slate-500/10 border-slate-500/20 dark:border-slate-500/30 text-slate-500 dark:text-slate-400',
};

export const STATUS_COLOR = { green: '#10b981', yellow: '#f59e0b', red: '#ef4444', grey: '#94a3b8', unknown: '#cbd5e1' };

/** KPI-health wording ("Optimal / Needs Attention / Critical Warning /
 * No Target Set") — a different vocabulary from the quadrant status above,
 * used by the Executive Dashboard KPI row and the Head Office page. */
export const KPI_HEALTH_LABEL = { green: 'Optimal', yellow: 'Needs Attention', red: 'Critical Warning', grey: 'No Target Set' };
export const KPI_HEALTH_PILL_CLASS = { ...STATUS_PILL_CLASS };

/** Activity-level wording ("Active / Moderate / Low Activity / No Data")
 * used by the Activity Dashboard. */
export const ACTIVITY_STATUS_LABEL = { green: 'Active', yellow: 'Moderate', red: 'Low Activity', grey: 'No Data' };

/** Performance vs monthly target: backend sends RED/YELLOW/GREEN. */
export const PERFORMANCE_LABEL = { RED: 'Critical', YELLOW: 'Warning', GREEN: 'On Track' };
export const PERFORMANCE_BADGE_CLASS = {
  RED: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
  YELLOW: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  GREEN: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
};

export function normalizeStatus(status) {
  if (!status) return 'grey';
  const key = String(status).toLowerCase();
  if (key === 'unknown') return 'grey';
  return ['green', 'yellow', 'red', 'grey'].includes(key) ? key : 'grey';
}
