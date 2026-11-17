/** Gaps: autonomous orchestration and delivery fates are absent from Ticket route proofs.
 * Reuses Ticket SQLite fixtures and the existing in-memory contract. No provider adapter.
 */
import { afterEach, expect, test, vi } from 'vitest';
import {
  fixture,
  fixture_path,
  create as create_ticket,
  edit,
  read,
  now,
  claim,
  propose,
  advance,
} from './ticket_fixture.ts';
import * as data from '../tickets/data/index.ts';
import * as decisions from '../tickets/decision_messages.ts';
import { deliver_ticket_decision_messages } from '../runtime/decision_message_delivery.ts';
import { conversation_holds_an_unanswered_memory_loss } from '../runtime/worker_memory.ts';
import { restart_worker } from '../tickets/worker_restart.ts';
import { OWNER, PlannerError } from '../core/contracts.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import {
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
  PromptDeliveryStarted,
} from '../conversation/contracts.ts';
import {
  start_ready_worker_step,
  WorkerStepReadinessLoop,
} from '../runtime/worker_step_readiness_loop.ts';
import { prepare_background_loops } from '../core/loops.ts';
import Fastify from 'fastify';
import { dirname } from 'node:path';
import { configure_conversation_start } from '../runtime/conversation_start.ts';
import { readinessTestRoutes } from '../runtime/testing.ts';
import { loadConfig } from '../core/config.ts';
import { TestClock } from '../core/clock.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import { Deferred } from '../conversation/runtime/state.ts';
import { acquireLock } from '../core/locks/index.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { AutomaticStartAdmission, explicit_start } from '../conversation/start_admission/index.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import * as readiness from '../runtime/worker_step_readiness.ts';
afterEach(() => vi.restoreAllMocks());
// Ordinary-step cases start after the initial approval instruction reached its Worker.
function create(...args: Parameters<typeof create_ticket>): ReturnType<typeof create_ticket> {
  const ticket = create_ticket(...args);
  args[0].write((sql) =>
    sql.run("UPDATE ticket_decision_messages SET delivery_status='delivered' WHERE ticket_id=?", [
      ticket.id,
    ]),
  );
  return ticket;
}
function setup() {
  const db = fixture();
  configure_conversation_start(db, {
    db_path: fixture_path(db),
    workspace_root: dirname(fixture_path(db)),
  });
  const system = new InMemoryConversationSystem();
  const options = {
    connect_database: () => db,
    conversation_system: system,
    worker_type_registry: configured_worker_type_registry(),
    now: () => now,
  };
  return { db, system, options };
}

// Python restart ports: the existing sequential HTTP list cannot interleave a
// Worker proposal or replacement claim with an awaited kill, or race the link lock.
test('the trial automatically starts only new Tickets without changing copied Tickets', async () => {
  const { db, options } = setup();
  const copied = create_ticket(db, { now: now - 1 });
  const fresh = create_ticket(db, { now });
  const before = read(db, copied.id);
  const automatic = { ...options, automatic_tickets_created_after: now };
  const loop = new WorkerStepReadinessLoop(automatic);
  expect(loop.poll_once()).toEqual([fresh.id]);
  await loop.drain();
  expect(read(db, copied.id)).toEqual(before);
  expect(read(db, fresh.id).conversation_id).not.toBeNull();
  expect((await start_ready_worker_step(copied.id, automatic)).started).toBe(false);
  expect((await start_ready_worker_step(copied.id, options)).started).toBe(true);
});

test('restart keeps a worker completion that lands during kill', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  const old_id = read(db, ticket.id).conversation_id!;
  const kill = system.kill.bind(system);
  vi.spyOn(system, 'kill').mockImplementation(async (id) => {
    propose(db, read(db, ticket.id), 'Completed while restart waited.');
    await kill(id);
  });
  const result = await restart_worker(system, db, OWNER, ticket.id, {
    now: options.now,
    start_worker_step: () => start_ready_worker_step(ticket.id, options),
  });
  expect(result.started).toBe(false);
  expect(result.not_started_because).toContain('awaiting_approval');
  expect(read(db, ticket.id)).toMatchObject({
    worker_step_claim: 'none',
    conversation_id: null,
    pending_proposal: { body: 'Completed while restart waited.' },
  });
  expect(system.backend_prompt_writes(old_id)).toHaveLength(1);
});

