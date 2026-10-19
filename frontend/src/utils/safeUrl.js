/** Defense-in-depth guard for rendering a backend-supplied URL (an
 * attachment or profile photo link) into href/src. Every such value comes
 * from src/r2_storage.py, which only ever returns an http(s) R2/presigned
 * URL or a "/uploads/..." local-fallback path — never anything else today —
 * but this is the last checkpoint before render rather than trusting that
 * unconditionally, so a bad value (e.g. a "javascript:" URI, however it got
 * there) renders as nothing instead of as a clickable/loadable link. */
export function safeUrl(url) {
  if (!url) return null;
  if (url.startsWith('/')) return url;
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? url : null;
  } catch {
    return null;
  }
}
