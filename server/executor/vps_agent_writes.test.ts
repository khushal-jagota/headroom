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
import { requestContext } from '../core/authority/identity.ts';
import type { Principal } from '../core/contracts.ts';
import { AGENT_REQUEST_SERVER_LIMIT_SECONDS } from './configuration.ts';
import { ORIGINAL_MAC_MACHINE_ID, type ExecutorIdentity } from '../execution_identity.ts';
import { InboxStore } from './data/inbox.ts';
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
  app.post('/api/probe', async (request, reply) => {
    seen.push({
      context: requestContext(request),
      query: request.query,
      body: request.body,
    });
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

    // A child whose assignment closed cannot act for its former principal.
    database.write((tx) =>
      tx.run('UPDATE executor_child_assignments SET closed_at_ms=1 WHERE child_id=?', [
        child.child_id,
      ]),
    );
    await expect(send(envelope(5, probe))).rejects.toMatchObject({ code: 'agent_forbidden' });
    expect(seen).toHaveLength(3);
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

// Risk: a relayed write that never answers holds every later item of its conversation behind
// the item lock. Gap: only a lost answer was proven, never a route that keeps running.
test('a relayed write that runs out of time leaves its one uncertain receipt', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  try {
    const { writes, envelope, send } = world({ kind: 'manager', id: 'm_one' });
    const signals: AbortSignal[] = [];
    writes.app = {
      inject: (options: { signal: AbortSignal }) => {
        signals.push(options.signal);
        return new Promise(() => {});
      },
    } as unknown as FastifyInstance;
    const item = envelope(1, 'cli.request', { method: 'POST', path: '/api/probe', body: null });
    const answering = send(item);
    await vi.advanceTimersByTimeAsync(AGENT_REQUEST_SERVER_LIMIT_SECONDS * 1000);
    const receipt = await answering;
    expect(receipt).toMatchObject({
      disposition: 'uncertain',
      result: { error: { code: 'uncertain' } },
    });
    expect((receipt.result.error as { message: string }).message).not.toContain('stopped');
    expect(signals.map((signal) => signal.aborted)).toEqual([true]);
    // A repeat of the item reads that receipt and never runs the route again.
    expect(await send(item)).toEqual(receipt);
    expect(signals).toHaveLength(1);
  } finally {
    vi.useRealTimers();
  }
});
