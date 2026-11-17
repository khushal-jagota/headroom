import * as remote from '../conversation/runtime/remote.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { WorkerStepReadinessLoop } from '../runtime/worker_step_readiness_loop.ts';
import { read_ticket } from '../tickets/data/index.ts';
/** Completion-to-prompt and admission races were absent from ordinary start tests.
 * Reuses the real-record scripted-child host; no new browser or backend host. */
import { expect, test, vi } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import { create, claim, read, propose, accept, now } from './ticket_fixture.ts';
import { worker_step_message_id_for_claim } from '../runtime/worker_memory.ts';
import {
  unsent_worker_continuation,
  admit_worker_continuation,
  settle_worker_continuations,
} from '../runtime/worker_continuation/index.ts';
import { continue_unfinished_worker_step } from '../runtime/worker_continuation/dispatch.ts';
import { CONTINUATION_PROMPT } from '../runtime/worker_continuation/logic/identity.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { PromptDispatchDecision } from '../conversation/contracts.ts';
import {
  PromptDeliverySuppressed,
  PromptDeliveryRefused,
  PromptDeliveryUncertain,
} from '../conversation/contracts.ts';

async function unfinished(ending: 'completed' | 'failed' = 'completed', clock?: () => number) {
  const f = setup({
    core: clock === undefined ? {} : { monotonic_now: clock },
    record: {
      admit_notice: (tx, conversation, identity, time) =>
        new PromptDispatchDecision({
          sender_message_id: identity,
          suppression_reason: admit_worker_continuation(tx, conversation, identity, time),
        }),
      event_participant(tx, conversation, payloads) {
        settle_worker_continuations(tx, conversation, payloads, now);
        return [];
      },
    },
  });
  await f.start();
  const t = create(f.database);
  f.database.write((tx) => {
    tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', ['core', t.id]);
    tx.run("UPDATE ticket_decision_messages SET delivery_status='delivered' WHERE ticket_id=?", [
      t.id,
    ]);
  });
  const claimed = claim(f.database, t.id)!;
  await f.send(
    'Finish this step',
    worker_step_message_id_for_claim(t.id, claimed.worker_step_claim_revision),
  );
  const child = await f.child;
  await child.finish({ ending });
  await f.core.wait_until_quiescent();
  const source = () =>
    f.database.read((sql) => unsent_worker_continuation(sql, read_ticket(sql, t.id)));
  const options = {
    connect_database: () => f.database,
    conversation_system: f.core,
    worker_type_registry: configured_worker_type_registry(),
    now: () => now,
  };
  return { ...f, t, child, source, options };
}

test('normal completion continues the same claim once through a real prompt', async () => {
  const f = await unfinished();
  const source = f.source()!;
  expect(source).not.toBeNull();
  const revision = read(f.database, f.t.id).worker_step_claim_revision;
  await Promise.all([
    continue_unfinished_worker_step(f.t.id, f.options),
    continue_unfinished_worker_step(f.t.id, f.options),
  ]);
  expect(
    f.store
      .read_events_after('core', 0)
      .filter(
        (row) =>
          row.kind === 'prompt' &&
          'sender_message_id' in row.payload &&
          row.payload.sender_message_id === source.sender_message_id,
      ),
  ).toHaveLength(1);
  expect(f.store.sender_message_outcome('core', source.sender_message_id)?.payload).toMatchObject({
    content: text_message_content(CONTINUATION_PROMPT),
  });
  expect(read(f.database, f.t.id).worker_step_claim_revision).toBe(revision);
  expect(f.source()).toBeNull();
  await f.child.finish();
  await f.core.wait_until_quiescent();
  expect(f.source()?.sender_message_id).not.toBe(source.sender_message_id);
});

test.each(['proposal', 'stage'] as const)(
  '%s before delivery suppresses the stale source without altering the new Ticket state',
  async (race) => {
    const f = await unfinished();
    const source = f.source()!;
    const proposed = propose(f.database, read(f.database, f.t.id), 'Review this result');
    if (race === 'stage') accept(f.database, proposed, 'what_changes');
    const before = read(f.database, f.t.id);
    expect(
      await f.core.send('core', text_message_content(CONTINUATION_PROMPT), {
        sender_label: 'loop',
        sender_message_id: source.sender_message_id,
        mode: 'queue',
      }),
    ).toBeInstanceOf(PromptDeliverySuppressed);
    expect(read(f.database, f.t.id)).toEqual(before);
    expect(f.source()).toBeNull();
  },
);

