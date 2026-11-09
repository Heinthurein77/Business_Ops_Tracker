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

/** D-5: a stored attachment URL is only a key reference — never render it
 * directly (it is a bearer link: a 7-day presigned URL, or permanently
 * public when a public base URL is set). Extract the storage key and route
 * through the session-gated view endpoint, which checks BU ownership and
 * 302-redirects to a fresh 15-minute presigned URL. Returns null for
 * anything that does not look like a stored attachment URL. */
export function viewUploadUrl(storedUrl) {
  const key = extractUploadKey(storedUrl);
  if (!key) return null;
  return `/api/ops/uploads/view?key=${encodeURIComponent(key)}`;
}

function extractUploadKey(storedUrl) {
  if (!storedUrl || typeof storedUrl !== 'string') return null;
  if (storedUrl.startsWith('/uploads/')) {
    const key = storedUrl.slice('/uploads/'.length);
    return key && !key.includes('..') ? key : null;
  }
  let path;
  try {
    path = new URL(storedUrl, window.location.origin).pathname;
  } catch {
    return null;
  }
  const key = decodeURIComponent(path).replace(/^\/+/, '');
  // R2 keys are always "<folder>/<uuid>.<ext>".
  return key.includes('/') && !key.includes('..') ? key : null;
}
