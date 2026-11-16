import { expect, test } from 'vitest';
import type { FastifyRequest } from 'fastify';
import { TestClock } from '../core/clock.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import { page_readings } from '../managers/pages.ts';
import { GoalResources, configure_goal_resources } from '../goals/resource_versions.ts';

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

// Resource comparisons cover wire responses; this seam isolates the selected counter.
test('an aggregate Goal change preserves the selected saved document token', () => {
  let aggregate = 1;
  const saved = {
    id: 'selected',
    revision: 7,
    page: '{"kind":"headroom.page","version":1,"title":"Selected","summary":"","blocks":[]}',
  };
  const sql: ReadSql = {
    get<Row>(query: string) {
      return (query.includes('FROM goals WHERE') ? saved : { revision: aggregate }) as Row;
    },
    all<Row>() {
      return [] as Row[];
    },
  };
  configure_goal_resources(new TestClock('2026-11-04T12:00:00Z'));
  const provider = new GoalResources();
  const token = (key: string) => provider.capture(sql, {} as FastifyRequest, key)!.revision;
  const collection = token('/api/goals'),
    selected = token('/api/goals/selected');
  aggregate++;
  expect(token('/api/goals')).not.toBe(collection);
  expect(token('/api/goals/selected')).toBe(selected);
  saved.revision++;
  expect(token('/api/goals/selected')).not.toBe(selected);
});
