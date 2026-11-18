/** Single home for money/date formatting (F-M5) and local-calendar dates
 * (F-m4). Previously each page redefined `money` (null → `—` on one page,
 * `Ks 0` unrounded on another, `Math.round(undefined)` → `Ks NaN` in
 * ComparisonPage) and dates were computed from UTC (`toISOString()`),
 * which is the previous day/month in Yangon before 06:30 local.
 *
 * Canonical rules, taken from the pre-existing `money()` in
 * utils/quadrantMetrics.js: null/undefined/non-numeric → `Ks 0`, rounded.
 * Dates use local date parts, never UTC. */

export function money(v) {
  const n = Number(v);
  return `Ks ${Math.round(Number.isFinite(n) ? n : 0).toLocaleString()}`;
}

/** `YYYY-MM-DD` in the browser's local timezone (Yangon for this app). */
export function localTodayISO(date = new Date()) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

/** `YYYY-MM` in local time — the report month for a local "today". */
export function localMonth(date = new Date()) {
  return localTodayISO(date).slice(0, 7);
}

/** Local hour (0–23) — use instead of `new Date().getHours()` only via
 * this helper so the intent ("local time") stays explicit at call sites. */
export function localHour(date = new Date()) {
  return date.getHours();
}

/** Short date, e.g. `27 Jul 2026`. Accepts `YYYY-MM-DD` (parsed as a local
 * date, not UTC midnight) or a Date. Returns '—' for missing input. */
export function formatDate(iso, options = { day: '2-digit', month: 'short', year: 'numeric' }) {
  if (!iso) return '—';
  const d = typeof iso === 'string' ? parseLocalDate(iso) : iso;
  if (!d || Number.isNaN(d.getTime())) return typeof iso === 'string' ? iso : '—';
  return d.toLocaleDateString('en-GB', options);
}

/** Long date, e.g. `Monday, 27 July 2026`. */
export function formatDateLong(iso) {
  return formatDate(iso, { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' });
}

/** Parse a `YYYY-MM-DD` string as a local date (avoids the UTC-midnight
 * shift of `new Date('2026-11-18')`). Falls back to the Date constructor
 * for anything else. */
export function parseLocalDate(iso) {
  if (typeof iso !== 'string') return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}
