import { expect, test, vi } from 'vitest';
import { ordered_record } from './ordered_record.ts';

// Populated route comparisons cover known fields. This pure check catches new fields
// that fixed projections previously dropped, without another server fixture.
test('unknown record fields survive production and fail loudly in test mode', () => {
  const record = { known: 1, future_column: null };
  try {
    vi.stubEnv('PLAN_TEST_MODE', '0');
    expect(ordered_record(record, ['known'], 'Measure')).toEqual(record);
    vi.stubEnv('PLAN_TEST_MODE', '1');
    expect(() => ordered_record(record, ['known'], 'Measure')).toThrow(
      'Unknown Measure response fields: future_column',
    );
  } finally {
    vi.unstubAllEnvs();
  }
});
