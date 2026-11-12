/** Gap: observer cancellation and new-agent cleanup have different durable effects.
 * Reuse the Ticket SQLite fixture and existing fake. No provider or network host.
 */
import { dirname } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { fixture, fixture_path, create, claim, read, now } from './ticket_fixture.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import { text_message_content } from '../conversation/message_content.ts';
import {
  configure_conversation_start,
  agent_resolve,
  send_to_agent_conversation,
  send_to_ticket_conversation,
  start_agent_conversation,
  read_agent_conversation,
} from '../runtime/conversation_start.ts';
afterEach(() => vi.restoreAllMocks());
function setup() {
  const db = fixture();
  configure_conversation_start(db, {
    db_path: fixture_path(db),
    workspace_root: dirname(fixture_path(db)),
  });
  const system = new InMemoryConversationSystem();
  const controller = new AbortController();
  let entered!: () => void;
  const sending = new Promise<void>((resolve) => {
    entered = resolve;
  });
  vi.spyOn(system, 'send').mockImplementation(() => {
    entered();
    return new Promise(() => {});
  });
  return { db, system, controller, sending };
}
test.each(['chief', 'manager_synthetic'])(
  'abort of a new %s send kills then unlinks only that conversation',
  async (key) => {
    const { db, system, controller, sending } = setup();
    const kill = system.kill.bind(system);
    vi.spyOn(system, 'kill').mockImplementation(async (id) => {
      expect(read_agent_conversation(db, key)).toBe(id);
      await kill(id);
    });
    const task = send_to_agent_conversation(
      system,
      db,
      key,
      text_message_content('opener'),
      agent_resolve(db),
      {
        conversation_id: null,
        created_conversation_id: 'new_agent',
        sender_label: 'test',
        signal: controller.signal,
      },
    );
    await sending;
    controller.abort();
    await expect(task).rejects.toMatchObject({ name: 'AbortError' });
    expect(system.kill).toHaveBeenCalledWith('new_agent');
    expect(read_agent_conversation(db, key)).toBeNull();
  },
);
test('abort of an existing agent send stops its observer and preserves the link', async () => {
  const { db, system, controller, sending } = setup();
  await start_agent_conversation(system, db, 'chief', agent_resolve(db), {
    conversation_id: 'existing_agent',
  });
  const kill = vi.spyOn(system, 'kill');
  const task = send_to_agent_conversation(
    system,
    db,
    'chief',
    text_message_content('message'),
    null,
    {
      conversation_id: 'existing_agent',
      sender_label: 'test',
      signal: controller.signal,
    },
  );
  await sending;
  controller.abort();
  await expect(task).rejects.toMatchObject({ name: 'AbortError' });
  expect(kill).not.toHaveBeenCalled();
  expect(read_agent_conversation(db, 'chief')).toBe('existing_agent');
});
test('abort of a new Ticket send preserves its source, link, and outstanding claim', async () => {
  const { db, system, controller, sending } = setup();
  const ticket = create(db);
  claim(db, ticket.id);
  const kill = vi.spyOn(system, 'kill');
  const task = send_to_ticket_conversation(system, db, ticket.id, text_message_content('opener'), {
    conversation_id: null,
    created_conversation_id: 'new_ticket',
    sender_label: 'test',
    now,
    signal: controller.signal,
  });
  await sending;
  controller.abort();
  await expect(task).rejects.toMatchObject({ name: 'AbortError' });
  expect(kill).not.toHaveBeenCalled();
  expect(read(db, ticket.id)).toMatchObject({
    conversation_id: 'new_ticket',
    worker_step_claim: 'out',
  });
});
