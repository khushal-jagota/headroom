/** Gaps: autonomous orchestration and delivery fates are absent from Ticket route proofs.
 * Reuses Ticket SQLite fixtures and the existing in-memory contract. No provider adapter.
 */
import { afterEach, expect, test, vi } from 'vitest';
import { fixture, fixture_path, create, edit, read, now, claim } from './ticket_fixture.ts';
import * as data from '../tickets/data/index.ts';
import * as feedback from '../tickets/revision_feedback.ts';
import { OWNER } from '../core/contracts.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import {
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
} from '../conversation/contracts.ts';
import {
  start_ready_worker_step,
  WorkerStepReadinessLoop,
} from '../runtime/worker_step_readiness_loop.ts';
import { prepare_background_loops } from '../core/loops.ts';
import Fastify from 'fastify';
import { readinessTestRoutes } from '../runtime/testing.ts';
import { loadConfig } from '../core/config.ts';
import { TestClock } from '../core/clock.ts';
import { acquireLock } from '../core/locks/index.ts';
import { ChangeSignal } from '../core/changes/index.ts';
afterEach(() => vi.restoreAllMocks());
function setup() {
  const db = fixture();
  const system = new InMemoryConversationSystem();
  const options = {
    connect_database: () => db,
    conversation_system: system,
    worker_type_registry: configured_worker_type_registry(),
    now: () => now,
  };
  return { db, system, options };
}
test.each([
  ['refused', () => new PromptDeliveryRefused({ refusal_reason: 'session_did_not_load' }), 'none'],
  ['suppressed', () => new PromptDeliverySuppressed({ reason: 'test suppression' }), 'none'],
  ['uncertain', () => new PromptDeliveryUncertain(), 'errored'],
  [
    'backend_did_not_start',
    () => new PromptDeliveryRefused({ refusal_reason: 'backend_did_not_start' }),
    'errored',
  ],
] as const)('delivery %s preserves its claim outcome', async (_name, fate, expected_claim) => {
  const { db, system, options } = setup();
  const ticket = create(db);
  vi.spyOn(system, 'send').mockResolvedValue(fate());
  const result = await start_ready_worker_step(ticket.id, options);
  expect(result.started).toBe(false);
  expect(read(db, ticket.id).worker_step_claim).toBe(expected_claim);
  if (_name === 'backend_did_not_start') expect(read(db, ticket.id).conversation_id).not.toBeNull();
});
test('an occupied conversation prevents another claim or opener', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  const taken = read(db, ticket.id);
  db.write((tx) =>
    data.release_worker_step_claim(tx, ticket.id, {
      expected_claim: taken.worker_step_claim,
      expected_claim_revision: taken.worker_step_claim_revision,
      now,
    }),
  );
  expect(await system.is_running(taken.conversation_id!)).toBe(true);
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(false);
  expect(read(db, ticket.id).worker_step_claim).toBe('none');
  expect(system.backend_prompt_writes(taken.conversation_id!)).toHaveLength(1);
});
test('start refusal releases a new link and the claim', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  vi.spyOn(system, 'start_conversation').mockRejectedValue(new Error('start refused'));
  await start_ready_worker_step(ticket.id, options);
  expect(read(db, ticket.id)).toMatchObject({ worker_step_claim: 'none', conversation_id: null });
});
test('late uncertainty cannot overwrite a newer claim', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  vi.spyOn(system, 'send').mockImplementation(async () => {
    const taken = read(db, ticket.id);
    db.write((tx) =>
      data.release_worker_step_claim(tx, ticket.id, {
        expected_claim: taken.worker_step_claim,
        expected_claim_revision: taken.worker_step_claim_revision,
        now,
      }),
    );
    claim(db, ticket.id);
    return new PromptDeliveryUncertain();
  });
  await start_ready_worker_step(ticket.id, options);
  expect(read(db, ticket.id).worker_step_claim).toBe('out');
});
test('a refused user-stage opener forgets its opener and permits a retry', async () => {
  const { db, system, options } = setup();
  const ticket = create(db, { worker_type: 'new_worker' });
  vi.spyOn(system, 'send').mockResolvedValue(
    new PromptDeliveryRefused({ refusal_reason: 'backend_did_not_start' }),
  );
  await start_ready_worker_step(ticket.id, options);
  expect(read(db, ticket.id).worker_step_claim).toBe('none');
  expect(
    db.read((sql) =>
      sql.get('SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id=?', [ticket.id]),
    ),
  ).toBeUndefined();
});
test('successful delivery only acknowledges the feedback revision its prompt carried', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  db.write((tx) =>
    feedback.set_feedback(tx, ticket.id, {
      stage: ticket.stage,
      sender: OWNER,
      message: 'first',
      now,
    }),
  );
  const send = system.send.bind(system);
  vi.spyOn(system, 'send').mockImplementation(async (...args) => {
    const result = await send(...args);
    db.write((tx) =>
      feedback.set_feedback(tx, ticket.id, {
        stage: ticket.stage,
        sender: OWNER,
        message: 'later',
        now,
      }),
    );
    return result;
  });
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(true);
  expect(db.read((sql) => feedback.snapshot(sql, ticket.id))?.text).toContain('later');
});
test('successful user-stage opener releases its claim and cannot run twice', async () => {
  const { db, system, options } = setup();
  const ticket = create(db, { worker_type: 'new_worker' });
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(true);
  expect(read(db, ticket.id).worker_step_claim).toBe('none');
  system.complete_running_turn(read(db, ticket.id).conversation_id!);
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(false);
});
test('poll hook, in-flight deduplication, and shutdown share one loop owner', async () => {
  const { db, options } = setup();
  const ticket = create(db);
  let held = true;
  const loop = new WorkerStepReadinessLoop({ ...options, before_poll: () => held });
  expect(loop.poll_once()).toEqual([]);
  held = false;
  expect(loop.poll_once()).toEqual([ticket.id]);
  expect(loop.poll_once()).toEqual([]);
  await loop.drain();
  expect(loop.poll_once()).toEqual([]);
  await loop.stop();
  expect(loop.poll_once()).toEqual([]);
});
test('production composition requires opt-in and dispatcher ownership; test mode never polls automatically', async () => {
  const { db, options } = setup();
  create(db);
  const lock_path = fixture_path(db) + '.dispatcher.lock';
  const owner = acquireLock(lock_path);
  expect(owner).not.toBeNull();
  const contender = acquireLock(lock_path);
  expect(contender).toBeNull();
  const base = {
    enable_worker_readiness: true,
    test_mode: false,
    dispatch_enabled: true,
    dispatcher_owned: owner !== null,
    worker: options,
  };
  expect(
    prepare_background_loops({ ...base, dispatcher_owned: contender !== null })
      .worker_step_readiness_loop,
  ).toBeNull();
  for (const refusal of [
    { enable_worker_readiness: false },
    { test_mode: true },
    { dispatch_enabled: false },
    { dispatcher_owned: false },
  ]) {
    expect(prepare_background_loops({ ...base, ...refusal }).worker_step_readiness_loop).toBeNull();
  }
  const loops = prepare_background_loops(base);
  const changes = new ChangeSignal();
  loops.start(60, changes);
  await loops.worker_step_readiness_loop!.drain();
  const next = create(db);
  changes.emit();
  await new Promise((resolve) => setImmediate(resolve));
  await loops.worker_step_readiness_loop!.drain();
  expect(read(db, next.id).worker_step_claim).toBe('out');
  await loops.stop();
  const last = create(db);
  changes.emit();
  await new Promise((resolve) => setImmediate(resolve));
  expect(read(db, last.id).worker_step_claim).toBe('none');
  owner!.close();
});

