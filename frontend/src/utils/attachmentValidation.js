// Mirrors ALLOWED_ATTACHMENT_EXTENSIONS / MAX_ATTACHMENT_SIZE_BYTES in
// src/routers/ceo.py and src/routers/ho.py — client-side check is purely
// for immediate feedback, the server re-validates independently either way.
export const ALLOWED_ATTACHMENT_EXTENSIONS = ['pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt'];
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024; // 10 MB

/** Returns an error message if the file fails the same checks the backend
 * enforces, or null if it's fine to attach. */
export function validateAttachment(file) {
  if (!file) return null;
  const extension = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : '';
  if (!ALLOWED_ATTACHMENT_EXTENSIONS.includes(extension)) {
    return `Unsupported file type. Allowed: ${ALLOWED_ATTACHMENT_EXTENSIONS.join(', ')}`;
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    return 'File must be 10 MB or smaller.';
  }
  return null;
}
