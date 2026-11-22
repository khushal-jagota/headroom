/** Gap: a generic agent write runs outside the receipt transaction. Prove one route run per
 * inbox position, the child's in-process identity, the local CLI's error payloads, and that a
 * lost or late answer leaves an uncertain receipt that nothing repeats. Reuse the Ticket database and
 * the real inbox store; a bare app route stands in for the ordinary routes.
 */
import Fastify, { type FastifyInstance } from 'fastify';
import { expect, test, vi } from 'vitest';
import { fixture } from '../tests/ticket_fixture.ts';
import { buildClock } from '../core/clock.ts';
import { loadConfig } from '../core/config.ts';
import { install_request_lifetime } from '../core/http/lifetime.ts';
import { requestContext } from '../core/authority/identity.ts';
import type { Principal } from '../core/contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID, type ExecutorIdentity } from '../execution_identity.ts';
import { InboxStore } from './data/inbox.ts';
import { MacInbox } from './vps_inbox.ts';
import { AgentWrites, agent_request_context, agent_response_json } from './vps_agent_writes.ts';
import type { FileStaging } from './vps_files.ts';
import type { JsonObject, QueuedEnvelope } from './wire.generated.ts';

const identity: ExecutorIdentity = {
  executor_incarnation: '11111111-1111-4111-8111-111111111111',
  application_revision: 'test',
  protocol_version: 26,
  spool_format_version: 5,
  connection_generation: 0,
};
/** One child of machine code, assigned to the given owner, and the real inbox store. */
function world(owner: { kind: string; id: string }) {
  const database = fixture();
  const child = {
    executor_incarnation: identity.executor_incarnation,
    child_id: `${owner.kind}-child`,
    conversation_id: `${owner.kind}-conversation`,
  };
  database.write((tx) => {
    tx.run(
      'INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,machine_id) VALUES(?,0,?)',
      [identity.executor_incarnation, ORIGINAL_MAC_MACHINE_ID],
    );
    tx.run(
      'INSERT OR REPLACE INTO mac_executor_presence(machine_id,executor_incarnation) VALUES(?,?)',
      [ORIGINAL_MAC_MACHINE_ID, identity.executor_incarnation],
    );
    tx.run(
      'INSERT INTO executor_child_assignments(executor_incarnation,child_id,conversation_id,principal_kind,principal_id,created_at_ms) VALUES(?,?,?,?,?,0)',
      [child.executor_incarnation, child.child_id, child.conversation_id, owner.kind, owner.id],
    );
    // A Manager child's conversation is that Manager's current one, as a real start links it.
    if (owner.kind === 'manager') {
      tx.run(
        "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,created_at,updated_at) VALUES (?,'Manager','project_vylo','codex','gpt-6.1-sol',0,0)",
        [owner.id],
      );
      tx.run('INSERT INTO agents(agent_key,conversation_id) VALUES (?,?)', [
        `manager_${owner.id}`,
        child.conversation_id,
      ]);
    }
  });
  const store = new InboxStore(database, { machine_id: ORIGINAL_MAC_MACHINE_ID } as FileStaging);
  const writes = new AgentWrites(
    store,
    buildClock(loadConfig(undefined, { PLAN_TEST_MODE: '1' })),
    {
      authority: () => owner as Principal,
      validate_stop() {},
      async accept_stop_receipt() {},
    },
  );
  const envelope = (sequence: number, operation: string, args: JsonObject): QueuedEnvelope => ({
    conversation_id: child.conversation_id,
    kind: 'cli_write',
    payload: { child, operation, arguments: args, turn: null },
    sender_id: 'sender',
    sequence,
    submission_id: `submission-${sequence}`,
    file_dependencies: [],
  });
  const send = (item: QueuedEnvelope) => writes.accept(item, identity, async () => null);
  return { database, child, writes, envelope, send };
}

