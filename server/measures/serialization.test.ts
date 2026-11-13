import { expect, test, vi } from 'vitest';
import { measure_json } from './serialization.ts';
import type { Measure } from './contracts.ts';

// Column drift previously vanished from fixed response projections. One pure record check
// proves production retention and the test-mode tripwire without another server fixture.
test('new Measure fields survive production and fail loudly in test mode', () => {
  const record: Measure & { future_column: null } = {
    id: 'measure_order',
    project_id: 'project_other',
    name: 'Count',
    unit: 'events',
    origin: 'manual',
    kind: 'reading',
    timezone: 'UTC',
    period: null,
    source_id: null,
    formula: null,
    definition_revision: 1,
    created_at: 1,
    updated_at: 1,
    write_revision: 0,
    future_column: null,
  };
  try {
    vi.stubEnv('PLAN_TEST_MODE', '0');
    expect(measure_json(record)).toEqual(record);
    expect(Object.keys(measure_json(record)).at(-1)).toBe('future_column');
    vi.stubEnv('PLAN_TEST_MODE', '1');
    expect(() => measure_json(record)).toThrow('Unknown Measure response fields: future_column');
  } finally {
    vi.unstubAllEnvs();
  }
});
