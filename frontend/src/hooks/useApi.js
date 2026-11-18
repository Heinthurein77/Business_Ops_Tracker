import { useCallback, useEffect, useRef, useState } from 'react';
import useRefreshOnFocus from './useRefreshOnFocus';

/** Shared server-state hook (F-M2). Replaces the per-page copy-paste of
 * `loading`/`error`/`refreshing` + `useEffect(load, [])` +
 * `// eslint-disable-next-line react-hooks/exhaustive-deps`.
 *
 * - `loader(signal)` is called on mount and whenever `deps` change; the
 *   previous request is aborted and a stale response can never overwrite
 *   newer data (request id + AbortController) — the AdminDashboard
 *   business-unit-detail overwrite this fixes.
 * - `useRefreshOnFocus` is wired through here, so every page using this
 *   hook refreshes on tab focus automatically instead of opting in.
 * - Errors are surfaced in `error` (never swallowed); `refetch` re-runs
 *   the loader and reports `refreshing` while a background reload runs.
 */
export default function useApi(loader, deps = [], { refreshOnFocus = true, initialData = null } = {}) {
  const [data, setData] = useState(initialData);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);
  const requestIdRef = useRef(0);
  const abortRef = useRef(null);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const run = useCallback(async ({ background = false } = {}) => {
    const requestId = ++requestIdRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    if (background) setRefreshing(true);
    else setLoading(true);
    setError('');
    try {
      const result = await loaderRef.current(controller.signal);
      if (requestId !== requestIdRef.current || controller.signal.aborted) return null;
      setData(result);
      setUpdatedAt(new Date());
      return result;
    } catch (err) {
      if (err?.name === 'AbortError' || controller.signal.aborted) return null;
      if (requestId !== requestIdRef.current) return null;
      setError(err?.message || 'Failed to load');
      return null;
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    run();
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const refetch = useCallback(() => run({ background: true }), [run]);
  useRefreshOnFocus(refetch, refreshOnFocus);

  return { data, error, setError, loading, refreshing, refetch, updatedAt, setData };
}
