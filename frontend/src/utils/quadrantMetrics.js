import { DAILY_STEP_GOAL } from '../constants';

// Maps this app's red/yellow/green/grey status vocabulary to the
// good/warning/critical/unknown vocabulary used by the quadrant card styling
// below (QUADRANT_BADGE_CLASS, QUADRANT_ACCENT, QUADRANT_ICON_CLASS).
export const STATUS_MAP = { green: 'good', yellow: 'warning', red: 'critical', grey: 'unknown', unknown: 'unknown' };

export const BU_QUADRANTS = [
  { key: 'financial', label: 'Business Performance' },
  { key: 'social', label: 'Social Activity' },
  { key: 'activity', label: 'Daily Activities' },
  { key: 'health', label: 'Health and Fitness' },
];

// Icon + accent treatment for the 4-Quadrant Snapshot cards — shared by the
// Executive Dashboard, the CEO's own dashboard, and the Business Unit Detail
// page, so all three read as one consistent visual language.
export const QUADRANT_ICON = {
  financial: 'M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  social: 'M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-2.13a4 4 0 10-4-4 4 4 0 004 4zm6 0a4 4 0 10-4-4',
  activity: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
  health: 'M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z',
};
export const QUADRANT_ACCENT = {
  good: 'bg-emerald-500',
  warning: 'bg-amber-500',
  critical: 'bg-red-500',
  unknown: 'bg-slate-300 dark:bg-slate-600',
};
export const QUADRANT_ICON_CLASS = {
  good: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  warning: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  critical: 'bg-red-500/10 text-red-600 dark:text-red-400',
  unknown: 'bg-slate-500/10 text-slate-500 dark:text-slate-400',
};

// Real actual-vs-CEO-target status: distinct from the financial_status
// quadrant above (which reflects cash-flow health rules, not budget
// adherence). Uses literal Red/Yellow/Green labels rather than the
// Good/Warning/Critical wording used elsewhere, since this badge is meant to
// read as a direct traffic-light verdict against the monthly target.
export const BUDGET_STATUS_COLOR = { red: '#ef4444', yellow: '#f59e0b', green: '#10b981', grey: '#64748b' };
export const BUDGET_STATUS_LABEL = { red: 'Red', yellow: 'Yellow', green: 'Green', grey: 'No Target' };
// Badge classes for Operations/Comparison — dark: variants activate only
// under the admin section's dark-mode toggle (see ThemeContext); CEO-facing
// pages never get the `dark` class applied, so they stay unaffected.
export const BUDGET_BADGE_CLASS = {
  red: 'bg-red-500/10 border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400',
  yellow: 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400',
  green: 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400',
  grey: 'bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400',
};

// Badge treatment for buildQuadrantMetrics' good/warning/critical/unknown
// vocabulary — shared by the Executive Dashboard, the CEO's own dashboard,
// and the Business Unit Detail page's "4-Quadrant Snapshot" panels, so all
// three use QuadrantStatusCircle + these cards and read as one design.
// dark: variants only activate under the admin section's toggle — inert on
// CEO-facing pages.
export const QUADRANT_BADGE_CLASS = {
  good: 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400',
  warning: 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400',
  critical: 'bg-red-500/10 border-red-500/20 dark:border-red-500/30 text-red-600 dark:text-red-400',
  unknown: 'bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400',
};
export const QUADRANT_STATUS_LABEL = { good: 'Good', warning: 'Warning', critical: 'Critical', unknown: 'No Data' };

export function money(v) {
  return `Ks ${Math.round(Number(v || 0)).toLocaleString()}`;
}

export function yesNo(flag) {
  return flag == null ? '—' : flag ? 'Yes' : 'No';
}

/** Formats a variance amount (actual minus target, in Kyat) with an explicit
 * +/- sign so "ahead of budget" vs "behind budget" reads at a glance. */
export function formatVariance(v) {
  if (v == null) return '—';
  const rounded = Math.round(Number(v));
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '';
  return `${sign}Ks ${Math.abs(rounded).toLocaleString()}`;
}

/** Converts a BU's quadrant fields (financial_status/cash_flow/revenue/... —
 * same shape whether it comes from admin `operations` entries or the CEO's
 * own `quadrant` payload) into the metrics shape BusinessUnitQuadrantDashboard expects. */
