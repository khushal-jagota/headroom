/** Shutdown races require an exact pause between the committed action and dispatch. */
import { expect, test, vi } from 'vitest';
import { issue_launch_token } from '../agent_identity/data.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import {
  ConversationRoleMaterials,
  ConversationStartRequest,
  PromptDeliveryStarted,
} from '../conversation/contracts.ts';
import { Deferred } from '../core/deferred.ts';
import { CredentialRequestOperations } from '../credentials/requests/service.ts';
import { createCredentialRequestRoutes } from '../credentials/requests/routes.ts';
import { decode_snapshot, validate_request } from '../credentials/requests/logic/validation.ts';
import { setup } from './conversation_core_fixture.ts';
import { create } from './ticket_fixture.ts';
import { DatabaseCredentialResolver, FileCredentialCipher } from '../credentials/store.ts';

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

test('snapshot validation rejects a nonfinite nullable member without JSON coercion', () => {
  const snapshot = {
    ...metadata('synthetic'),
    kind: 'credential_request',
    state: 'pending',
    credential: null,
    delivery: null,
  };
  expect(() => decode_snapshot({ ...snapshot, replacement: Infinity })).toThrow(
    'invalid credential request snapshot',
  );
  // A request saved before fields credentials has no fields key.
  expect(decode_snapshot(snapshot).fields).toBeNull();
});

/** The real core and record fixture behind the credential request routes, with no child or host. */
async function credential_routes(conversation_id: string) {
  const f = setup();
  const ticket = create(f.database);
  await f.core.start_conversation(
    new ConversationStartRequest({
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
    }),
  );
  f.database.write((tx) => {
    tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', [conversation_id, ticket.id]);
    tx.run('INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)', [
      conversation_id,
      ticket.id,
    ]);
  });
  const writes = new ConversationHttpWriteScope();
  const operations = new CredentialRequestOperations({
    database: f.database,
    core: f.core,
    writes,
    key_path: f.database_path + '.credential-key',
    now: () => 100,
  });
  const app = await createApp(
    loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: f.database_path }),
    {
      database: f.database,
      areas: [{ prefix: '/api', routes: createCredentialRequestRoutes(operations) }],
    },
  );
  const headers = { Authorization: `Bearer ${issue_launch_token(f.database, conversation_id)}` };
  return { f, writes, operations, app, headers };
}

