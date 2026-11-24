/** An unchanged destination must not enter the update's interruption warning. */
import { expect, test } from 'vitest';
import type { ConversationRecord } from '../record/contracts.ts';
import { ConversationState } from './state.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';
import { update_interrupted_work } from './update_preparation.ts';

test('interruption filters active and background work by the stored destination', () => {
  const changed = new ConversationState({ backend_key: 'codex' } as ConversationRecord);
  const unchanged = new ConversationState({ backend_key: 'codex' } as ConversationRecord);
  const background = new ConversationState({ backend_key: 'codex' } as ConversationRecord);
  changed.phase = 'starting';
  unchanged.phase = 'starting';
  const conversations = new Map([
    ['changed', changed],
    ['unchanged', unchanged],
    ['background', background],
  ]);
  const host = {
    conversations,
    options: {
      store: {
        read_live_background_task_ids: () => new Set(['background']),
        update_work_metadata: () =>
          new Map([
            ['changed', ['Changed agent', 'linux']],
            ['unchanged', ['Unchanged agent', 'darwin']],
            ['background', ['Background agent', 'linux']],
          ]),
      },
    },
  } as unknown as SqliteProcessConversationSystem;
  expect(update_interrupted_work(host, ['linux'])).toEqual(['Changed agent', 'Background agent']);
  expect(update_interrupted_work(host)).toEqual([
    'Changed agent',
    'Unchanged agent',
    'Background agent',
  ]);
});
