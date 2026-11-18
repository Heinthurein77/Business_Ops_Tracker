// Unit tests for the API client (F-M4): query encoding, PATCH/DELETE
// helpers going through the single request path, and the request timeout.
// fetch is stubbed; no network is touched.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, buildQuery } from './client';

describe('buildQuery', () => {
  it('encodes values', () => {
    expect(buildQuery({ business_unit_id: 3, month: '2026-11' })).toBe('?business_unit_id=3&month=2026-11');
    expect(buildQuery({ q: 'a b&c' })).toBe('?q=a+b%26c');
  });
  it('drops empty values', () => {
    expect(buildQuery({ a: undefined, b: null, c: '' })).toBe('');
    expect(buildQuery({})).toBe('');
  });
});

describe('api methods through the shared request path', () => {
  let fetchMock;
  let lastInit;

  function jsonResponse(body, status = 200) {
    return {
      status,
      ok: status >= 200 && status < 300,
      headers: { get: () => 'application/json' },
      json: async () => body,
    };
  }

  beforeEach(() => {
    lastInit = null;
    fetchMock = vi.fn(async (url, init) => {
      lastInit = init;
      return jsonResponse({ ok: true });
    });
    globalThis.fetch = fetchMock;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('encodes query params in ceoActivityCalendar', async () => {
    await api.ceoActivityCalendar(7, '2026-11');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/ceo-activity-calendar?business_unit_id=7&month=2026-11',
      expect.anything(),
    );
  });

  it('encodes the forecast business_unit_id', async () => {
    await api.forecast('12');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/ops/forecast?business_unit_id=12');
  });

  it('updateActionItem uses PATCH through the shared path', async () => {
    await api.updateActionItem(5, { status: 'done' });
    expect(lastInit.method).toBe('PATCH');
    expect(lastInit.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(lastInit.body)).toEqual({ status: 'done' });
  });

  it('deleteActionItem and deleteDocument use DELETE', async () => {
    await api.deleteActionItem(5);
    expect(lastInit.method).toBe('DELETE');
    await api.deleteDocument(9);
    expect(lastInit.method).toBe('DELETE');
  });

  it('every request carries a timeout signal', async () => {
    await api.me();
    expect(lastInit.signal).toBeInstanceOf(AbortSignal);
    expect(lastInit.signal.aborted).toBe(false);
  });

  it('createActionItem sends an Idempotency-Key', async () => {
    await api.createActionItem({ title: 'x' });
    expect(lastInit.headers['Idempotency-Key']).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
  });

  it('normalises a 422 detail list into one message', async () => {
    fetchMock.mockResolvedValueOnce({
      status: 422,
      ok: false,
      headers: { get: () => 'application/json' },
      json: async () => ({ detail: [{ msg: 'Value error, bad field' }, { msg: 'second problem' }] }),
    });
    await expect(api.me()).rejects.toThrow('bad field. second problem.');
  });
});
