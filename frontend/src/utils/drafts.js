/** Draft persistence (SM-1): an unsaved form survives a session expiry.
 *
 * The global 401 handler (api/client.js) drops the user to /login when a
 * session ends mid-form. Without this, the form's contents would be gone
 * after re-login. Each form saves its state to localStorage on every
 * change (debounced by the caller via useEffect) and restores it on mount;
 * a successful submit clears it. Keys are namespaced per form; values are
 * plain JSON. Storage failures (private mode, quota) are swallowed — a
 * draft is a convenience, never a blocker.
 */

const PREFIX = 'bot-draft:';

export function saveDraft(key, data) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(data ?? null));
  } catch { /* draft persistence is best-effort */ }
}

export function loadDraft(key) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (!raw) return null;
    const data = JSON.parse(raw);
    return data && typeof data === 'object' ? data : null;
  } catch {
    return null;
  }
}

export function clearDraft(key) {
  try {
    localStorage.removeItem(PREFIX + key);
  } catch { /* best-effort */ }
}

/** True when the stored draft holds anything the user typed (as opposed
 * to an empty form saved by an early effect run). Callers use it to show a
 * "draft restored" hint instead of silently pre-filling. */
export function draftHasContent(data) {
  if (!data || typeof data !== 'object') return false;
  return Object.values(data).some((v) => {
    if (v === '' || v === null || v === undefined) return false;
    if (Array.isArray(v)) return v.length > 0 && v.some((row) => row && Object.values(row).some((c) => c !== '' && c !== null && c !== undefined));
    return true;
  });
}