// Gap: composition admission alone cannot prove that the test HTTP door refuses a missing fake.
test('the readiness test route refuses a missing test conversation system', async () => {
  const db = fixture();
  const ticket = create(db);
  const app = Fastify();
  await app.register(readinessTestRoutes.routes, {
    database: db,
    config: loadConfig('/missing-worker-test-config', { PLAN_TEST_MODE: '1' }),
    clock: new TestClock('2026-07-04T10:00:00+00:00'),
    identity: () => ({ principal: OWNER, conversationId: null }),
  });
  try {
    const result = await app.inject({ method: 'POST', url: '/readiness' });
    expect(result.statusCode).toBe(400);
    expect(result.json().message).toContain('require a test conversation system');
    expect(read(db, ticket.id).worker_step_claim).toBe('none');
  } finally {
    await app.close();
  }
});

// Gap: the in-memory instrument writes no durable compaction events. Seed only the
// record facts that the scanner reads, then prove its real queue boundary twice.
test('an unanswered compaction sends one queued notice and an uncertain notice prevents another automatic attempt', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  const id = read(db, ticket.id).conversation_id!;
  db.write((tx) => {
    tx.run(
      'INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) VALUES (?,1,?,?,?)',
      [id, 'prompt', JSON.stringify({ sender_message_id: 'worker_step_message_original' }), now],
    );
    tx.run(
      'UPDATE conversations SET automatically_compacted_through_sequence=2 WHERE conversation_id=?',
      [id],
    );
  });
  const loop = new WorkerStepReadinessLoop(options);
  expect(loop.tell_every_worker_that_lost_its_memory()).toEqual([ticket.id]);
  await loop.drain();
  expect(await system.held_prompts(id)).toHaveLength(1);
  expect((await system.held_prompts(id))[0]!.content).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ text: expect.stringContaining('Your context was compacted') }),
    ]),
  );
  loop.tell_every_worker_that_lost_its_memory();
  await loop.drain();
  expect(await system.held_prompts(id)).toHaveLength(1);
  system.complete_running_turn(id);
  for (const held of await system.held_prompts(id))
    await system.discard_held_prompt(id, held.held_prompt_id);
  db.write((tx) =>
    tx.run(
      'INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) VALUES (?,3,?,?,?)',
      [
        id,
        'prompt_delivery_uncertain',
        JSON.stringify({ sender_message_id: 'worker_step_memory_notice_uncertain' }),
        now,
      ],
    ),
  );
  expect(loop.tell_every_worker_that_lost_its_memory()).toEqual([]);
  await loop.stop();
});