test('restart releases the current errored claim generation after kill', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  const claimed = read(db, ticket.id);
  db.write((tx) =>
    data.mark_worker_step_claim_errored(tx, ticket.id, {
      expected_claim: claimed.worker_step_claim,
      expected_claim_revision: claimed.worker_step_claim_revision,
      now,
      error_reason: 'delivery uncertain',
    }),
  );
  const admitted = read(db, ticket.id);
  const kill = system.kill.bind(system);
  vi.spyOn(system, 'kill').mockImplementation(async (id) => {
    db.write((tx) => {
      expect(
        data.release_worker_step_claim(tx, ticket.id, {
          expected_claim: admitted.worker_step_claim,
          expected_claim_revision: admitted.worker_step_claim_revision,
          now,
        }),
      ).toBe(true);
      data.mark_ticket_errored(tx, ticket.id, { now, error_reason: 'delivery uncertain' });
    });
    await kill(id);
  });
  const result = await restart_worker(system, db, OWNER, ticket.id, {
    now: options.now,
    start_worker_step: () => start_ready_worker_step(ticket.id, options),
  });
  expect(result.started).toBe(true);
  const restarted = read(db, ticket.id);
  expect(restarted.worker_step_claim).toBe('out');
  expect(restarted.worker_step_claim_revision).toBeGreaterThan(admitted.worker_step_claim_revision);
  expect(restarted.conversation_id).not.toBe(claimed.conversation_id);
});

test('the next opener waits for restart and only one starts a fresh conversation', async () => {
  const { db, system, options } = setup();
  const ticket = edit(db, create(db), { ceiling: 'needs_implementation' });
  await start_ready_worker_step(ticket.id, options);
  const old_id = read(db, ticket.id).conversation_id!;
  let announce_kill!: () => void;
  const kill_started = new Promise<void>((resolve) => (announce_kill = resolve));
  let release_kill!: () => void;
  const may_kill = new Promise<void>((resolve) => (release_kill = resolve));
  const kill = system.kill.bind(system);
  vi.spyOn(system, 'kill').mockImplementation(async (id) => {
    announce_kill();
    await may_kill;
    await kill(id);
  });
  const restarting = restart_worker(system, db, OWNER, ticket.id, {
    now: options.now,
    start_worker_step: () => start_ready_worker_step(ticket.id, options),
  });
  await kill_started;
  try {
    propose(db, read(db, ticket.id), 'Advance before the next opener.');
    let next_settled = false;
    const next_step = start_ready_worker_step(ticket.id, options).then((result) => {
      next_settled = true;
      return result;
    });
    await new Promise((resolve) => setImmediate(resolve));
    expect(next_settled).toBe(false);
    expect(system.backend_prompt_writes(old_id)).toHaveLength(1);
    release_kill();
    const [restarted, next] = await Promise.all([restarting, next_step]);
    expect([restarted.started, next.started].filter(Boolean)).toHaveLength(1);
    const current = read(db, ticket.id);
    expect(current.stage).toBe('needs_implementation');
    expect(current.worker_step_claim).toBe('out');
    expect(current.conversation_id).not.toBeNull();
    expect(current.conversation_id).not.toBe(old_id);
    expect(system.backend_prompt_writes(old_id)).toHaveLength(1);
  } finally {
    release_kill();
    await restarting;
  }
});