test('an agent request runs its route once as the child and keeps that one receipt', async () => {
  const { child, writes, send, ...made } = world({ kind: 'manager', id: 'm_one' });
  const envelope = (sequence: number, args: JsonObject) =>
    made.envelope(sequence, 'cli.request', args);
  const database = made.database;
  const seen: unknown[] = [];
  let answer: { status: number; body: unknown } = { status: 200, body: { ok: true } };
  const app = Fastify();
  const scope = install_request_lifetime(app);
  let route_release: Promise<void> | undefined;
  let route_started: (() => void) | undefined;
  app.post('/api/probe', async (request, reply) => {
    seen.push({
      context: requestContext(request),
      query: request.query,
      body: request.body,
    });
    route_started?.();
    await route_release;
    return reply.code(answer.status).send(answer.body);
  });
  await app.ready();
  writes.app = app;
  const probe = { method: 'POST', path: '/api/probe?scope=all', body: { name: 'café' } };
  try {
    const first = envelope(1, probe);
    const accepted = await send(first);
    expect(accepted).toMatchObject({ disposition: 'accepted', result: { ok: true } });
    expect(seen).toEqual([
      {
        context: {
          principal: { kind: 'manager', id: 'm_one' },
          conversationId: child.conversation_id,
        },
        query: { scope: 'all' },
        body: { name: 'café' },
      },
    ]);
    expect(await send(first)).toEqual(accepted);
    expect(seen).toHaveLength(1);

    // A route refusal is the CLI's payload, and so is a failure without an error object.
    const refusal = { error: { code: 'agent_forbidden', message: 'Not yours', detail: {} } };
    answer = { status: 403, body: refusal };
    expect(await send(envelope(2, probe))).toMatchObject({
      disposition: 'refused',
      result: refusal,
    });
    answer = { status: 404, body: { detail: 'Not Found' } };
    expect(await send(envelope(3, probe))).toMatchObject({
      disposition: 'refused',
      result: {
        error: { code: 'http_error', message: 'HTTP 404', detail: { detail: 'Not Found' } },
      },
    });

    // The home loses the answer after it reserved the position: nothing runs the route again.
    const lost = envelope(4, probe);
    writes.app = {
      inject: () => Promise.reject(new Error('home stopped')),
    } as unknown as FastifyInstance;
    await expect(send(lost)).rejects.toThrow('home stopped');
    writes.app = app;
    const uncertain = await send(lost);
    expect(uncertain).toMatchObject({
      disposition: 'uncertain',
      result: { error: { code: 'uncertain' } },
    });
    expect(seen).toHaveLength(3);

    // A nested relay continues admitted work after the external HTTP door seals.
    // It stays in the same scope until the real handler completes.
    scope.seal();
    expect((await app.inject({ method: 'POST', url: '/api/probe', payload: {} })).statusCode).toBe(
      503,
    );
    let release!: () => void;
    route_release = new Promise<void>((resolve) => {
      release = resolve;
    });
    const started = new Promise<void>((resolve) => {
      route_started = resolve;
    });
    answer = { status: 200, body: { continued: true } };
    const continuation = send(envelope(5, probe));
    await started;
    let settled = false;
    const settlement = scope.settled().then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(settled).toBe(false);
    release();
    expect(await continuation).toMatchObject({
      disposition: 'accepted',
      result: { continued: true },
    });
    await settlement;
    expect(settled).toBe(true);

    // A child whose assignment closed cannot act for its former principal.
    database.write((tx) =>
      tx.run('UPDATE executor_child_assignments SET closed_at_ms=1 WHERE child_id=?', [
        child.child_id,
      ]),
    );
    await expect(send(envelope(6, probe))).rejects.toMatchObject({ code: 'agent_forbidden' });
    expect(seen).toHaveLength(4);
  } finally {
    await app.close();
  }
  // A local generic agent has no launch identity, so a relayed one has none either.
  expect(agent_request_context({ kind: 'conversation', id: 'c_one' }, 'c_one')).toEqual({
    principal: { kind: 'unidentified', id: 'unidentified' },
    conversationId: null,
  }); // A file is text whatever it holds, as `send_text` reads it. An API answer is data.
  const file = { 'content-type': 'application/json', 'content-disposition': 'inline' };
  expect(agent_response_json(200, '{"a":1}', file)).toEqual({ text: '{"a":1}' });
  expect(agent_response_json(200, '42', { 'content-type': 'text/markdown' })).toEqual({
    text: '42',
  });
  expect(agent_response_json(200, '{"a":1}', { 'content-type': 'application/json' })).toEqual({
    a: 1,
  });
});

