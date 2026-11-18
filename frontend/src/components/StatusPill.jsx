import { useId } from 'react';
import { normalizeStatus, STATUS_LABEL, STATUS_LABEL_BY_KEY, STATUS_PILL_CLASS, KPI_HEALTH_LABEL, ACTIVITY_STATUS_LABEL } from '../utils/status';

/** One status pill (F-m1). `vocabulary` picks the label set; the colour
 * always comes from the single STATUS_PILL_CLASS map. */
export default function StatusPill({ status, vocabulary = 'status', label, className = '' }) {
  const key = normalizeStatus(status);
  const labels = vocabulary === 'kpiHealth' ? KPI_HEALTH_LABEL : vocabulary === 'activity' ? ACTIVITY_STATUS_LABEL : STATUS_LABEL;
  const text = label ?? labels[key] ?? STATUS_LABEL_BY_KEY[key] ?? 'No Data';
  return (
    <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_PILL_CLASS[key]} ${className}`}>
      {text}
    </span>
  );
}
