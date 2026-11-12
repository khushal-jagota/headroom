import { afterEach, expect, it, vi } from 'vitest';
import { ClaudeReceipts } from './receipts.ts';
import { cancellation_uuid_set } from './wire.ts';

afterEach(() => vi.useRealTimers());

it('retains exact admission when a result beats the writer and distinguishes conflicting refusal', async () => {
  const receipts = new ClaudeReceipts();
  receipts.watch_user_message('accepted');
  receipts.observe({ type: 'result', uuid: 'result', user_message_uuids: ['accepted', 9] });
  expect([...receipts.user_message_uuids_for_result('result')]).toEqual(['accepted']);
  expect(await receipts.wait_for_user_message_admission('accepted')).toBe(true);
  receipts.watch_user_message('conflict');
  receipts.observe({ type: 'command_lifecycle', command_uuid: 'conflict', state: 'refused' });
  receipts.observe({ type: 'command_lifecycle', command_uuid: 'conflict', state: 'started' });
  expect(await receipts.wait_for_user_message_admission('conflict')).toBe(null);
});

it('bounds a folded steer but waits beyond the bound when a queued steer starts during the wait', async () => {
  vi.useFakeTimers();
  const receipts = new ClaudeReceipts();
  receipts.watch_user_message('folded');
  receipts.observe({ type: 'command_lifecycle', command_uuid: 'folded', state: 'started' });
  const folded = receipts.wait_for_user_message_settlement('folded');
  await vi.advanceTimersByTimeAsync(15_000);
  await folded;
  receipts.watch_user_message('queued');
  receipts.observe({ type: 'command_lifecycle', command_uuid: 'queued', state: 'queued' });
  let finished = false;
  const queued = receipts.wait_for_user_message_settlement('queued').then(() => {
    finished = true;
  });
  await vi.advanceTimersByTimeAsync(1_000);
  receipts.observe({ type: 'command_lifecycle', command_uuid: 'queued', state: 'started' });
  await vi.advanceTimersByTimeAsync(15_000);
  expect(finished).toBe(false);
  receipts.observe({ type: 'command_lifecycle', command_uuid: 'queued', state: 'completed' });
  await queued;
});

it('does not turn malformed queue cancellation members into proof of an empty queue', () => {
  expect(() => cancellation_uuid_set([9])).toThrow(
    'claude did not confirm queued command cancellation',
  );
  expect(() => cancellation_uuid_set(undefined)).toThrow(
    'claude did not confirm queued command cancellation',
  );
  expect([...cancellation_uuid_set([])]).toEqual([]);
});
