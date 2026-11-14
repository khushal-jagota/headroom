import { expect, test } from 'vitest';
import type { BackendEvent } from '../../wire.generated.ts';
import { MacLiveSender } from '../transport/live.ts';

test('live delivery keeps each conversation independent and drops the oldest waiting events and failed batches', async () => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const calls: [string, string[]][] = [];
  const sender = new MacLiveSender({
    async post_live(id, batch) {
      calls.push([id, batch.events.map((event) => String(event.arguments.text_delta))]);
      if (calls.length === 1) {
        await held;
        throw new Error('offline');
      }
    },
  });
  function event(text: string, conversation_id = 'a'): BackendEvent {
    return {
      child: { conversation_id, child_id: 'child', executor_incarnation: 'inc' },
      turn: null,
      method: 'agent_message_delta',
      arguments: { text_delta: text },
    };
  }
  sender.send(event('first'));
  await Promise.resolve();
  for (let index = 0; index < 65; index++) sender.send(event(String(index)));
  sender.send(event('independent', 'b'));
  await Promise.resolve();
  expect(calls).toEqual([
    ['a', ['first']],
    ['b', ['independent']],
  ]);
  release();
  await sender.settled();
  expect(calls).toEqual([
    ['a', ['first']],
    ['b', ['independent']],
    ['a', Array.from({ length: 64 }, (_, index) => String(index + 1))],
  ]);
  await sender.stop({ deadline: performance.now() / 1000 + 5 });
  sender.send(event('after stop'));
  expect(calls).toHaveLength(3);
});
