// Unit tests for the shared quadrant helpers (F-M5: money lives here as a
// re-export of the canonical utils/format.js implementation).
import { describe, expect, it } from 'vitest';
import { buildQuadrantMetrics, money, formatVariance, STATUS_MAP } from './quadrantMetrics';

describe('money re-export', () => {
  it('matches the canonical rule', () => {
    expect(money(null)).toBe('Ks 0');
    expect(money(2500)).toBe('Ks 2,500');
  });
});

describe('STATUS_MAP', () => {
  it('maps the backend vocabulary to the quadrant vocabulary', () => {
    expect(STATUS_MAP).toEqual({ green: 'good', yellow: 'warning', red: 'critical', grey: 'unknown', unknown: 'unknown' });
  });
});

describe('buildQuadrantMetrics', () => {
  const base = {
    financial_status: 'green', financial_reason: 'On track',
    cumulative_net_revenue: 1000000, mtd_revenue: 800000, mtd_other_amount: 200000,
    achievement_pct: 80, variance_amount: -50000, budget_status: 'green',
    social_status: 'yellow', social_reason: 'Few events',
    activity_status: 'red', activity_reason: 'No submissions',
    health_status: 'grey', health_reason: null,
    event_flag: true, meeting_flag: false, golf_flag: null,
    monthly_event_count: 2, monthly_meeting_count: 1, monthly_golf_count: 0,
    gym_percent: 50, swimming_percent: null, cycle_percent: 70,
    daily_steps: 8000, heart_rate: 72, sleep_hours: 7, bp_systolic: 120, bp_diastolic: 80,
  };

  it('maps statuses and formats money values', () => {
    const m = buildQuadrantMetrics(base);
    expect(m.financial.status).toBe('good');
    expect(m.financial.displayValue).toBe('Ks 1,000,000');
    expect(m.social.status).toBe('warning');
    expect(m.activity.status).toBe('critical');
    expect(m.health.status).toBe('unknown');
  });
  it('averages only the present activity percents', () => {
    const m = buildQuadrantMetrics(base);
    expect(m.activity.value).toBe(60); // (50 + 70) / 2
  });
  it('counts engaged social flags', () => {
    const m = buildQuadrantMetrics(base);
    expect(m.social.displayValue).toBe('1/3 Engaged');
  });
});

describe('formatVariance', () => {
  it('signs the variance', () => {
    expect(formatVariance(5000)).toBe('+Ks 5,000');
    expect(formatVariance(-5000)).toBe('−Ks 5,000');
    expect(formatVariance(0)).toBe('Ks 0');
    expect(formatVariance(null)).toBe('—');
  });
});