// The HTTP restart list never reaches an absent Mac. Admission must not kill,
// start, edit launch configuration, or change its already-owned claim/link.
test('restart refuses a Mac ticket while the Mac is away without changing it', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  db.write((tx) => {
    tx.run("UPDATE tickets SET locale='macos',machine_id=? WHERE id=?", [
      ORIGINAL_MAC_MACHINE_ID,
      ticket.id,
    ]);
  });
  const before = read(db, ticket.id);
  const kill = vi.spyOn(system, 'kill');
  const start = vi.fn(() => start_ready_worker_step(ticket.id, options));
  const configure = vi.fn();
  await expect(
    restart_worker(system, db, OWNER, ticket.id, {
      now: options.now,
      start_worker_step: start,
      write_employee_configuration: configure,
    }),
  ).rejects.toMatchObject({
    code: 'validation',
    message: 'Waiting for the Mac: restart this Ticket once it is back',
    detail: { ticket_id: ticket.id },
  });
  expect(kill).not.toHaveBeenCalled();
  expect(start).not.toHaveBeenCalled();
  expect(configure).not.toHaveBeenCalled();
  expect(read(db, ticket.id)).toEqual(before);
});
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
  vi.spyOn(system, 'send').mockImplementation(async (_id, _content, sent) => {
    const claimed = read(db, ticket.id);
    expect(sent.sender_message_id).toBe(
      `worker_step_message_${ticket.id}:${claimed.worker_step_claim_revision}`,
    );
    expect(
      db.read((sql) =>
        sql.get(
          'SELECT * FROM worker_step_dispatch_intents WHERE ticket_id=? AND claim_revision=?',
          [ticket.id, claimed.worker_step_claim_revision],
        ),
      ),
    ).toEqual({
      ticket_id: ticket.id,
      claim_revision: claimed.worker_step_claim_revision,
      sender_message_id: sent.sender_message_id,
      state: 'dispatch_started',
    });
    return fate();
  });
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
  const starts = new AutomaticStartAdmission({ clock: () => 0 });
  Object.assign(system, { start_admission: starts });
  starts.try_start({
    identity: `worker-preparation:${ticket.id}`,
    work_class: 'worker',
    destination: HOME_MACHINE_ID,
  });
  const refusal = vi.fn(() => new PlannerError('validation', 'Execution refused'));
  configure_conversation_start(db, {
    db_path: fixture_path(db),
    workspace_root: dirname(fixture_path(db)),
    execution_refusal: refusal,
  });
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(false);
  expect(starts.snapshots()).toEqual([]);
  expect(refusal).not.toHaveBeenCalled();
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
test('decisions reach the actual prompt and queue ownership prevents replay', async () => {
  const { db, system, options } = setup();
  const ticket = create_ticket(db);
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(true);
  const id = read(db, ticket.id).conversation_id!;
  expect(system.backend_prompt_writes(id)[0]).toMatchObject({
    content: expect.arrayContaining([
      expect.objectContaining({
        text: expect.stringContaining('User approved the Brief proposal.'),
      }),
    ]),
  });
  const proposed = propose(db, read(db, ticket.id), 'Revise this proposal.');
  db.write((tx) =>
    data.reject_proposal(tx, ticket.id, {
      message: 'Keep the note.',
      principal: OWNER,
      expected_proposal_revision: proposed.pending_proposal_revision,
      decision_id: 'test-reject',
      now,
    }),
  );
  // Another turn can start after the idle check; retain the combined prompt in the queue.
  vi.spyOn(system, 'is_running').mockResolvedValueOnce(false);
  const send = system.send.bind(system);
  vi.spyOn(system, 'send').mockImplementationOnce((id, content, options) =>
    send(id, content, { ...options, mode: 'queue' }),
  );
  await start_ready_worker_step(ticket.id, options);
  await start_ready_worker_step(ticket.id, options);
  expect(await system.held_prompts(id)).toHaveLength(1);
  expect((await system.held_prompts(id))[0]!.content).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        text: expect.stringContaining(
          'User sent back the What Changes proposal with this note:\nKeep the note.',
        ),
      }),
    ]),
  );
  expect(db.read((sql) => decisions.has_undelivered(sql, ticket.id))).toBe(true);
  // A dequeued prompt remains owned until its canonical ownership contract releases it.
  vi.spyOn(system, 'held_prompts').mockResolvedValueOnce([]);
  await start_ready_worker_step(ticket.id, options);
  expect(
    db.read((sql) =>
      sql.get<{ delivery_status: string }>(
        "SELECT delivery_status FROM ticket_decision_messages WHERE decision_id='test-reject'",
      ),
    )!.delivery_status,
  ).toBe('queued');
  // Once ownership and durable outcome are both absent, explicit restart authorizes retry.
  vi.spyOn(system, 'sender_message_ids_awaiting_outcome').mockResolvedValueOnce(new Set());
  await start_ready_worker_step(ticket.id, options);
  const status = () =>
    db.read((sql) =>
      sql.get<{ delivery_status: string }>(
        "SELECT delivery_status FROM ticket_decision_messages WHERE decision_id='test-reject'",
      ),
    )!.delivery_status;
  expect(status()).toBe('uncertain');
  expect(read(db, ticket.id).worker_step_claim).toBe('errored');
  await start_ready_worker_step(ticket.id, options);
  expect(status()).toBe('uncertain');
  const result = await restart_worker(system, db, OWNER, ticket.id, {
    now: options.now,
    start_worker_step: () => start_ready_worker_step(ticket.id, options),
  });
  expect(result.revision_restarted_in_fresh_conversation).toBe(true);
  expect(status()).toBe('delivered');
  const fresh = read(db, ticket.id).conversation_id!;
  expect(fresh).not.toBe(id);
  expect(system.backend_prompt_writes(fresh)[0]!.text).toContain(
    'User sent back the What Changes proposal with this note:\nKeep the note.',
  );
});
// Multiple decisions can accumulate before a step is eligible. All must share its
// actual prompt without violating the unique decision receipt identities.
test('one step carries every pending decision and a done Ticket gets no wake', async () => {
  const { db, system, options } = setup();
  const ticket = create_ticket(db);
  db.write((tx) =>
    tx.run(
      `INSERT INTO ticket_decision_messages (decision_id,ticket_id,action,field,proposal_revision,sender_kind,sender_id,request_json,message,model_text,created_at,sender_message_id)
     SELECT 'second-note',ticket_id,action,field,proposal_revision,sender_kind,sender_id,request_json,message,'Another decision note',created_at,'second-note-message' FROM ticket_decision_messages WHERE ticket_id=? LIMIT 1`,
      [ticket.id],
    ),
  );
  const pending = db.read((sql) =>
    sql.all<{ model_text: string }>(
      "SELECT model_text FROM ticket_decision_messages WHERE ticket_id=? AND delivery_status='pending' ORDER BY created_at,rowid",
      [ticket.id],
    ),
  );
  expect(pending.length).toBeGreaterThan(1);
  await start_ready_worker_step(ticket.id, options);
  const id = read(db, ticket.id).conversation_id!;
  const writes = system.backend_prompt_writes(id);
  expect(writes).toHaveLength(1);
  for (const row of pending) expect(writes[0]!.text).toContain(row.model_text);
  expect(db.read((sql) => decisions.has_undelivered(sql, ticket.id))).toBe(false);
  system.complete_running_turn(id);
  advance(db, read(db, ticket.id));
  await start_ready_worker_step(ticket.id, options);
  expect(system.backend_prompt_writes(id)).toHaveLength(1);
  expect(db.read(decisions.pending_ticket_ids)).not.toContain(ticket.id);
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
  await loop.stop({ deadline: performance.now() / 1000 + 1 });
  expect(loop.poll_once()).toEqual([]);
});

