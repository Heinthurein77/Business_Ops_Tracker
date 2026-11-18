import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { clearDraft, loadDraft, saveDraft } from '../utils/drafts';
import { useDebouncedEffect } from './useDebounce';
import { localTodayISO } from '../utils/format';

/** Shared daily-report state machine (F-M1, F-m2, F-m3). Both daily-report
 * pages (CEO + Head Office) used to duplicate this whole block: load →
 * map server payload to a 30-field form → debounced draft save/restore →
 * target save → submit with 34/25 hand-written `formData.append(...)`
 * calls → reload.
 *
 * Config:
 * - `emptyForm`, `emptyTargetForm`: initial form shapes.
 * - `draftKey`: localStorage draft namespace.
 * - `fetchDashboard(signal)`: returns the page payload.
 * - `mapForm(payload)`: payload → form values.
 * - `draftDate(payload)`: the ISO date the draft is stamped with.
 * - `isFormEmpty(form)`: true when the server form is empty (draft may
 *   restore over it).
 * - `fieldMap`: form key → FormData name, in submit order. Booleans are
 *   serialised as 'true'/'false'; everything else as-is. The form is
 *   serialised by iterating this map — no hand-written appends.
 * - `submitReport(formData)`, `setTargetApi(payload)`,
 *   `targetMonth(payload)`, `extraSubmitFields(formData, data)` (e.g.
 *   CEO's `report_date` when editing a past day).
 * - `onLoadExtra(payload)`: page-specific state resets (HO resets the
 *   review index / target editing).
 * - `addNoteApi(text)`: optional — only the CEO page has record notes.
 *
 * Behaviour notes:
 * - Draft autosave is debounced (~400 ms) via useDebouncedEffect.
 * - A reload failure after a successful submit/save does NOT report as a
 *   submit failure (F-m3): it sets `refreshError` ("saved, but couldn't
 *   refresh") while the success state stands.
 */
