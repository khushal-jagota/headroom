import { expect, test } from 'vitest';
import { setup } from '../../tests/conversation_core_fixture.ts';
import { SignalPublicationCoordinator } from '../publication/coordinator.ts';
import * as persistence from '../publication/persistence.ts';
import { InboxStore } from '../../executor/data/inbox.ts';
import { FileStaging } from '../../executor/vps_files.ts';
import { add_inbox_presence_table } from '../../executor/testing/inbox_fixture.ts';
import { PlannerError } from '../../core/contracts.ts';
import { as_object } from '../../executor/protocol.ts';
import { TurnEnded } from '../backends/contracts.ts';
import type { WireValue } from '../../executor/contracts.ts';

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
  add_inbox_presence_table(f.database);
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
