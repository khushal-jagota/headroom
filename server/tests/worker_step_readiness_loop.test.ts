import { setup as core_setup } from './conversation_core_fixture.ts';
import { send_message } from '../message_delivery/service.ts';
import { MessageRecordedToOwner } from '../message_delivery/contracts.ts';
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
import { apply as apply_decision } from '../tickets/decision_operations.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { deliver_ticket_decision_messages } from '../runtime/decision_message_delivery.ts';
import { conversation_holds_an_unanswered_memory_loss } from '../runtime/worker_memory.ts';
import { restart_worker } from '../tickets/worker_restart.ts';
import { OWNER, PlannerError } from '../core/contracts.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import {
  PromptDeliveryQueued,
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
import { Deferred } from '../core/deferred.ts';
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
  const send = system.send.bind(system);
  vi.spyOn(system, 'send').mockImplementation(async (id, content, options) => {
    if (options.recording_receipt?.in_admission) {
      db.write((tx) => options.recording_receipt!.in_admission!(tx, id));
      options.recording_receipt.admitted?.();
    }
    return send(id, content, options);
  });
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

test('restart defers the next opener and only one starts a fresh conversation', async () => {
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
    expect(next_settled).toBe(true);
    await expect(
      send_message(
        system,
        db,
        new TestClock(new Date(now * 1000).toISOString()),
        { principal: OWNER, conversationId: null },
        { kind: 'ticket', id: ticket.id },
        'stale correction',
        'steer',
      ),
    ).rejects.toMatchObject({ code: 'validation' });
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
    message: 'Waiting for Mac: restart this Ticket once it is back',
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
    if (sent.recording_receipt?.in_admission) {
      db.write((tx) => sent.recording_receipt!.in_admission!(tx, _id));
      sent.recording_receipt.admitted?.();
    }
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
test('an occupied conversation queues one claimed step without a preparation permit', async () => {
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
  const prepare = vi.spyOn(starts, 'try_start');
  expect((await start_ready_worker_step(ticket.id, options)).delivery_fate).toBe('queued');
  expect(prepare).not.toHaveBeenCalled();
  expect(starts.snapshots()).toEqual([
    expect.objectContaining({ state: 'queued', permit_identity: null }),
  ]);
  expect(read(db, ticket.id).worker_step_claim).toBe('out');
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(false);
  expect(await system.held_prompts(taken.conversation_id!)).toHaveLength(1);
  expect(system.backend_prompt_writes(taken.conversation_id!)).toHaveLength(1);
  system.complete_running_turn(taken.conversation_id!);
  expect(system.backend_prompt_writes(taken.conversation_id!)).toHaveLength(2);
  expect(await system.held_prompts(taken.conversation_id!)).toHaveLength(0);
  expect(read(db, ticket.id).worker_step_claim).toBe('out');
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
// Gap: decision tests prove note text reaches the prompt, and nothing proves a picture sent
// with a decision does. This crosses the decision command, its saved operation, and the file store.
test('a picture sent with a decision reaches the Worker prompt', async () => {
  const { db, system, options } = setup();
  const ticket = create_ticket(db);
  const files = { ...options, message_files: new ConversationMessageFiles(fixture_path(db)) };
  await start_ready_worker_step(ticket.id, files);
  const id = read(db, ticket.id).conversation_id!;
  const proposed = propose(db, read(db, ticket.id), 'Revise this proposal.');
  const png =
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
  db.write((tx) =>
    apply_decision(
      tx,
      ticket.id,
      'op-with-picture',
      {
        decision: 'send_back',
        field: 'what_changes',
        expected_proposal_revision: proposed.pending_proposal_revision,
        message: 'See the screenshot.',
        attachments: [
          { piece: 'image', data: png, media_type: 'image/png', file_name: 'shot.png' },
        ],
      },
      OWNER,
      now,
    ),
  );
  await start_ready_worker_step(ticket.id, files);
  const held = (await system.held_prompts(id))[0]!.content;
  expect(held).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ text: expect.stringContaining('See the screenshot.') }),
      expect.objectContaining({ media_type: 'image/png', file_name: 'shot.png' }),
    ]),
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
// Reuse their in-memory conversations and SQLite source to prove that unrelated
// Tickets remain eligible without weakening per-Ticket task ownership.
test('blocked preparations do not cap hand-overs and retain per-Ticket ownership', async () => {
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
    expect(loop.poll_once()).toEqual([
      tickets[0]!.id,
      tickets[3]!.id,
      tickets[1]!.id,
      tickets[2]!.id,
    ]);
    await new Promise((resolve) => setImmediate(resolve));
    expect(running).toHaveBeenCalledTimes(4);
    expect(loop.poll_once()).toEqual([]);
    gate.resolve();
    await loop.drain();
    running.mockRestore();
    expect(loop.poll_once()).toContain(tickets[3]!.id);
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

// Gap: a saved upload token is not delivery. This reuses the existing Worker prompt fixture,
// streams video and binary bytes, pins the accepted decision beyond TTL, then reads the actual prompt files.
test('an accepted decision retains streamed files through refusal, TTL, and a fresh Worker retry', async () => {
  const { VideoUploadService } = await import('../conversation/video_uploads/service.ts');
  const { readFileSync } = await import('node:fs');
  const { db, system, options } = setup();
  const ticket = create_ticket(db);
  let upload_now = now;
  const uploads = new VideoUploadService(db, fixture_path(db), {
    now: () => upload_now,
    disk_free_bytes: () => 20 * 1024 ** 3,
  });
  const files = {
    ...options,
    message_files: new ConversationMessageFiles(fixture_path(db)),
    upload_service: uploads,
  };
  await start_ready_worker_step(ticket.id, files);
  const id = read(db, ticket.id).conversation_id!;
  system.complete_running_turn(id);
  const proposed = propose(db, read(db, ticket.id), 'Revise the attachment surface.');
  const { create_video_fixture } = await import('../conversation/testing/video_fixture.ts');
  const video = create_video_fixture(dirname(fixture_path(db)));
  const expected = new Map([
    ['proof.mp4', readFileSync(video)],
    ['proof.bin', Buffer.from([0, 255, 7, 6, 5])],
  ]);
  const refs: { piece: 'attachment_upload'; upload_id: string }[] = [];
  for (const [filename, bytes] of expected) {
    const upload = uploads.reserve({
      destination: { kind: 'ticket', id: ticket.id, expected_conversation_id: id },
      filename,
      byte_count: bytes.length,
    });
    await uploads.transfer(
      upload.upload_id,
      (async function* () {
        yield bytes;
      })(),
    );
    refs.push({ piece: 'attachment_upload', upload_id: upload.upload_id });
  }
  const command = {
    decision: 'send_back',
    field: 'what_changes',
    expected_proposal_revision: proposed.pending_proposal_revision,
    message: 'Use these files.',
    attachments: refs,
  };
  const first = db.write((tx) =>
    apply_decision(tx, ticket.id, 'op-streams', command, OWNER, now, uploads),
  );
  expect(first.receipt?.outcome).toBe('applied');
  expect(
    db.write((tx) => apply_decision(tx, ticket.id, 'op-streams', command, OWNER, now, uploads)),
  ).toEqual(first);
  system.arm_backend_write_failure(id);
  const refused = await start_ready_worker_step(ticket.id, files);
  expect(refused.delivery_fate).toBe('refused');
  for (const ref of refs) {
    const retained = db.read((sql) =>
      sql.get<{ state: string; sender_message_id: string; conversation_id: null }>(
        'SELECT state,sender_message_id,conversation_id FROM conversation_video_uploads WHERE upload_id=?',
        [ref.upload_id],
      ),
    );
    expect(retained).toEqual({
      state: 'admitted',
      sender_message_id: 'decision-upload:op-streams',
      conversation_id: null,
    });
  }
  upload_now += 48 * 60 * 60;
  uploads.recover_and_cleanup();
  for (const ref of refs) expect(uploads.status(ref.upload_id).state).toBe('admitted');
  await restart_worker(system, db, OWNER, ticket.id, {
    now: () => now,
    start_worker_step: () => start_ready_worker_step(ticket.id, files),
  });
  const retried_id = read(db, ticket.id).conversation_id!;
  expect(retried_id).not.toBe(id);
  const prompt = system.backend_prompt_writes(retried_id).at(-1)!.content;
  for (const [filename, bytes] of expected) {
    const piece = prompt.find((piece) => 'file_name' in piece && piece.file_name === filename)!;
    expect(piece).toBeDefined();
    if (!('stored_file_id' in piece))
      throw new Error('Worker prompt omitted the managed file reference');
    expect(readFileSync(files.message_files.path_of(retried_id, piece.stored_file_id))).toEqual(
      bytes,
    );
  }
  for (const ref of refs) expect(uploads.status(ref.upload_id).state).toBe('linked');
});

// Gap: the reference system proves claim rules but cannot hold a native receipt.
// Reuse the real-record/scripted-process fixture to race kickoff with a correction.
test('a kickoff racing a steer shares admission order without releasing its claim at acceptance', async () => {
  const f = core_setup({ behavior: { barrier: 'kickoff' } });
  configure_conversation_start(f.database, {
    db_path: f.database_path,
    workspace_root: dirname(f.database_path),
  });
  const ticket = create(f.database);
  const options = {
    connect_database: () => f.database,
    conversation_system: f.core,
    worker_type_registry: configured_worker_type_registry(),
    now: () => now,
  };
  let finished = false;
  const kickoff = start_ready_worker_step(ticket.id, options).then((result) => {
    finished = true;
    return result;
  });
  const child = await f.child;
  await child.wait_for_barrier('kickoff');
  const accepted = new Deferred<void>();
  const correction = send_message(
    f.core,
    f.database,
    new TestClock(new Date(now * 1000).toISOString()),
    { principal: OWNER, conversationId: null },
    { kind: 'ticket', id: ticket.id },
    'correction',
    'steer',
    {
      sender_message_id: 'correction',
      recording_receipt: {
        sender_id: '',
        submission_id: 'correction',
        admitted: () => accepted.resolve(),
      },
    },
  );
  try {
    await accepted.promise;
    expect(finished).toBe(false);
    expect(read(f.database, ticket.id).worker_step_claim).toBe('out');
    expect(
      f.database
        .read((sql) =>
          sql.all<{ payload: string }>(
            'SELECT payload FROM recipient_deliveries ORDER BY delivery_order',
          ),
        )
        .map((row) => JSON.parse(row.payload).sender_message_id),
    ).toEqual([expect.stringMatching(/^worker_step_message_/), 'correction']);
    expect((await start_ready_worker_step(ticket.id, options)).started).toBe(false);
    await child.release('kickoff');
    expect((await kickoff).started).toBe(true);
    await correction;
    expect((await child.inspect()).calls.filter((call) => call.command === 'steer')).toMatchObject([
      { arguments: { sender_content: [{ piece: 'text', text: 'correction' }] } },
    ]);
  } finally {
    await child.release('kickoff');
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// Gap: no case held a retired workspace's cleanup while the same sender kept writing.
// Reuse the real core: the triggering message takes durable custody before cleanup, and the
// sender's owner reply proceeds while a hung kill waits. Failure or an active turn refuses once.
test.each(['cleaned', 'failed', 'active'] as const)(
  'a message to a retired workspace is accepted before cleanup when cleanup is %s',
  async (cleanup) => {
    const f = core_setup();
    configure_conversation_start(f.database, {
      db_path: f.database_path,
      workspace_root: dirname(f.database_path),
    });
    const db = f.database;
    const options = {
      connect_database: () => db,
      conversation_system: f.core,
      worker_type_registry: configured_worker_type_registry(),
      now: () => now,
    };
    const ended = create(db);
    await start_ready_worker_step(ended.id, options);
    // The Chief stands in for any agent sender: its later reply goes to the owner.
    await f.start();
    db.write((tx) =>
      tx.run('INSERT INTO agents(agent_key,conversation_id) VALUES(?,?)', [
        'chief_of_staff',
        'core',
      ]),
    );
    const old_id = read(db, ended.id).conversation_id!;
    await (await f.child).finish();
    await vi.waitFor(async () => expect(await f.core.is_running(old_id)).toBe(false));
    db.write((tx) => {
      tx.run("UPDATE tickets SET stage='done' WHERE id=?", [ended.id]);
      tx.run('UPDATE ticket_workspace_lifecycle SET ended_at=? WHERE ticket_id=?', [now, ended.id]);
      tx.run('INSERT INTO ticket_workspace_conversations VALUES(?,?,?,?)', [
        old_id,
        ended.id,
        HOME_MACHINE_ID,
        'retired-workspace',
      ]);
      tx.run('INSERT INTO ticket_workspace_retirements VALUES(?,?,?,?)', [
        ended.id,
        HOME_MACHINE_ID,
        'retired-workspace',
        now,
      ]);
    });
    const release = new Deferred<void>();
    const killing = new Deferred<void>();
    const is_running = f.core.is_running.bind(f.core);
    vi.spyOn(f.core, 'is_running').mockImplementation(async (id) =>
      id === old_id && cleanup === 'active' ? true : is_running(id),
    );
    const kill = f.core.kill.bind(f.core);
    const killed = vi.spyOn(f.core, 'kill').mockImplementation(async (id) => {
      if (id === old_id) {
        killing.resolve();
        await release.promise;
        if (cleanup === 'failed') throw new Error('cleanup proof unavailable');
      }
      await kill(id);
    });
    const clock = new TestClock(new Date(now * 1000).toISOString());
    const as_sender = { principal: { kind: 'chief' as const, id: 'chief' }, conversationId: null };
    const custody: { conversation_id: string; delivery_order?: number }[] = [];
    const accepted = await send_message(
      f.core,
      db,
      clock,
      as_sender,
      { kind: 'ticket', id: ended.id },
      'correction',
      'queue',
      {
        sender_message_id: 'correction',
        recording_receipt: {
          sender_id: 'sender',
          submission_id: 'correction',
          in_admission: (tx, id) =>
            custody.push({
              conversation_id: id,
              delivery_order: tx.get<{ delivery_order: number }>(
                'SELECT delivery_order FROM recipient_deliveries WHERE submission_id=?',
                [`${id}:correction`],
              )?.delivery_order,
            }),
        },
      },
    );
    const journal = () =>
      db.read((sql) =>
        sql.all<{ delivery_order: number; conversation_id: string; state: string }>(
          "SELECT delivery_order,conversation_id,state FROM recipient_deliveries WHERE json_extract(payload,'$.sender_message_id')='correction'",
        ),
      );
    const lifecycle = () =>
      db.read(
        (sql) =>
          sql.get<{ retiring: number }>(
            "SELECT retiring FROM recipient_lifecycles WHERE recipient_kind='ticket' AND recipient_id=?",
            [ended.id],
          )?.retiring,
      );
    const refusals = () =>
      f.store
        .read_events_after(old_id, 0)
        .filter(
          (row) =>
            row.kind === 'prompt_delivery_refused' &&
            (row.payload as { sender_message_id?: string }).sender_message_id === 'correction',
        );
    try {
      expect(accepted.fate).toBeInstanceOf(PromptDeliveryQueued);
      // Receipts and owner status name the fresh conversation that will receive the message.
      const fresh = accepted.conversation_id!;
      expect(fresh).not.toBe(old_id);
      expect(custody).toEqual([{ conversation_id: fresh, delivery_order: expect.any(Number) }]);
      const held = (state: string) => [
        { delivery_order: custody[0].delivery_order, conversation_id: fresh, state },
      ];
      if (cleanup === 'active') {
        // A running turn is never interrupted, and nothing was cleaned, so the fence lifts.
        await vi.waitFor(() => expect(refusals()).toHaveLength(1));
        expect(killed).not.toHaveBeenCalledWith(old_id);
        expect(journal()).toEqual(held('refused'));
        expect(lifecycle()).toBe(0);
        expect(read(db, ended.id).conversation_id).toBe(old_id);
        return;
      }
      await killing.promise;
      expect(journal()).toEqual(held('awaiting_lifecycle'));
      expect(
        await f.core.owner_send_progress(fresh, 'correction', { kind: 'ticket', id: ended.id }),
      ).toEqual({ state: 'queued', position: 1 });
      await expect(
        send_message(f.core, db, clock, as_sender, OWNER, 'owner reply'),
      ).resolves.toMatchObject({ fate: { constructor: MessageRecordedToOwner } });
      expect(read(db, ended.id).conversation_id).toBe(old_id);
      release.resolve();
      if (cleanup === 'failed') {
        await vi.waitFor(() => expect(refusals()).toHaveLength(1));
        expect(journal()).toEqual(held('refused'));
        expect(lifecycle()).toBe(1);
        expect(read(db, ended.id).conversation_id).toBe(old_id);
        return;
      }
      await vi.waitFor(() => expect(journal()).toEqual(held('delivered')));
      expect(read(db, ended.id).conversation_id).toBe(fresh);
      expect(lifecycle()).toBe(0);
      const first_prompt = f.store.read_events_after(fresh, 0).find((row) => row.kind === 'prompt');
      expect(first_prompt?.payload).toMatchObject({ sender_message_id: 'correction' });
      expect(refusals()).toEqual([]);
    } finally {
      release.resolve();
      await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
    }
  },
);