// The engine proof does not establish that readiness transfers its preparation
// reservation or that explicit starts bypass this automatic caller.
test('automatic preparation transfers the claim identity, refunds refusal, and exempts an explicit retry', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  const starts = new AutomaticStartAdmission({ clock: () => 0 });
  Object.assign(system, { start_admission: starts });
  const prepare = vi.spyOn(starts, 'try_start');
  vi.spyOn(system, 'send').mockImplementation(async (_id, _content, sent) => {
    expect(starts.snapshots().map((entry) => entry.request.identity)).toEqual([
      sent.sender_message_id,
    ]);
    return new PromptDeliveryRefused({ refusal_reason: 'session_did_not_load' });
  });
  expect((await start_ready_worker_step(ticket.id, options)).delivery_fate).toBe('refused');
  expect(prepare).toHaveBeenCalledWith({
    identity: `worker-preparation:${ticket.id}`,
    work_class: 'worker',
    destination: HOME_MACHINE_ID,
  });
  expect(starts.snapshots()).toEqual([]);
  expect((await start_ready_worker_step(ticket.id, options)).delivery_fate).toBe(null);
  expect(read(db, ticket.id).worker_step_claim).toBe('none');
  vi.mocked(system.send).mockResolvedValue(new PromptDeliveryStarted());
  expect((await explicit_start(() => start_ready_worker_step(ticket.id, options))).started).toBe(
    true,
  );
  expect(prepare).toHaveBeenCalledTimes(2);
});

