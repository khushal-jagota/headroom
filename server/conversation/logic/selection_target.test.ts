/** Risk: a target that drops an HTTP effort change, which arrives without `reasoning_effort_is_set`.
 * Gap: the rule was inline in delivery before. Layer: pure.
 */
import { expect, it } from 'vitest';
import { selection_target } from './selection_target.ts';

const RECORD = { model: 'recorded-model', reasoning_effort: 'medium' };

it.each([
  [
    'an HTTP effort change without is_set',
    { model_change: null, reasoning_effort_change: 'high', reasoning_effort_is_set: false },
    { model: 'recorded-model', reasoning_effort: 'high' },
  ],
  [
    'an effort set to the model default',
    { model_change: null, reasoning_effort_change: null, reasoning_effort_is_set: true },
    { model: 'recorded-model', reasoning_effort: null },
  ],
  [
    'no change',
    { model_change: null, reasoning_effort_change: null, reasoning_effort_is_set: false },
    { model: 'recorded-model', reasoning_effort: 'medium' },
  ],
])('%s', (_name, message, target) => {
  expect(selection_target(message, RECORD)).toEqual(target);
});
