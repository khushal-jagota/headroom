/** Shutdown races require an exact pause between the committed action and dispatch. */
import { expect, test, vi } from 'vitest';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import { ConversationRoleMaterials, ConversationStartRequest } from '../conversation/contracts.ts';
import { Deferred } from '../conversation/runtime/state.ts';
import { CredentialRequestOperations } from '../credentials/requests/service.ts';
import { createCredentialRequestRoutes } from '../credentials/requests/routes.ts';
import { validate_request } from '../credentials/requests/logic/validation.ts';
import { setup } from './conversation_core_fixture.ts';
import { create } from './ticket_fixture.ts';

const metadata = (request_id: string) => ({
  request_id,
  name: 'Synthetic shutdown key',
  provider: 'Synthetic service',
  use: 'Resume the authorized synthetic work',
  auth_kind: 'bearer',
  header_name: null,
  help: null,
  help_url: null,
  replacement: null,
});

// Ordinary HTTP comparisons cannot seal the shared write scope at a pending dispatch.
// Reuse the real core and record fixture without starting a child or listening host.
test('a sealed shared write scope refuses late requests and drains the admitted action through dispatch', async () => {
  const f = setup();
  const ticket = create(f.database);
  const conversation_id = 'credential-shutdown';
  await f.core.start_conversation(new ConversationStartRequest({
    conversation_id,
    model: 'model',
    workspace_folder: '/workspace',
    role_materials: new ConversationRoleMaterials({
      role_text: 'Synthetic employee credential proof.',
      identity_environment_variables: [
        ['PLAN_ACTOR', 'worker'],
        ['PLAN_TICKET_ID', ticket.id],
      ],
    }),
  }));
  f.database.write((tx) => {
    tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', [conversation_id, ticket.id]);
    tx.run('INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)', [conversation_id, ticket.id]);
  });
  const writes = new ConversationHttpWriteScope();
  const operations = new CredentialRequestOperations({
    database: f.database,
    core: f.core,
    writes,
    key_path: f.database_path + '.credential-key',
    now: () => 100,
  });
  const entered = new Deferred<void>();
  const released = new Deferred<void>();
  const dispatch = operations.dispatch.bind(operations);
  const blocked = vi.spyOn(operations, 'dispatch').mockImplementation(async (...args) => {
    entered.resolve();
    await released.promise;
    return await dispatch(...args);
  });
  const app = await createApp(
    loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: f.database_path }),
    { database: f.database, areas: [{ prefix: '/api', routes: createCredentialRequestRoutes(operations) }] },
  );
  const headers = {
    'x-plan-actor': 'worker',
    'x-plan-ticket-id': ticket.id,
    'x-plan-conversation-id': conversation_id,
  };
  const admitted = app.inject({
    method: 'POST',
    url: '/api/credentials/requests',
    headers,
    payload: metadata('19000000-0000-4000-8000-000000000010'),
  }).then((response) => response);
  try {
    await entered.promise;
    writes.seal();
    let drained = false;
    const drain = writes.drain().then(() => { drained = true; });
    const late = await app.inject({
      method: 'POST',
      url: '/api/credentials/requests',
      headers,
      payload: metadata('19000000-0000-4000-8000-000000000011'),
    });
    expect(late.statusCode).toBe(503);
    expect(late.json()).toEqual({ detail: 'the conversation system is unavailable' });
    expect(f.database.read((sql) => sql.get<{ count: number }>('SELECT count(*) AS count FROM credential_requests'))?.count).toBe(1);
    expect(blocked).toHaveBeenCalledTimes(1);
    expect(drained).toBe(false);
    released.resolve();
    expect((await admitted).statusCode).toBe(200);
    await drain;
    await operations.finish_operations();
    expect(drained).toBe(true);
  } finally {
    released.resolve();
    await admitted.catch(() => {});
    await operations.finish_operations();
    await app.close();
    await f.core.shutdown();
  }
});

// The Manager accepts narrower TS UUID parsing. Pin that one intentional boundary.
test('credential metadata refuses a hexadecimal UUID prefix', () => {
  expect(() => validate_request(metadata('0x00000000000040008000000000000001')))
    .toThrow('request_id must be a UUID');
});
