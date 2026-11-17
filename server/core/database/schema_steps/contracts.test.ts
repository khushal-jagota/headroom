import assert from 'node:assert/strict';
import { it } from 'vitest';
import { select_schema_steps, validate_schema_step_chain } from './contracts.ts';
import type { SchemaStep } from './contracts.ts';

const step = (revision: string, previous_revision: string): SchemaStep => ({
  revision,
  previous_revision,
  apply() {},
});

it('chain selects the tail; refuses unknown or broken chains', () => {
  // The wrong steps would run on a real database. Pure chain rules only.
  const one = step('ts_1', 'base'),
    two = step('ts_2', 'ts_1'),
    chain = [one, two];
  assert.deepEqual(select_schema_steps(chain, 'ts_2', 'ts_2'), []);
  assert.deepEqual(select_schema_steps(chain, 'ts_1', 'ts_2'), [two]);
  assert.deepEqual(select_schema_steps(chain, 'base', 'ts_2'), [one, two]);
  assert.deepEqual(select_schema_steps([], 'base', 'base'), []);
  assert.throws(() => select_schema_steps(chain, 'older_python', 'ts_2'), /not on this release/);
  assert.throws(() => select_schema_steps([], 'older_python', 'base'), /not on this release/);
  assert.throws(() => select_schema_steps(chain, 'ts_2', 'ts_3'), /does not end at ts_3/);

  validate_schema_step_chain(chain, 'base');
  validate_schema_step_chain([], 'base');
  assert.throws(() => validate_schema_step_chain([two, one], 'base'), /follows ts_1, not base/);
  assert.throws(() => validate_schema_step_chain([one, two], 'other'), /follows base, not other/);
  assert.throws(
    () => validate_schema_step_chain([one, step('ts_1', 'ts_1')], 'base'),
    /not unique/,
  );
  assert.throws(() => validate_schema_step_chain([step('base', 'base')], 'base'), /not unique/);
});
