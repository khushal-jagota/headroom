/** Claude's selection rule, the only proof of it now that the adapter no longer throws. */
import { expect, it } from 'vitest';
import type { BackendSelection } from '../../core/selection.ts';
import { claude_selection_transition } from './selection.ts';

const session_capabilities = {
  sessions: { supportsModelSwitchInSession: false },
  turns: { supportsActiveSteering: true },
};
const current: BackendSelection = { model: 'haiku', reasoning_effort: 'low' };
it.each<[string, BackendSelection, BackendSelection, string]>([
  ['a model change', current, { model: 'opus', reasoning_effort: 'low' }, 'restart_session'],
  [
    'an effort change to a live level',
    current,
    { model: 'haiku', reasoning_effort: 'high' },
    'apply_on_next_turn',
  ],
  [
    'an effort change to null',
    current,
    { model: 'haiku', reasoning_effort: null },
    'restart_session',
  ],
  [
    'an effort change to a non-live level',
    current,
    { model: 'haiku', reasoning_effort: 'minimal' },
    'restart_session',
  ],
  [
    'no change',
    { model: 'haiku', reasoning_effort: null },
    { model: 'haiku', reasoning_effort: null },
    'apply_on_next_turn',
  ],
])('plans %s', (_case, from, target, type) => {
  expect(claude_selection_transition({ current: from, target, session_capabilities })).toEqual({
    type,
  });
});