// Existing start cases do not hold asynchronous preparation open across polls.
// Reuse their in-memory conversations and SQLite source. Machine policy has its
// own direct proof, so this case isolates destination fairness and preparation caps.
test('blocked preparations cap each destination and rotate the next pass', async () => {
  const { db, system, options } = setup();
  const tickets = Array.from({ length: 4 }, () => create(db));
  for (const ticket of tickets) {
    await start_ready_worker_step(ticket.id, options);
    const taken = read(db, ticket.id);
    system.complete_running_turn(taken.conversation_id!);
    db.write((tx) =>
      data.release_worker_step_claim(tx, ticket.id, {
        expected_claim: taken.worker_step_claim,
        expected_claim_revision: taken.worker_step_claim_revision,
        now,
      }),
    );
  }
  db.write((tx) => {
    for (const [index, ticket] of tickets.entries())
      tx.run('UPDATE tickets SET updated_at=? WHERE id=?', [now + index, ticket.id]);
    tx.run("UPDATE tickets SET locale='macos',machine_id=? WHERE id=?", [
      ORIGINAL_MAC_MACHINE_ID,
      tickets[3]!.id,
    ]);
  });
  Object.assign(system, {
    start_admission: new AutomaticStartAdmission({ limit: 2, clock: () => 0 }),
  });
  vi.spyOn(readiness, 'is_ready_for_worker_step').mockReturnValue(true);
  const gate = new Deferred<void>();
  const running = vi.spyOn(system, 'is_running').mockImplementation(async () => {
    await gate.promise;
    return false;
  });
  const loop = new WorkerStepReadinessLoop(options);
  try {
    expect(loop.poll_once()).toEqual([tickets[0]!.id, tickets[3]!.id, tickets[1]!.id]);
    await new Promise((resolve) => setImmediate(resolve));
    expect(running).toHaveBeenCalledTimes(3);
    expect(loop.poll_once()).toEqual([]);
    gate.resolve();
    await loop.drain();
    running.mockRestore();
    expect(loop.poll_once()[0]).toBe(tickets[3]!.id);
    await loop.drain();
  } finally {
    gate.resolve();
    await loop.stop({ deadline: performance.now() / 1000 + 1 });
  }
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
  await loops.stop({ deadline: performance.now() / 1000 + 1 });
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
  let stop: { deadline: number } | undefined;
  await app.register(
    readinessTestRoutes(() => {
      if (!stop) throw new Error('Isolated readiness host has not begun shutdown');
      return stop;
    }).routes,
    {
      database: db,
      config: loadConfig('/missing-worker-test-config', { PLAN_TEST_MODE: '1' }),
      clock: new TestClock('2026-07-04T10:00:00+00:00'),
      identity: () => ({ principal: OWNER, conversationId: null }),
    },
  );
  try {
    const result = await app.inject({ method: 'POST', url: '/readiness' });
    expect(result.statusCode).toBe(400);
    expect(result.json().message).toContain('require a test conversation system');
    expect(read(db, ticket.id).worker_step_claim).toBe('none');
  } finally {
    stop = { deadline: performance.now() / 1000 + 1 };
    await app.close();
  }
});

// Gap: the in-memory instrument writes no durable compaction events. Seed only the
// record facts that the scanner reads, then prove delivery into the running turn.
test('an unanswered compaction steers one notice and an uncertain notice prevents another automatic attempt', async () => {
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
  // Idle compaction carries memory on the next wake but never sends a continuation.
  expect(db.read((sql) => conversation_holds_an_unanswered_memory_loss(sql, id))).toBe(true);
  expect(loop.tell_every_worker_that_lost_its_memory()).toEqual([]);
  db.write((tx) =>
    tx.run(
      'UPDATE conversations SET working_turn_compacted_through_sequence=2 WHERE conversation_id=?',
      [id],
    ),
  );
  expect(loop.tell_every_worker_that_lost_its_memory()).toEqual([ticket.id]);
  await loop.drain();
  expect(await system.held_prompts(id)).toHaveLength(0);
  expect(system.backend_prompt_writes(id)).toHaveLength(2);
  expect(system.backend_prompt_writes(id)[1]).toMatchObject({
    mode: 'steer',
    content: expect.arrayContaining([
      expect.objectContaining({ text: expect.stringContaining('Your context was compacted') }),
    ]),
  });
  // The instrument does not persist delivered events, so seed its receipt for the scanner.
  db.write((tx) =>
    tx.run(
      'INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) VALUES (?,3,?,?,?)',
      [
        id,
        'prompt',
        JSON.stringify({ sender_message_id: 'worker_step_memory_notice_delivered' }),
        now,
      ],
    ),
  );
  expect(loop.tell_every_worker_that_lost_its_memory()).toEqual([]);
  expect(system.backend_prompt_writes(id)).toHaveLength(2);
  system.complete_running_turn(id);
  db.write((tx) =>
    tx.run(
      'INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) VALUES (?,4,?,?,?)',
      [
        id,
        'prompt_delivery_uncertain',
        JSON.stringify({ sender_message_id: 'worker_step_memory_notice_uncertain' }),
        now,
      ],
    ),
  );
  expect(loop.tell_every_worker_that_lost_its_memory()).toEqual([]);
  await loop.stop({ deadline: performance.now() / 1000 + 1 });
});

