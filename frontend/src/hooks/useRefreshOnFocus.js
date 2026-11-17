import { useEffect, useRef } from 'react';

// m-09: dashboards used to load once on mount and never refresh again —
// a page left open showed silently stale data until the user hit the
// manual Refresh button. This hook re-runs the page's existing load()
// when the tab becomes visible again or the window regains focus, so a
// Chairman returning to an open dashboard sees current data.
//
// Deliberately NOT interval polling: re-running the heavy admin
// overview every minute would multiply the M-07 query cost for every
// open tab. Focus/visibility is the cheap trigger — it fires exactly
// when the user is about to look at the page again.
//
// Auth-failure stop (same pattern as NotificationBell's 60 s poll):
// after 3 consecutive 401s the listeners are removed, so an expired
// session never keeps re-firing loads. Most page loads swallow errors
// internally and the global 401 handler redirects to login, so this
// is belt-and-braces for loads that propagate the error.
// Focus and visibilitychange fire together on a tab switch, so
// refreshes within 2 s of each other are coalesced into one.
const MAX_AUTH_FAILURES = 3;
const MIN_REFRESH_GAP_MS = 2000;

export default function useRefreshOnFocus(onRefresh) {
  const callbackRef = useRef(onRefresh);
  callbackRef.current = onRefresh;
  const lastRefreshRef = useRef(0);
  const authFailuresRef = useRef(0);

  useEffect(() => {
    let stopped = false;
    let onVisible;
    let onFocus;

    const maybeRefresh = () => {
      if (stopped || document.hidden) return;
      const now = Date.now();
      if (now - lastRefreshRef.current < MIN_REFRESH_GAP_MS) return;
      if (authFailuresRef.current >= MAX_AUTH_FAILURES) {
        stopped = true;
        return;
      }
      lastRefreshRef.current = now;
      let result;
      try {
        result = callbackRef.current();
      } catch (err) {
        if (err && err.status === 401) authFailuresRef.current += 1;
        return;
      }
      if (result && typeof result.catch === 'function') {
        result.catch((err) => {
          if (err && err.status === 401) {
            authFailuresRef.current += 1;
            if (authFailuresRef.current >= MAX_AUTH_FAILURES) {
              stopped = true;
              document.removeEventListener('visibilitychange', onVisible);
              window.removeEventListener('focus', onFocus);
            }
          }
        });
      }
    };

    onVisible = () => {
      if (!document.hidden) maybeRefresh();
    };
    onFocus = () => maybeRefresh();

    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onFocus);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onFocus);
    };
  }, []);
}
