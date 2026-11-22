/** Gap: a generic agent write runs outside the receipt transaction. Prove one route run per
 * inbox position, the child's in-process identity, the local CLI's error payloads, and that a
 * lost answer leaves an uncertain receipt that nothing repeats. Reuse the Ticket database and
 * the real inbox store; a bare app route stands in for the ordinary routes.
 */
import Fastify, { type FastifyInstance } from 'fastify';
import { expect, test } from 'vitest';
import { fixture } from '../tests/ticket_fixture.ts';
import { buildClock } from '../core/clock.ts';
import { loadConfig } from '../core/config.ts';
import { requestContext } from '../core/authority/identity.ts';
import { ORIGINAL_MAC_MACHINE_ID, type ExecutorIdentity } from '../execution_identity.ts';
import { InboxStore } from './data/inbox.ts';
import { AgentWrites, agent_request_context } from './vps_agent_writes.ts';
import type { FileStaging } from './vps_files.ts';
import type { JsonObject, QueuedEnvelope } from './wire.generated.ts';

test('an agent request runs its route once as the child and keeps that one receipt', async () => {
  const database = fixture();
  const identity: ExecutorIdentity = {
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
    application_revision: 'test',
    protocol_version: 26,
    spool_format_version: 5,
    connection_generation: 0,
  };
  const child = {
    executor_incarnation: identity.executor_incarnation,
    child_id: 'manager-child',
    conversation_id: 'manager-conversation',
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
      "INSERT INTO executor_child_assignments(executor_incarnation,child_id,conversation_id,principal_kind,principal_id,created_at_ms) VALUES(?,?,?,'manager','m_one',0)",
      [child.executor_incarnation, child.child_id, child.conversation_id],
    );
  });
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
  const store = new InboxStore(database, { machine_id: ORIGINAL_MAC_MACHINE_ID } as FileStaging);
  const writes = new AgentWrites(
    store,
    buildClock(loadConfig(undefined, { PLAN_TEST_MODE: '1' })),
    {
      authority: () => ({ kind: 'manager', id: 'm_one' }),
      validate_stop() {},
      async accept_stop_receipt() {},
    },
  );
  writes.app = app;
  const envelope = (sequence: number, args: JsonObject): QueuedEnvelope => ({
    conversation_id: child.conversation_id,
    kind: 'cli_write',
    payload: { child, operation: 'cli.request', arguments: args, turn: null },
    sender_id: 'sender',
    sequence,
    submission_id: `submission-${sequence}`,
    file_dependencies: [],
  });
  const send = (item: QueuedEnvelope) => writes.accept(item, identity, async () => null);
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
  });
});
