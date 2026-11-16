/** Shutdown must retain HTTP-owned SQLite BUSY retries without runtime adoption. */
import { expect, test, vi } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ConversationStartRequest } from '../conversation/contracts.ts';
import { resolve_conversation_start_request } from '../conversation/logic/conversation_start_resolution.ts';
import { ToolCallStartedEventPayload } from '../conversation/events.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';

// Distinct gap: core-owned receipt tests cannot prove the HTTP scope joins a BUSY owner-read.
test('sealed HTTP scope drains a real owner-read after BUSY and propagates semantic failures once', async () => {
  const store = new ConversationStore(fixture(), { integer_now: () => 100 });
  store.create_conversation(
    resolve_conversation_start_request(
      new ConversationStartRequest({
        conversation_id: 'http',
        model: 'model',
        workspace_folder: '/workspace',
      }),
    ),
  );
  store.append_events('http', [
    new ToolCallStartedEventPayload({
      tool_call_id: 'read',
      title: 'Read',
      tool_kind: 'read',
      detail: null,
    }),
  ]);
  const scope = new ConversationHttpWriteScope();
  const real = store.advance_owner_read_through_sequence.bind(store);
  let attempts = 0;
  vi.spyOn(store, 'advance_owner_read_through_sequence').mockImplementation((...args) => {
    if (++attempts === 1) throw Object.assign(new Error('writer is busy'), { code: 'SQLITE_BUSY' });
    return real(...args);
  });
  const saved = scope.run(() => store.advance_owner_read_through_sequence('http', 1));
  scope.seal();
  let drained = false;
  const drain = scope.drain().then(() => {
    drained = true;
  });
  await Promise.resolve();
  expect(drained).toBe(false);
  expect(store.read_conversation('http')!.owner_read_through_sequence).toBe(0);
  expect(() =>
    scope.run(() => {
      throw new Error('must not begin');
    }),
  ).toThrow('the conversation system is unavailable');
  expect((await saved)!.owner_read_through_sequence).toBe(1);
  await drain;
  expect(attempts).toBe(2);
  expect(drained).toBe(true);
  const semantic = new Error('rejected domain transition');
  let rejected = 0;
  const other = new ConversationHttpWriteScope();
  await expect(
    other.run(() => {
      rejected++;
      throw semantic;
    }),
  ).rejects.toBe(semantic);
  other.seal();
  await other.drain();
  expect(rejected).toBe(1);
  vi.restoreAllMocks();
});
