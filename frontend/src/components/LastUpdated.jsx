/** SM-5: staleness indicator for dashboards.
 *
 * Dashboards load once on mount and never refresh again, so a page left
 * open shows silently stale data indefinitely. Every dashboard renders
 * this next to its title: the time its data was last loaded, plus a manual
 * refresh button that re-runs the page's load(). It does not poll — the
 * user refreshes deliberately, and mutate actions refetch before acting.
 */
export default function LastUpdated({ updatedAt, onRefresh, refreshing }) {
  const label = updatedAt
    ? updatedAt.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
    : '—';
  return (
    <span className="inline-flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
      <span title={updatedAt ? `Data loaded at ${updatedAt.toLocaleString()}` : 'Data not loaded yet'}>
        Updated {label}
      </span>
      <button
        type="button"
        onClick={onRefresh}
        disabled={refreshing}
        className="font-semibold text-brand-600 dark:text-brand-400 hover:underline disabled:opacity-50"
      >
        {refreshing ? 'Refreshing…' : 'Refresh'}
      </button>
    </span>
  );
}
