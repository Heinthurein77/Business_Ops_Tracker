// Unit tests for the daily-report FormData serialiser (F-M1): the form is
// serialised by iterating a field map instead of hand-written appends.
import { describe, expect, it } from 'vitest';
import { buildReportFormData } from './useDailyReport';

const FIELD_MAP = {
  revenue: 'revenue',
  cash_flow: 'cash_flow',
  is_sick: 'is_sick',
  gym_percent: 'health_gym_percent',
  event_flag: 'event_flag',
};

describe('buildReportFormData', () => {
  it('appends every mapped field in order', () => {
    const fd = buildReportFormData(
      { revenue: '100', cash_flow: '', is_sick: false, gym_percent: '50', event_flag: true },
      FIELD_MAP,
    );
    expect([...fd.keys()]).toEqual(['revenue', 'cash_flow', 'is_sick', 'health_gym_percent', 'event_flag']);
    expect(fd.get('revenue')).toBe('100');
    expect(fd.get('cash_flow')).toBe('');
  });
  it('serialises booleans as true/false strings', () => {
    const fd = buildReportFormData({ is_sick: true, event_flag: false }, { is_sick: 'is_sick', event_flag: 'event_flag' });
    expect(fd.get('is_sick')).toBe('true');
    expect(fd.get('event_flag')).toBe('false');
  });
  it('runs the extra-fields hook (e.g. report_date for a past day)', () => {
    const fd = buildReportFormData({ revenue: '1' }, { revenue: 'revenue' }, (formData) => {
      formData.append('report_date', '2026-11-10');
    });
    expect(fd.get('report_date')).toBe('2026-11-10');
  });
});
