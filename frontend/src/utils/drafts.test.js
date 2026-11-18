// Unit tests for draft persistence (F-m2). localStorage is stubbed — the
// module only touches it inside try/catch, so these run in node.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { saveDraft, loadDraft, clearDraft, draftHasContent } from './drafts';

function installLocalStorage() {
  const store = new Map();
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
}

describe('drafts', () => {
  beforeEach(() => installLocalStorage());

  it('round-trips a draft', () => {
    saveDraft('ceo-daily-report', { revenue: '100', _report_date: '2026-11-18' });
    expect(loadDraft('ceo-daily-report')).toEqual({ revenue: '100', _report_date: '2026-11-18' });
  });
  it('returns null for a missing draft', () => {
    expect(loadDraft('nope')).toBeNull();
  });
  it('clears a draft', () => {
    saveDraft('x', { a: 1 });
    clearDraft('x');
    expect(loadDraft('x')).toBeNull();
  });
  it('namespaces keys per form', () => {
    saveDraft('a', { a: 1 });
    saveDraft('b', { b: 2 });
    expect(loadDraft('a')).toEqual({ a: 1 });
    expect(loadDraft('b')).toEqual({ b: 2 });
  });
  it('draftHasContent ignores empty forms', () => {
    expect(draftHasContent({ revenue: '', cash_flow: '' })).toBe(false);
    expect(draftHasContent({ revenue: '5' })).toBe(true);
    expect(draftHasContent(null)).toBe(false);
  });
  it('survives a throwing storage (private mode / quota)', () => {
    globalThis.localStorage = {
      getItem: () => { throw new Error('denied'); },
      setItem: () => { throw new Error('denied'); },
      removeItem: () => { throw new Error('denied'); },
    };
    expect(() => saveDraft('x', { a: 1 })).not.toThrow();
    expect(loadDraft('x')).toBeNull();
    expect(() => clearDraft('x')).not.toThrow();
  });
});
