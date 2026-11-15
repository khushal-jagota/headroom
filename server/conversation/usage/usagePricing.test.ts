import { expect, test } from 'vitest';
import {
  cacheSavingsUsd,
  createOverrideRateTable,
  lookupRate,
  parseRateTable,
  priceUsage,
} from './usagePricing.ts';
import { EMPTY_TOTALS } from './usageTranscripts.ts';

const rates = parseRateTable({
  'provider/test-model': {
    input_cost_per_token: 1,
    output_cost_per_token: 2,
    cache_read_input_token_cost: 0.1,
    cache_creation_input_token_cost: 3,
    input_cost_per_token_above_272k_tokens: 4,
    output_cost_per_token_above_272k_tokens: 5,
    cache_read_input_token_cost_above_272k_tokens: 0.2,
    cache_creation_input_token_cost_above_272k_tokens: 6,
    cache_creation_input_token_cost_above_1hr: 7,
  },
});

// Gap: base-rate comparisons do not prove the strict per-request context boundary.
test('prompt categories select the long-context rates only above 272000 tokens', () => {
  const boundary = {
    ...EMPTY_TOTALS,
    uncachedInputTokens: 271_980,
    cachedInputTokens: 10,
    cacheCreationTokens: 10,
    outputTokens: 2,
  };
  expect(priceUsage(rates, 'test-model', boundary, null).costUsd).toBe(272_015);
  const above = { ...boundary, cacheCreationTokens: 11 };
  expect(priceUsage(rates, 'test-model', above, null).costUsd).toBe(1_087_998);
  expect(cacheSavingsUsd(rates, 'test-model', above)).toBe(38);
  const partial = parseRateTable({
    model: {
      input_cost_per_token: 1,
      output_cost_per_token: 2,
      input_cost_per_token_above_272k_tokens: 4,
    },
  });
  expect(priceUsage(partial, 'model', above, null).costUsd).toBe(1_087_945);
});

// Gap: flat cache-write rates do not prove duration splitting or override precedence.
test('one-hour cache writes split existing totals and retain provider and override precedence', () => {
  const totals = { ...EMPTY_TOTALS, cacheCreationTokens: 10 };
  expect(priceUsage(rates, 'test-model', totals, null, undefined, 4).costUsd).toBe(46);
  expect(priceUsage(rates, 'test-model', totals, null, undefined, 20).costUsd).toBe(70);
  expect(priceUsage(rates, 'test-model', totals, null, undefined, -1).costUsd).toBe(30);
  expect(priceUsage(rates, 'test-model', totals, 11, undefined, 4)).toEqual({
    costUsd: 11,
    costSource: 'providerReported',
  });
  const overrides = createOverrideRateTable({
    'test-model': {
      inputCostPerMillionTokens: 1_000_000,
      outputCostPerMillionTokens: 2_000_000,
      cacheWriteCostPerMillionTokens: 3_000_000,
    },
  });
  expect(priceUsage(rates, 'test-model', totals, 11, overrides, 4)).toEqual({
    costUsd: 30,
    costSource: 'modelPriced',
  });
});

// Gap: equal base rates can conceal conflicting context and cache-duration aliases.
test('bare aliases remain unavailable when extended rates differ', () => {
  const rates = parseRateTable({
    'first/model': {
      input_cost_per_token: 1,
      output_cost_per_token: 2,
      input_cost_per_token_above_272k_tokens: 4,
    },
    'second/model': {
      input_cost_per_token: 1,
      output_cost_per_token: 2,
      input_cost_per_token_above_272k_tokens: 5,
    },
    'first/cache-model': {
      input_cost_per_token: 1,
      output_cost_per_token: 2,
      cache_creation_input_token_cost_above_1hr: 7,
    },
    'second/cache-model': {
      input_cost_per_token: 1,
      output_cost_per_token: 2,
      cache_creation_input_token_cost_above_1hr: 8,
    },
  });
  expect(lookupRate(rates, 'model')).toBeNull();
  expect(lookupRate(rates, 'cache-model')).toBeNull();
});