// Gap: normal drain cases cannot prove that a stalled provider permits loop shutdown.
test('loop stop aborts its observer immediately and retains raw start ownership past the deadline', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  const path = fixture_path(db) + '.hung-dispatcher.lock';
  const owner = acquireLock(path)!;
  let entered!: () => void;
  const sending = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const provider = new Deferred<PromptDeliveryStarted>();
  vi.spyOn(system, 'send').mockImplementation(() => {
    entered();
    return provider.promise;
  });
  const loop = new WorkerStepReadinessLoop(options);
  loop.poll_once();
  try {
    await sending;
    const departure = read(db, ticket.id);
    const abort = vi.spyOn(AbortController.prototype, 'abort');
    const stopping = loop.stop({ deadline: performance.now() / 1000 + 0.01 });
    expect(abort).toHaveBeenCalledOnce();
    await expect(stopping).rejects.toBeInstanceOf(ShutdownDeadlineExceeded);
    expect(acquireLock(path)).toBeNull();
    let actual_done = false;
    const actual = loop.settled().then(() => {
      actual_done = true;
    });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(actual_done).toBe(false);
    provider.resolve(new PromptDeliveryStarted());
    await actual;
    owner.close();
    const next = acquireLock(path);
    expect(next).not.toBeNull();
    next!.close();
    expect(read(db, ticket.id)).toMatchObject({
      conversation_id: departure.conversation_id,
      worker_step_claim: 'out',
      worker_step_claim_revision: departure.worker_step_claim_revision,
    });
    expect(loop.poll_once()).toEqual([]);
  } finally {
    provider.resolve(new PromptDeliveryStarted());
    await loop.stop({ deadline: performance.now() / 1000 + 1 }).catch((error: unknown) => {
      if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
    });
    await loop.settled();
    owner.close();
  }
});

// Gap: queue removal and durable receipt publication can race; the first lookup
// can miss the receipt even though releasing ownership has already published it.
// Reuse setup's in-memory backend and SQLite, with no new host/provider.
test('dequeued feedback rereads its receipt and late delivery unblocks the first failed decision', async () => {
  const { db, system, options } = setup();
  const ticket = create_ticket(db);
  await start_ready_worker_step(ticket.id, options);
  const current = read(db, ticket.id);
  const proposed = propose(db, current, 'Proposal');
  db.write((tx) =>
    data.reject_proposal(tx, ticket.id, {
      principal: OWNER,
      now,
      message: 'Revise.',
      decision_id: 'receipt-race',
      expected_proposal_revision: proposed.pending_proposal_revision,
    }),
  );
  db.write((tx) =>
    tx.run(
      "UPDATE ticket_decision_messages SET delivery_status='queued',conversation_id=? WHERE decision_id='receipt-race'",
      [current.conversation_id],
    ),
  );
  const receipt = db.read((sql) =>
    sql.get<{ sender_message_id: string; conversation_id: string }>(
      "SELECT sender_message_id,conversation_id FROM ticket_decision_messages WHERE decision_id='receipt-race'",
    ),
  )!;
  vi.spyOn(system, 'sender_message_ids_awaiting_outcome').mockImplementationOnce(async () => {
    db.write((tx) =>
      tx.run(
        "INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) SELECT ?,COALESCE(MAX(sequence),0)+1,'prompt',?,? FROM conversation_events WHERE conversation_id=?",
        [
          receipt.conversation_id,
          JSON.stringify({ sender_message_id: receipt.sender_message_id }),
          now,
          receipt.conversation_id,
        ],
      ),
    );
    return new Set();
  });
  await deliver_ticket_decision_messages(ticket.id, options);
  expect(
    db.read((sql) =>
      sql.get<{ delivery_status: string }>(
        "SELECT delivery_status FROM ticket_decision_messages WHERE decision_id='receipt-race'",
      ),
    )!.delivery_status,
  ).toBe('delivered');
  db.write((tx) =>
    tx.run(
      "UPDATE ticket_decision_messages SET delivery_status='uncertain' WHERE decision_id='receipt-race'",
    ),
  );
  expect(db.read(decisions.pending_ticket_ids)).toContain(ticket.id);
  await deliver_ticket_decision_messages(ticket.id, options);
  expect(db.read((sql) => decisions.has_undelivered(sql, ticket.id))).toBe(false);
});
