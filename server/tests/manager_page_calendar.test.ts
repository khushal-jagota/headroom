import { expect, test } from 'vitest';
import type { ReadSql } from '../core/database/contracts.ts';
import { page_readings } from '../managers/pages.ts';

// The fixed-date HTTP comparison cannot expose a RealClock DST boundary.
// No database or server is needed: this seam consumes one saved plain page row.
const plainPage: ReadSql = {
  get<Row>() {
    return { page: null, page_revision: 2 } as Row;
  },
  all<Row>() {
    return [] as Row[];
  },
};
test('plain page refresh uses the real calendar across DST and retains fixed test offsets', () => {
  const previous = process.env.TZ;
  process.env.TZ = 'Europe/Berlin';
  try {
    const now = new Date('2026-03-29T00:30:00+01:00');
    const real = page_readings(plainPage, null, now);
    expect(real.status_date).toBe('2026-03-29');
    expect(real.next_refresh_at).toBe(Date.parse('2026-03-30T00:00:00+02:00') / 1000);
    const fixed = page_readings(plainPage, null, now, '2026-03-29T00:30:00+01:00');
    expect(fixed.next_refresh_at).toBe(Date.parse('2026-03-30T00:00:00+01:00') / 1000);
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});
