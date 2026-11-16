import { reset_agent_conversation } from '../runtime/conversation_start.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
/** Risk: native restarts must rotate credentials without storing or inheriting secrets.
 * Reuse the SQLite Ticket fixture. No provider, browser, or server host is needed.
 */
import { expect, test, vi } from 'vitest';
import { fixture } from '../tests/ticket_fixture.ts';
import {
  ConversationRoleMaterials,
  ResolvedConversationStart,
  ConversationAccess,
} from '../conversation/contracts.ts';
import { ensure_started_conversation_record } from '../conversation/record/data/store.ts';
import type { BackendChild } from '../conversation/backends/contracts.ts';
import { authenticated_local_child, local_launch_start } from './launch.ts';
import { authenticate_launch_token } from './data.ts';

function setup() {
  const database = fixture();
  const start = new ResolvedConversationStart({
    conversation_id: 'private-launch',
    backend_key: 'codex',
    model: 'test',
    reasoning_effort: null,
    workspace_folder: '/synthetic',
    access: ConversationAccess.full,
    role_materials: new ConversationRoleMaterials({
      role_text: 'test',
      identity_environment_variables: [['PLAN_ACTOR', 'worker']],
    }),
  });
  database.write((tx) => {
    ensure_started_conversation_record(tx, start, { created_at: 1 });
    tx.run("INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff',?)", [
      start.conversation_id,
    ]);
  });
  return { database, start };
}

test('native restarts rotate only the transient launch and forward child methods', async () => {
  const { database, start } = setup();
  const starts: ResolvedConversationStart[] = [];
  const stop = vi.fn(async () => {});
  const child = authenticated_local_child(
    {
      start: async (value: ResolvedConversationStart) => {
        starts.push(value);
      },
      stop,
    } as unknown as BackendChild,
    database,
  );
  await child.start(start, { vendor_session_cursor: null });
  await child.start(start, { vendor_session_cursor: 'retained' });
  const token = (value: ResolvedConversationStart) =>
    new Map(value.role_materials!.identity_environment_variables).get('PLAN_LAUNCH_TOKEN')!;
  expect(token(starts[0]!)).not.toBe(token(starts[1]!));
  expect(database.read((sql) => authenticate_launch_token(sql, token(starts[0]!)))).toBeNull();
  expect(
    database.read((sql) => authenticate_launch_token(sql, token(starts[1]!)))?.principal.kind,
  ).toBe('chief');
  expect(start.role_materials!.identity_environment_variables).toEqual([['PLAN_ACTOR', 'worker']]);
  expect(
    database.read((sql) =>
      sql.get<{ identity_environment_variables: string }>(
        'SELECT identity_environment_variables FROM conversations WHERE conversation_id=?',
        [start.conversation_id],
      ),
    )!.identity_environment_variables,
  ).not.toContain('PLAN_LAUNCH_TOKEN');
  await child.stop();
  expect(stop).toHaveBeenCalledOnce();
  expect(() =>
    database.write((tx) => ensure_started_conversation_record(tx, starts[1]!, { created_at: 1 })),
  ).toThrow('launch tokens are transient');
  await reset_agent_conversation(new InMemoryConversationSystem(), database, 'chief_of_staff');
  expect(database.read((sql) => authenticate_launch_token(sql, token(starts[1]!)))).toBeNull();
  expect(database.read((sql) => sql.get('SELECT 1 FROM agent_launch_tokens'))).toBeUndefined();
});

test('an unlinked generic conversation receives the unidentified sentinel', () => {
  const { database, start } = setup();
  const previous = new Map(
    local_launch_start(database, start).role_materials!.identity_environment_variables,
  ).get('PLAN_LAUNCH_TOKEN')!;
  database.write((tx) =>
    tx.run("UPDATE agents SET conversation_id=NULL WHERE agent_key='chief_of_staff'"),
  );
  expect(database.read((sql) => authenticate_launch_token(sql, previous))).toBeNull();

  expect(
    new Map(local_launch_start(database, start).role_materials!.identity_environment_variables).get(
      'PLAN_LAUNCH_TOKEN',
    ),
  ).toBe('unidentified');
});