export default function useDailyReport(config) {
  const {
    emptyForm,
    emptyTargetForm,
    draftKey,
    fetchDashboard,
    mapForm,
    draftDate,
    isFormEmpty,
    fieldMap,
    submitReport,
    setTargetApi,
    targetMonth,
    extraSubmitFields,
    onLoadExtra,
    addNoteApi,
    loadDeps = [],
  } = config;
  const configRef = useRef(config);
  configRef.current = config;

  const [data, setData] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [attachment, setAttachment] = useState(null);
  const [error, setError] = useState('');
  const [refreshError, setRefreshError] = useState('');
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [targetForm, setTargetForm] = useState(emptyTargetForm);
  const [targetSaving, setTargetSaving] = useState(false);
  const [targetError, setTargetError] = useState('');
  const [noteText, setNoteText] = useState('');
  const [noteSubmitting, setNoteSubmitting] = useState(false);
  const [noteError, setNoteError] = useState('');
  const loadedRef = useRef(false);
  const [draftRestored, setDraftRestored] = useState(false);

  const load = useCallback(async () => {
    const cfg = configRef.current;
    setRefreshing(true);
    try {
      const payload = await cfg.fetchDashboard();
      setData(payload);
      setTargetForm(cfg.emptyTargetForm);
      setForm(cfg.mapForm(payload));
      const draft = loadDraft(cfg.draftKey);
      if (draft && draft._report_date === cfg.draftDate(payload)) {
        setForm((current) => {
          if (!cfg.isFormEmpty(current)) return current;
          setDraftRestored(true);
          return { ...cfg.emptyForm, ...draft };
        });
      }
      cfg.onLoadExtra?.(payload);
      loadedRef.current = true;
      setUpdatedAt(new Date());
      return payload;
    } finally {
      setRefreshing(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load().catch((err) => {
      setRefreshing(false);
      setError(err instanceof ApiError ? err.message : 'Failed to load dashboard');
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, loadDeps);

  // F-m2: debounced draft autosave — one localStorage write ~400 ms after
  // the user stops typing, not one per keystroke.
  useDebouncedEffect(() => {
    if (!loadedRef.current) return;
    saveDraft(draftKey, { ...form, _report_date: data ? draftDate(data) : localTodayISO() });
  }, [form, data]);

  function updateField(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function updateTargetField(key, value) {
    setTargetForm((f) => ({ ...f, [key]: value }));
  }

  function handleAttachmentChange(e, validateAttachment) {
    const file = e.target.files?.[0] || null;
    const validationError = validateAttachment ? validateAttachment(file) : null;
    if (validationError) {
      setError(validationError);
      e.target.value = '';
      setAttachment(null);
      return;
    }
    setError('');
    setAttachment(file);
  }

  /** Reload after a successful mutation, without letting a reload failure
   * masquerade as a mutation failure (F-m3). */
  async function reloadAfterSave() {
    try {
      await load();
    } catch (err) {
      setRefreshError(err instanceof ApiError ? err.message : 'Saved, but the page could not be refreshed — reload to see the latest data.');
    }
  }

  async function handleSetTarget(e, { targetMonthOverride } = {}) {
    e.preventDefault();
    setTargetError('');
    const { monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, monthly_max_payable } = targetForm;
    if ([monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, monthly_max_payable].some((v) => v === '')) {
      setTargetError('All four target fields are required.');
      return;
    }
    setTargetSaving(true);
    try {
      await configRef.current.setTargetApi({
        target_month: targetMonthOverride ?? configRef.current.targetMonth(data),
        monthly_revenue_target: Number(monthly_revenue_target),
        monthly_cash_flow_target: Number(monthly_cash_flow_target),
        monthly_max_receivable: Number(monthly_max_receivable),
        monthly_max_payable: Number(monthly_max_payable),
      });
      await reloadAfterSave();
    } catch (err) {
      setTargetError(err instanceof ApiError ? err.message : 'Failed to save monthly target');
    } finally {
      setTargetSaving(false);
    }
  }

  function serializeForm() {
    const cfg = configRef.current;
    const formData = new FormData();
    for (const [formKey, fieldName] of Object.entries(cfg.fieldMap)) {
      const value = form[formKey];
      formData.append(fieldName, typeof value === 'boolean' ? (value ? 'true' : 'false') : (value ?? ''));
    }
    cfg.extraSubmitFields?.(formData, data);
    if (attachment) formData.append('attachment', attachment);
    return formData;
  }

  async function handleSubmit(e, { validate } = {}) {
    if (e) e.preventDefault();
    setError('');
    setRefreshError('');
    if (validate) {
      const validationError = validate(form);
      if (validationError) {
        setError(validationError);
        return;
      }
    }
    setSubmitting(true);
    try {
      await configRef.current.submitReport(serializeForm());
      setSuccess(true);
      clearDraft(draftKey);
      await reloadAfterSave();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to submit report');
    } finally {
      setSubmitting(false);
      setRefreshing(false);
    }
  }

  async function handleAddNote(e) {
    e.preventDefault();
    setNoteError('');
    if (!noteText.trim()) {
      setNoteError('Note cannot be empty.');
      return;
    }
    setNoteSubmitting(true);
    try {
      await configRef.current.addNoteApi(noteText.trim());
      setNoteText('');
      await reloadAfterSave();
    } catch (err) {
      setNoteError(err instanceof ApiError ? err.message : 'Failed to save note');
    } finally {
      setNoteSubmitting(false);
    }
  }

  return {
    data, form, setForm, attachment, error, setError, refreshError, success, setSuccess,
    submitting, updatedAt, refreshing, loadedRef, draftRestored,
    targetForm, targetSaving, targetError, setTargetError,
    noteText, setNoteText, noteSubmitting, noteError,
    load, updateField, updateTargetField, handleAttachmentChange,
    handleSetTarget, handleSubmit, handleAddNote, serializeForm,
  };
}

/** Build a FormData serialiser for a field map without a hook — used by
 * the vitest suite and by pages that serialise outside the hook. */
export function buildReportFormData(form, fieldMap, extra) {
  const formData = new FormData();
  for (const [formKey, fieldName] of Object.entries(fieldMap)) {
    const value = form[formKey];
    formData.append(fieldName, typeof value === 'boolean' ? (value ? 'true' : 'false') : (value ?? ''));
  }
  extra?.(formData);
  return formData;
}