export function buildQuadrantMetrics(bu) {
  const activityValues = [bu.gym_percent, bu.swimming_percent, bu.cycle_percent].filter((v) => v != null);
  const activityAvg = activityValues.length ? Math.round(activityValues.reduce((a, b) => a + b, 0) / activityValues.length) : null;
  const engagedCount = [bu.event_flag, bu.meeting_flag, bu.golf_flag].filter((v) => v === true).length;
  const anySocialData = [bu.event_flag, bu.meeting_flag, bu.golf_flag].some((v) => v != null);

  return {
    financial: {
      status: STATUS_MAP[bu.financial_status] || 'unknown',
      reason: bu.financial_reason || null,
      displayValue: bu.cumulative_net_revenue != null ? money(bu.cumulative_net_revenue) : undefined,
      // Single-step Cumulative Financial Health: judged solely on Cumulative
      // Net Revenue (MTD Revenue + MTD Other Amount) vs the CEO's monthly
      // target, pro-rated to today's day-of-month — see
      // evaluate_cumulative_financial_health on the backend for the full rules.
      subMetrics: [
        { label: 'MTD Revenue', value: bu.mtd_revenue != null ? money(bu.mtd_revenue) : '—' },
        { label: 'MTD Other Amount', value: bu.mtd_other_amount != null ? money(bu.mtd_other_amount) : '—' },
        { label: 'Cumulative Net Revenue', value: bu.cumulative_net_revenue != null ? money(bu.cumulative_net_revenue) : '—' },
        { label: 'Achievement %', value: bu.achievement_pct != null ? `${bu.achievement_pct}%` : '—' },
        { label: 'Variance vs Monthly Target', value: formatVariance(bu.variance_amount) },
        {
          label: 'vs Target',
          value: BUDGET_STATUS_LABEL[bu.budget_status] || BUDGET_STATUS_LABEL.grey,
          color: BUDGET_STATUS_COLOR[bu.budget_status] || BUDGET_STATUS_COLOR.grey,
        },
        { label: 'Other', value: bu.financial_other_notes || '—' },
      ],
    },
    social: {
      status: STATUS_MAP[bu.social_status] || 'unknown',
      reason: bu.social_reason || null,
      displayValue: anySocialData ? `${engagedCount}/3 Engaged` : undefined,
      subMetrics: [
        { label: 'Event', value: yesNo(bu.event_flag) },
        { label: 'Meeting', value: yesNo(bu.meeting_flag) },
        { label: 'Golf', value: yesNo(bu.golf_flag) },
        { label: 'Events this month', value: bu.monthly_event_count != null ? `${bu.monthly_event_count}` : '—' },
        { label: 'Meetings this month', value: bu.monthly_meeting_count != null ? `${bu.monthly_meeting_count}` : '—' },
        { label: 'Golf this month', value: bu.monthly_golf_count != null ? `${bu.monthly_golf_count}` : '—' },
        { label: 'Other', value: bu.social_other_notes || '—' },
      ],
    },
    activity: {
      status: STATUS_MAP[bu.activity_status] || 'unknown',
      reason: bu.activity_reason || null,
      value: activityAvg,
      subMetrics: [
        { label: 'Gym', value: bu.gym_percent != null ? `${bu.gym_percent}%` : '—' },
        { label: 'Swimming', value: bu.swimming_percent != null ? `${bu.swimming_percent}%` : '—' },
        { label: 'Cycle', value: bu.cycle_percent != null ? `${bu.cycle_percent}%` : '—' },
        { label: 'Other', value: bu.activity_other_notes || '—' },
      ],
    },
    health: {
      status: STATUS_MAP[bu.health_status] || 'unknown',
      reason: bu.health_reason || null,
      displayValue: bu.daily_steps != null ? `${bu.daily_steps.toLocaleString()} steps` : undefined,
      subMetrics: [
        { label: 'Steps', value: bu.daily_steps != null ? `${bu.daily_steps.toLocaleString()} / ${DAILY_STEP_GOAL.toLocaleString()}` : '—' },
        { label: 'Heart Rate', value: bu.heart_rate != null ? `${bu.heart_rate} bpm` : '—' },
        { label: 'Sleep Time', value: bu.sleep_hours != null ? `${bu.sleep_hours} hrs` : '—' },
        { label: 'Blood Pressure', value: bu.bp_systolic != null && bu.bp_diastolic != null ? `${bu.bp_systolic}/${bu.bp_diastolic}` : '—' },
        { label: 'Other', value: bu.health_other_notes || '—' },
      ],
    },
  };
}