test('temporary delivery suppression retries from its durable receipt, and idle Stop fences that retry', async () => {
  const f = await unfinished();
  const source = f.source()!;
  const backlog = (value: boolean) =>
    f.database.write((tx) =>
      tx.run('UPDATE tickets SET is_backlogged=? WHERE id=?', [Number(value), f.t.id]),
    );
  backlog(true);
  expect(
    await f.core.send('core', text_message_content(CONTINUATION_PROMPT), {
      sender_label: 'loop',
      sender_message_id: source.sender_message_id,
      mode: 'queue',
    }),
  ).toBeInstanceOf(PromptDeliverySuppressed);
  backlog(false);
  const retry = f.source()!;
  expect(retry).not.toBeNull();
  expect(retry.sender_message_id).not.toBe(source.sender_message_id);
  expect(read(f.database, f.t.id).worker_step_claim).toBe('out');
  await f.core.interrupt('core');
  expect(f.source()).toBeNull();
  expect(
    await f.core.send('core', text_message_content(CONTINUATION_PROMPT), {
      sender_label: 'loop',
      sender_message_id: retry.sender_message_id,
      mode: 'queue',
    }),
  ).toBeInstanceOf(PromptDeliverySuppressed);
});

test('temporary suppressed continuation resumes after its gate clears', async () => {
  const f = await unfinished();
  const source = f.source()!;
  f.database.write((tx) => tx.run('UPDATE tickets SET is_backlogged=1 WHERE id=?', [f.t.id]));
  await f.core.send('core', text_message_content(CONTINUATION_PROMPT), {
    sender_label: 'loop',
    sender_message_id: source.sender_message_id,
    mode: 'queue',
  });
  f.database.write((tx) => tx.run('UPDATE tickets SET is_backlogged=0 WHERE id=?', [f.t.id]));
  expect((await continue_unfinished_worker_step(f.t.id, f.options))?.started).toBe(true);
  expect(await f.core.is_running('core')).toBe(true);
});

test('failed continuation delivery marks its matching claim errored', async () => {
  const f = await unfinished();
  vi.spyOn(f.core, 'send').mockResolvedValueOnce(
    new PromptDeliveryRefused({ refusal_reason: 'write_to_backend_failed' }),
  );
  await continue_unfinished_worker_step(f.t.id, f.options);
  expect(read(f.database, f.t.id).worker_step_claim).toBe('errored');
  expect(f.source()).toBeNull();
});

// These sources already ended: runtime/API start tests cannot prove that the
// completion reconciler excludes them while it retains the outstanding claim.
test('failed, interrupted, and user-owned turns do not become continuation sources', async () => {
  const f = await unfinished('failed');
  expect(f.source()).toBeNull();
  await f.send('Owner resumes work', 'owner-resume');
  await f.core.interrupt('core');
  expect(f.source()).toBeNull();
  await f.send('Owner resumes again', 'owner-resume-again');
  await f.child.finish();
  await f.core.wait_until_quiescent();
  expect(f.source()).not.toBeNull();
  f.database.write((tx) =>
    tx.run(
      "UPDATE tickets SET workflow_json=json_set(workflow_json,'$.stages[1].ownership_mode','user') WHERE id=?",
      [f.t.id],
    ),
  );
  expect(f.source()).toBeNull();
});

test('maintenance does not consume the unfinished work source or start a continuation chain', async () => {
  const f = await unfinished();
  const source = f.source()!;
  await f.core.send('core', text_message_content('/compact'), { sender_label: 'Panels' });
  await f.child.finish();
  await f.core.wait_until_quiescent();
  expect(f.source()?.sender_message_id).toBe(source.sender_message_id);
});

