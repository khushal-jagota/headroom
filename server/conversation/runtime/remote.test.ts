import { expect, test, vi } from 'vitest';
import { setup } from '../../tests/conversation_core_fixture.ts';
import { SignalPublicationCoordinator } from '../publication/coordinator.ts';
import * as persistence from '../publication/persistence.ts';
import { InboxStore } from '../../executor/data/inbox.ts';
import { FileStaging } from '../../executor/vps_files.ts';
import { PlannerError } from '../../core/contracts.ts';
import { as_object } from '../../executor/protocol.ts';
import {
  BackendPermissionAsk,
  BackendPromptAccepted,
  BackendUserInputRequest,
  PermissionAskRaised,
  TurnEnded,
  UserInputRequested,
} from '../backends/contracts.ts';
import type { WireValue } from '../../executor/contracts.ts';
import { text_message_content } from '../message_content.ts';
import { Deferred } from './state.ts';

// The SQL-only inbox proof cannot detect predictions or a global Signal batch
// that escapes before a permanent refusal receipt commits through the real core.
test('a refused remote take restores runtime before the global finalizer and publishes no event', async () => {
  let producer: SignalPublicationCoordinator | undefined;
  let batches = 0,
    published = 0;
  const f = setup({
    database: { commitSteps: [(tx) => producer?.finalize_signal(tx)] },
    core: {
      save_attempt: (id, work, mutations) => producer!.save_attempt(id, work, mutations),
      publish_event: () => {
        published++;
      },
    },
  });
  producer = new SignalPublicationCoordinator({
    database: f.database,
    require_runtime_ownership: () => {},
  });
  producer.bind_runtime((id) => f.core.runtime_turn(id));
  producer.initialize();
  const attachment = producer.attach_signal_publication({
    install: () => {},
    participate: () => {
      batches++;
    },
    unavailable: () => {},
  });
  const identity = {
    executor_incarnation: 'remote',
    protocol_version: 22,
    spool_format_version: 5,
    application_revision: 'fixture',
    connection_generation: 0,
  };
  f.database.write((tx) =>
    tx.run('INSERT INTO mac_executor_presence(singleton,executor_incarnation) VALUES(1,?)', [
      identity.executor_incarnation,
    ]),
  );
  try {
    await f.start();
    await f.send();
    const running = f.core.runtime_turn('core')!;
    const snapshot = () => ({
      ...f.database.read((sql) => ({
        cursor: persistence.cursor(sql),
        events: sql.all('SELECT * FROM conversation_events ORDER BY sequence'),
      })),
      facts: f.store.lifecycle_facts('core'),
    });
    const before = snapshot();
    const before_publications = { batches, published };
    const envelope: WireValue<'QueuedEnvelope'> = {
      conversation_id: 'core',
      sender_id: 'sender',
      submission_id: 'refused-end',
      sequence: 1,
      kind: 'backend_event',
      payload: {},
      file_dependencies: [],
    };
    const inbox = new InboxStore(f.database, new FileStaging(f.database_path));
    const result = await f.core.take_backend_event(
      running.token,
      new TurnEnded({ ending: 'completed' }),
      {
        in_commit: (tx, take) => {
          const [receipt, taken] = inbox.accept_in_transaction(
            tx,
            envelope,
            () => {
              take();
              throw new PlannerError('validation', 'permanent refusal');
            },
            100,
            { identity },
          );
          return { result: as_object(receipt, 'AcceptanceReceipt'), taken, written: [] };
        },
      },
    );
    expect(result.taken).toBe(false);
    expect(result.result.disposition).toBe('refused');
    expect(f.core.runtime_turn('core')).toBe(running);
    expect(snapshot()).toEqual(before);
    expect(f.database.read((sql) => sql.all('SELECT * FROM signal_publication_dirty'))).toEqual([]);
    expect({ batches, published }).toEqual(before_publications);
    expect(
      f.database.read((sql) =>
        sql.get<{ disposition: string }>('SELECT disposition FROM mac_inbox_positions'),
      )?.disposition,
    ).toBe('refused');
  } finally {
    attachment.close();
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});

// A cancelled inbox wait must leave no queued lock owner that blocks the retry.
test('a remote event that times out behind earlier work leaves the retry its lock', async () => {
  const f = setup();
  try {
    await f.start();
    await f.send();
    const state = f.core.conversations.get('core')!,
      token = state.running_turn!.token;
    const release = await state.lock.acquire();
    try {
      await expect(
        f.core.take_backend_event(token, new TurnEnded({ ending: 'completed' }), {
          retry_later_at: performance.now() / 1000 - 1,
        }),
      ).rejects.toMatchObject({ code: 'retry_later' });
      expect(f.core.runtime_turn('core')?.token).toEqual(token);
    } finally {
      release();
    }
    const accepted = await f.core.take_backend_event(token, new TurnEnded({ ending: 'completed' }));
    expect(accepted.taken).toBe(true);
    expect(f.core.runtime_turn('core')).toBeNull();
    expect(
      f.store.read_events_after('core', 0).filter((row) => row.kind === 'turn_ended'),
    ).toHaveLength(1);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});

// Reuse the existing real core/record/process fixture; hold only its native replay reply.
// Earlier receipt tests do not kill an already-recorded turn during a replay write.
test('kill during replay ends its turn and asks before a late successful reply', async () => {
  const close_rewritten = vi.fn();
  const f = setup({
    core: {
      remote_recovery: {
        retire_replaced() {},
        close_rewritten,
        async recover() {},
      },
    },
  });
  const entered = new Deferred<void>(),
    release = new Deferred<void>();
  let replay: Promise<void> | undefined;
  try {
    await f.start();
    await f.send();
    const child = await f.child,
      state = f.core.conversation_state('core')!,
      token = state.running_turn!.token;
    const reference = {
      conversation_id: 'core',
      executor_incarnation: 'remote',
      child_id: 'replaying',
    };
    state.retained_child_reference = reference;
    Object.defineProperty(child, 'stop_outside_lock', { value: true });
    vi.spyOn(child, 'withdraw_delivery').mockResolvedValue(false);
    vi.spyOn(child, 'stop').mockResolvedValue();
    const write = vi.spyOn(child, 'write_prompt').mockImplementation(async () => {
      entered.resolve();
      await release.promise;
      return new BackendPromptAccepted({ composed_content_delivered: true });
    });
    await f.core.take_backend_event(
      token,
      new PermissionAskRaised({
        ask: new BackendPermissionAsk({
          ask_id: 'permission',
          title: 'Permission',
          detail: null,
          options: [],
        }),
      }),
    );
    await f.core.take_backend_event(
      token,
      new UserInputRequested({
        request: new BackendUserInputRequest({ request_id: 'input', questions: [] }),
      }),
    );
    const content = text_message_content('original');
    replay = f.core.resume_undelivered_prompt(
      reference,
      {
        turn_number: token.turn_number,
        content,
        sender_content: content,
        sender_label: 'Owner',
        sender_message_count: 1,
        mode: 'queue',
        model_change: null,
        reasoning_effort_change: null,
        reasoning_effort_is_set: false,
        automatic_compaction: false,
      },
      { start_needed: false },
    );
    await entered.promise;
    expect(state.reserved_turn?.token).toEqual(token);
    const prompt_fact = f.store.lifecycle_facts('core').find((fact) => fact.operation === 'prompt');
    await f.core.kill('core');
    expect(state.running_turn).toBeNull();
    const requests = f.store.lifecycle_facts('core').filter((fact) => fact.kind === 'request');
    expect(requests.map((fact) => fact.status)).toEqual(['unavailable', 'unavailable']);
    const endings = f.store.read_events_after('core', 0).filter((row) => row.kind === 'turn_ended');
    expect(endings).toHaveLength(1);
    expect(endings[0]!.payload).toMatchObject({ ending: 'interrupted' });
    expect(f.store.lifecycle_facts('core').find((fact) => fact.operation === 'prompt')).toEqual(
      prompt_fact,
    );
    release.resolve();
    await replay;
    expect(close_rewritten).toHaveBeenCalledExactlyOnceWith(
      'core',
      token.turn_number,
      'The turn ended before its message was written again.',
    );
    expect(write).toHaveBeenCalledTimes(1);
    expect(f.children).toHaveLength(1);
  } finally {
    release.resolve();
    await replay;
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});
