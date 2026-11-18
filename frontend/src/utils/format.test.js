// Unit tests for the canonical formatters (F-M5, F-m4). Run with `npm test`.
import { describe, expect, it } from 'vitest';
import { money, formatDate, formatDateLong, localTodayISO, localMonth, parseLocalDate } from './format';

describe('money — canonical rule: null/undefined/non-numeric -> Ks 0, rounded', () => {
  it('formats a plain value', () => {
    expect(money(1234567)).toBe('Ks 1,234,567');
  });
  it('rounds', () => {
    expect(money(1234.56)).toBe('Ks 1,235');
  });
  it('renders missing values as Ks 0 (never Ks NaN)', () => {
    expect(money(null)).toBe('Ks 0');
    expect(money(undefined)).toBe('Ks 0');
    expect(money(NaN)).toBe('Ks 0');
  });
  it('accepts numeric strings', () => {
    expect(money('5000')).toBe('Ks 5,000');
  });
});

describe('local dates — local date parts, never UTC (F-m4)', () => {
  it('localTodayISO uses local parts', () => {
    // 02:00 local on 1 Jan: UTC would still be 31 Dec the previous year.
    expect(localTodayISO(new Date(2026, 0, 1, 2, 0, 0))).toBe('2026-01-01');
    expect(localTodayISO(new Date(2026, 10, 30, 23, 59, 0))).toBe('2026-11-30');
  });
  it('localMonth derives from the local day', () => {
    expect(localMonth(new Date(2026, 0, 1, 2, 0, 0))).toBe('2026-01');
  });
  it('parseLocalDate does not shift a YYYY-MM-DD across midnight UTC', () => {
    const d = parseLocalDate('2026-11-18');
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(10);
    expect(d.getDate()).toBe(18);
  });
});

describe('formatDate', () => {
  it('formats an ISO date short', () => {
    expect(formatDate('2026-11-18')).toBe('18 Nov 2026');
  });
  it('renders missing input as —', () => {
    expect(formatDate(null)).toBe('—');
    expect(formatDate('')).toBe('—');
  });
  it('formats long', () => {
    expect(formatDateLong('2026-11-18')).toBe('Wednesday, 18 November 2026');
  });
});