// Risk: a generic conversation's child, which has no Panels identity, makes the canonical writes
// that the HTTP door refuses a local generic agent. Gap: only its relayed routes were mapped.
test('a generic conversation child makes only the writes of an agent without an identity', async () => {
  const { envelope, send } = world({ kind: 'conversation', id: 'conversation-conversation' });
  await expect(
    send(envelope(1, 'ticket.create', { body: { title: 'New', brief: 'Brief' } })),
  ).rejects.toMatchObject({ code: 'agent_forbidden' });
  await expect(
    send(
      envelope(2, 'message.send', {
        body: { target: { kind: 'owner', id: 'owner' }, message: 'Hello' },
      }),
    ),
  ).rejects.toMatchObject({ code: 'agent_forbidden' });
});

// Risk: a route exceeds the old 15-second observation limit and HTTP retries expose its
// unfinished receipt as terminal. Existing coverage proves only immediate replay. Reuse the
// real inbox, including its retry and shutdown owners, with a controlled injected route.
test('a slow relayed write retains its receipt through retries and shutdown', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  try {
    const { writes, envelope, send } = world({ kind: 'manager', id: 'm_one' });
    const inbox = new MacInbox(writes.store, writes.clock, writes.runs);
    let finish!: (response: unknown) => void;
    const inject = vi.fn(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    inbox.agent_writes.app = { inject } as unknown as FastifyInstance;
    const item = envelope(1, 'cli.request', { method: 'POST', path: '/api/probe', body: null });
    const batch = { sender_id: item.sender_id, items: [{ envelope: item, sender_refusal: null }] };
    const answering = inbox.accept_batch(item.conversation_id, batch, identity);
    await vi.advanceTimersByTimeAsync(21_000);
    expect(await answering).toMatchObject({ receipts: [], stop: { reason: 'retry_later' } });
    // A second HTTP request waits for the original producer instead of returning its
    // provisional uncertain receipt. Later items remain behind that producer as well.
    const retrying = inbox.accept_batch(item.conversation_id, batch, identity);
    await vi.advanceTimersByTimeAsync(21_000);
    expect(await retrying).toMatchObject({ receipts: [], stop: { reason: 'retry_later' } });
    expect(inject).toHaveBeenCalledTimes(1);
    let settled = false;
    void inbox.settled().then(() => {
      settled = true;
    });
    const stopping = inbox.stop({ deadline: performance.now() / 1000 });
    const observed_stop = expect(stopping).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(1);
    await observed_stop;
    expect(settled).toBe(false);
    finish({
      statusCode: 200,
      body: '{"applied":true}',
      headers: { 'content-type': 'application/json' },
    });
    await inbox.settled();
    expect(settled).toBe(true);
    // After the old owner drains, the next server receives the final receipt unchanged.
    expect(await send(item)).toMatchObject({ disposition: 'accepted', result: { applied: true } });
    expect(inject).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});

// Risk: a Manager is reset while its machine is away. When the machine returns, the writes that
// the old child held reach every route as that Manager. A local agent's token is revoked at the
// reset; the relayed child must lose its authority the same way.
test("a reset Manager's old child can no longer act for it", async () => {
  const { database, writes, envelope, send } = world({ kind: 'manager', id: 'm_one' });
  const seen: unknown[] = [];
  const app = Fastify();
  app.post('/api/probe', async (request) => {
    seen.push(requestContext(request));
    return { ok: true };
  });
  await app.ready();
  writes.app = app;
  try {
    database.write((tx) =>
      tx.run("UPDATE agents SET conversation_id='manager-successor' WHERE agent_key=?", [
        'manager_m_one',
      ]),
    );
    await expect(
      send(envelope(1, 'cli.request', { method: 'POST', path: '/api/probe', body: {} })),
    ).rejects.toMatchObject({ code: 'agent_forbidden' });
    await expect(
      send(envelope(2, 'ticket.create', { body: { title: 'From the old child' } })),
    ).rejects.toMatchObject({ code: 'agent_forbidden' });
    expect(seen).toEqual([]);
  } finally {
    await app.close();
  }
});