// Ordinary HTTP comparisons cannot seal the shared write scope at a pending dispatch.
// Reuse the real core and record fixture without starting a child or listening host.
test('a sealed shared write scope refuses late requests and drains an admitted save through dispatch', async () => {
  const { f, writes, operations, app, headers } = await credential_routes('credential-shutdown');
  const entered = new Deferred<void>();
  const released = new Deferred<void>();
  const dispatch = operations.dispatch.bind(operations);
  const blocked = vi.spyOn(operations, 'dispatch').mockImplementation(async (...args) => {
    if (args[1] !== null) {
      entered.resolve();
      await released.promise;
    }
    return await dispatch(...args);
  });
  const sent = vi.spyOn(f.core, 'send').mockResolvedValue(new PromptDeliveryStarted());
  const request_id = '19000000-0000-4000-8000-000000000010';
  const pending = await app.inject({
    method: 'POST',
    url: '/api/credentials/requests',
    headers,
    payload: metadata(request_id),
  });
  expect(pending.statusCode).toBe(200);
  blocked.mockClear();
  const admitted = app
    .inject({
      method: 'POST',
      url: `/api/credentials/requests/${request_id}/save`,
      payload: {
        name: 'Synthetic shutdown key',
        value: 'synthetic-private-value',
        expected_revision: null,
      },
    })
    .then((response) => response);
  try {
    await entered.promise;
    const intent = f.database.read((sql) =>
      sql.get<{ attempt_id: string; status: string }>(
        'SELECT attempt_id,status FROM credential_request_delivery_attempts WHERE request_id=?',
        [request_id],
      ),
    )!;
    expect(intent.status).toBe('intent');
    expect(
      f.database.read((sql) =>
        sql.get<{ count: number }>('SELECT count(*) AS count FROM credentials'),
      )?.count,
    ).toBe(1);
    writes.seal();
    let drained = false;
    const drain = writes.drain().then(() => {
      drained = true;
    });
    const late = await app.inject({
      method: 'POST',
      url: '/api/credentials/requests',
      headers,
      payload: metadata('19000000-0000-4000-8000-000000000011'),
    });
    expect(late.statusCode).toBe(503);
    expect(late.json()).toEqual({ detail: 'the conversation system is unavailable' });
    expect(
      f.database.read((sql) =>
        sql.get<{ count: number }>('SELECT count(*) AS count FROM credential_requests'),
      )?.count,
    ).toBe(1);
    expect(blocked).toHaveBeenCalledTimes(1);
    expect(drained).toBe(false);
    released.resolve();
    const completed = await admitted;
    expect(completed.statusCode).toBe(200);
    expect(completed.json()).toMatchObject({
      state: 'saved',
      delivery: { attempt_id: intent.attempt_id, status: 'started' },
    });
    await drain;
    await operations.finish_operations({ deadline: performance.now() / 1000 + 1 });
    expect(drained).toBe(true);
    expect(sent).toHaveBeenCalledTimes(1);
    expect(
      f.database.read((sql) =>
        sql.get<{ status: string }>(
          'SELECT status FROM credential_request_delivery_attempts WHERE attempt_id=?',
          [intent.attempt_id],
        ),
      )?.status,
    ).toBe('started');
  } finally {
    released.resolve();
    await admitted.catch(() => {});
    await operations.settled();
    await app.close();
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});

// The Manager accepts narrower TS UUID parsing. Pin that one intentional boundary.
test('credential metadata refuses a hexadecimal UUID prefix', () => {
  expect(() => validate_request(metadata('0x000000000000000000000000000001'))).toThrow(
    'request_id must be a UUID',
  );
});

// Risk: a fields save stores a partial or different set of values, or a refused save echoes
// one. The shutdown test above covers only a bearer value. This reuses its route fixture.
test('a fields request saves exactly its named values and refuses a partial set without echo', async () => {
  const { f, operations, app, headers } = await credential_routes('credential-fields');
  vi.spyOn(f.core, 'send').mockResolvedValue(new PromptDeliveryStarted());
  const request_id = '19000000-0000-4000-8000-000000000012';
  const fields = [
    { name: 'key_id', file: false },
    { name: 'private_key', file: true },
  ];
  const private_key = '-----BEGIN PRIVATE KEY-----\nsynthetic-only\n-----END PRIVATE KEY-----\n';
  const save = (value: unknown) =>
    app.inject({
      method: 'POST',
      url: `/api/credentials/requests/${request_id}/save`,
      payload: { name: 'Synthetic fields key', value, expected_revision: null },
    });
  try {
    const requested = await app.inject({
      method: 'POST',
      url: '/api/credentials/requests',
      headers,
      payload: { ...metadata(request_id), auth_kind: 'fields', fields },
    });
    expect(requested.statusCode).toBe(200);
    expect(requested.json()).toMatchObject({ state: 'pending', auth_kind: 'fields', fields });
    const partial = await save({ key_id: 'synthetic-key-id' });
    expect(partial.statusCode).toBe(400);
    expect(partial.body).not.toContain('synthetic-key-id');
    const extra = await save({ key_id: 'synthetic-key-id', private_key, other: 'x' });
    expect(extra.statusCode).toBe(400);
    expect(
      f.database.read((sql) =>
        sql.get<{ count: number }>('SELECT count(*) AS count FROM credentials'),
      )?.count,
    ).toBe(0);
    const saved = await save({ key_id: 'synthetic-key-id', private_key });
    expect(saved.statusCode).toBe(200);
    expect(saved.json()).toMatchObject({ state: 'saved', credential: { auth_kind: 'fields' } });
    expect(saved.body).not.toContain('synthetic-key-id');
    expect(saved.body).not.toContain('synthetic-only');
    const stored = f.database.read((sql) =>
      new DatabaseCredentialResolver(
        sql,
        new FileCredentialCipher(f.database_path + '.credential-key', true),
      ).resolve({ scheme: 'local', name: 'Synthetic fields key' }),
    );
    expect(stored.secret).toEqual({
      kind: 'fields',
      fields: { key_id: 'synthetic-key-id', private_key },
    });
  } finally {
    await operations.settled();
    await app.close();
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});