test.each(['uncertain', 'throw', 'stale-throw'] as const)(
  '%s delivery stays visible without poisoning a newer claim',
  async (outcome) => {
    const f = await unfinished();
    if (outcome === 'uncertain')
      vi.spyOn(f.core, 'send').mockResolvedValueOnce(new PromptDeliveryUncertain());
    else
      vi.spyOn(f.core, 'send').mockImplementationOnce(async () => {
        if (outcome === 'stale-throw') {
          const proposed = propose(f.database, read(f.database, f.t.id), 'Done');
          accept(f.database, proposed, 'what_changes');
          claim(f.database, f.t.id);
        }
        throw new Error('transport failed');
      });
    await continue_unfinished_worker_step(f.t.id, f.options);
    expect(read(f.database, f.t.id).worker_step_claim).toBe(
      outcome === 'stale-throw' ? 'out' : 'errored',
    );
  },
);

test('a continuation which leaves readiness gives up its deferred admission priority', async () => {
  let time = 0;
  const f = await unfinished('completed', () => time);
  const starts = f.core.start_admission;
  const source = f.source()!;
  expect(
    starts.try_start({ identity: 'occupant', work_class: 'worker', destination: HOME_MACHINE_ID }),
  ).toBe(true);
  expect((await continue_unfinished_worker_step(f.t.id, f.options))?.started).toBe(false);
  expect(
    starts.snapshots().some((entry) => entry.request.identity === source.sender_message_id),
  ).toBe(true);
  f.database.write((tx) => tx.run('UPDATE tickets SET is_backlogged=1 WHERE id=?', [f.t.id]));
  const loop = new WorkerStepReadinessLoop(f.options);
  expect(loop.poll_once()).toEqual([]);
  expect(
    starts.snapshots().some((entry) => entry.request.identity === source.sender_message_id),
  ).toBe(false);
  starts.finish('occupant');
  time = 60;
  expect(
    starts.try_start({
      identity: 'another-worker',
      work_class: 'worker',
      destination: HOME_MACHINE_ID,
    }),
  ).toBe(true);
});

test('an accepted deploy-recovery prompt authorizes only its restored claim revision', async () => {
  const f = await unfinished();
  const revision = read(f.database, f.t.id).worker_step_claim_revision;
  f.database.write((tx) => {
    tx.run(
      "UPDATE conversation_events SET payload=json_set(payload,'$.sender_message_id','recovery-message','$.sender_label','Panels') WHERE conversation_id='core' AND kind='prompt'",
    );
    tx.run(
      "INSERT INTO deploy_interruption_accounts VALUES ('capture','deploy',1,'captured',NULL)",
    );
    tx.run(
      "INSERT INTO deploy_interruptions VALUES ('interruption','capture','deploy','core',1,NULL,NULL,'Work','ticket',?,?,'running','closed','Recovered',1)",
      [f.t.id, revision - 2],
    );
    tx.run(
      "INSERT INTO deploy_interruption_deliveries VALUES ('interruption','core',1,'started',?,'recovery-message')",
      [revision + 1],
    );
  });
  expect(f.source()).toBeNull();
  f.database.write((tx) =>
    tx.run('UPDATE deploy_interruption_deliveries SET restored_claim_revision=?', [revision]),
  );
  expect(f.source()).not.toBeNull();
});

// Update retires the same child through the kill path, but it is not an owner Stop.
test.each(['kill', 'update'] as const)(
  '%s distinguishes explicit Stop from internal retirement',
  async (operation) => {
    const f = await unfinished();
    const source = f.source()!;
    if (operation === 'kill') await f.core.kill('core');
    else {
      expect(await f.core.prepare_update('now')).toBe(true);
      f.core.cancel_update();
    }
    expect(f.source()?.sender_message_id ?? null).toBe(
      operation === 'kill' ? null : source.sender_message_id,
    );
  },
);

// Remote withdrawal already proves its unavailable/suppressed receipts. This
// checks that the outer early return preserves an explicit Stop for old work.
test('explicit kill still vetoes continuation after initial-delivery withdrawal', async () => {
  const f = await unfinished();
  expect(f.source()).not.toBeNull();
  const withdrawal = vi
    .spyOn(remote, 'kill_initial_delivery')
    .mockImplementationOnce(async (host, state) => {
      if (state.child) await host.discard_child(state, state.child);
      return true;
    });
  await f.core.kill('core');
  expect(withdrawal).toHaveBeenCalledOnce();
  expect(f.source()).toBeNull();
});
