/** Risk: a native operation holds both locks while an agent replies to the owner.
 * Existing acceptance tests prove receipt custody, but do not exercise these runtime locks.
 * Reuse the core fixture and real event store, with no browser, listener, or backend process.
 */
import { expect, test } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import { send_message, sender_conversation_link_key } from '../message_delivery/service.ts';
import { conversation_link_lock } from '../runtime/conversation_start.ts';
import { CHIEF_SETTINGS_KEY } from '../worker_settings/service.ts';
import { text_message_content } from '../conversation/message_content.ts';
import type { NativeMessageOperation } from '../conversation/contracts.ts';
import type { StoredConversationEvent } from '../conversation/record/contracts.ts';
import type { Clock } from '../core/clock.ts';
import {
  conversation_attention_snapshot,
  advance_conversation_attention,
  capture_conversation_attention,
  conversation_attention,
} from '../notifications/attention.ts';
import { read_row } from '../conversation/views/data/storage.ts';
import { Deferred } from '../core/deferred.ts';
import { AgentWrites } from '../executor/vps_agent_writes.ts';
import { InboxStore } from '../executor/data/inbox.ts';
import { ORIGINAL_MAC_MACHINE_ID, type ExecutorIdentity } from '../execution_identity.ts';
import { PROTOCOL_VERSION } from '../execution_configuration.ts';
import type { FileStaging } from '../executor/vps_files.ts';
import type { QueuedEnvelope } from '../executor/wire.generated.ts';
import { read_agent_message } from '../message_delivery/data/agent_acceptances.ts';
import { MessageToOwnerEventPayload } from '../conversation/events.ts';

const clock = { nowUnix: () => 100 } as Clock;
const sender = { kind: 'chief', id: 'chief' } as const;
const recipient = { kind: 'owner', id: 'owner' } as const;

test('owner custody records and publishes once while native and conversation-link work waits', async () => {
  const published: StoredConversationEvent[] = [];
  const f = setup({ core: { publish_event: (event) => published.push(event) } });
  await f.start();
  f.database.write((tx) =>
    tx.run('INSERT INTO agents(agent_key,conversation_id) VALUES (?,?)', [
      CHIEF_SETTINGS_KEY,
      'core',
    ]),
  );
  const state = f.core.conversation_state('core')!;
  const release = new Deferred<void>();
  const entered = new Deferred<void>();
  const native = conversation_link_lock(sender_conversation_link_key(f.database, sender)).run(() =>
    state.lock.run(async () => {
      entered.resolve();
      await release.promise;
    }),
  );
  await entered.promise;
  let commits = 0;
  const recording_receipt: NativeMessageOperation = {
    sender_id: 'source',
    submission_id: 'reply',
    in_commit(tx, conversation_id) {
      // The receipt participant sees the event inside its local transaction.
      expect(
        tx.get<{ count: number }>(
          "SELECT COUNT(*) AS count FROM conversation_events WHERE conversation_id=? AND kind='message_to_owner'",
          [conversation_id],
        )?.count,
      ).toBe(1);
      commits += 1;
    },
  };
  const send = (text = 'Ready for review') =>
    send_message(
      f.core,
      f.database,
      clock,
      { principal: sender, conversationId: 'core' },
      recipient,
      text,
      'queue',
      { sender_message_id: 'reply', recording_receipt },
    );
  try {
    const first = send();
    // The lock holder remains blocked until finally. A queued native path cannot pass this check.
    await expect(first).resolves.toMatchObject({ conversation_id: 'core', recipient });
    expect(commits).toBe(1);
    expect(published.map((event) => event.kind)).toEqual(['message_to_owner']);
    expect(f.store.read_conversation('core')!.latest_sequence).toBe(1);
    expect(state.record.latest_sequence).toBe(1);
    await send();
    expect(commits).toBe(2);
    expect(published).toHaveLength(1);
    await expect(send('Different content')).rejects.toThrow('different message');
    expect(f.store.read_events_after('core', 0)).toHaveLength(1);
    expect(state.save_failure).toBeNull();

    // The older native save committed, but its asynchronous completion did not publish yet.
    const older = f.store.append_events('core', [
      new MessageToOwnerEventPayload({
        content: text_message_content('Older native save'),
        sender_label: 'Chief',
        sender,
        recipient,
        sender_message_id: 'older',
      }),
    ]);
    await f.core.record_message_to_owner('core', text_message_content('Second'), {
      sender_label: 'Chief',
      sender,
      recipient,
      sender_message_id: 'second',
      recording_receipt: { sender_id: 'source', submission_id: 'second' },
    });
    expect(published.map((event) => event.sequence)).toEqual([1, 2, 3]);
    f.core.note_written(state, older);
    expect(published.map((event) => event.sequence)).toEqual([1, 2, 3]);
    expect(state.record.latest_sequence).toBe(3);
  } finally {
    release.resolve();
    await native;
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});

// Distinct boundary: the real executor receipt participant must roll back together with the
// owner event, then return the original durable receipt after a successful commit and replay.
test('owner event, acceptance, and sender advance commit or roll back together', async () => {
  let fail_commit = false;
  const published: StoredConversationEvent[] = [];
  const f = setup({
    record: {
      prepare_attention(tx, conversation_id) {
        const snapshot = conversation_attention_snapshot(tx, conversation_id);
        if (snapshot === null) return null;
        return (source, change) => {
          const attention = advance_conversation_attention(source, snapshot, change.written, {
            owner_read_through_sequence: change.owner_read_through_sequence,
            occurred_at: change.occurred_at,
          });
          capture_conversation_attention(source, conversation_id, change.occurred_at, attention);
        };
      },
    },
    database: {
      commitSteps: [
        () => {
          if (fail_commit) throw Object.assign(new Error('Disk full'), { code: 'SQLITE_FULL' });
        },
      ],
    },
    core: { publish_event: (event) => published.push(event) },
  });
  await f.start();
  const identity: ExecutorIdentity = {
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
    application_revision: 'test',
    protocol_version: PROTOCOL_VERSION,
    spool_format_version: 5,
    connection_generation: 0,
  };
  const child = {
    executor_incarnation: identity.executor_incarnation,
    child_id: 'chief-child',
    conversation_id: 'core',
  };
  f.database.write((tx) => {
    tx.run('INSERT INTO agents(agent_key,conversation_id) VALUES (?,?)', [
      CHIEF_SETTINGS_KEY,
      'core',
    ]);
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
      [child.executor_incarnation, child.child_id, child.conversation_id, sender.kind, sender.id],
    );
  });
  const inbox = new InboxStore(f.database, { machine_id: ORIGINAL_MAC_MACHINE_ID } as FileStaging);
  const writes = new AgentWrites(inbox, clock, {
    authority: () => sender,
    validate_stop() {},
    async accept_stop_receipt() {},
  });
  writes.conversations = f.core;
  const envelope: QueuedEnvelope = {
    conversation_id: 'core',
    kind: 'cli_write',
    sender_id: 'sender',
    sequence: 1,
    submission_id: 'atomic-reply',
    file_dependencies: [],
    payload: {
      child,
      operation: 'message.send',
      arguments: { body: { target: recipient, message: 'Complete' } },
      turn: null,
    },
  };
  const send = () => writes.accept(envelope, identity, async () => null);
  const acceptance = () =>
    f.database.read((sql) => read_agent_message(sql, envelope.submission_id));
  try {
    fail_commit = true;
    await expect(send()).rejects.toThrow('Disk full');
    expect(acceptance()).toBeNull();
    expect(f.store.read_events_after('core', 0)).toEqual([]);
    expect(f.database.read((sql) => sql.get('SELECT * FROM mac_inbox_positions'))).toBeUndefined();
    expect(published).toEqual([]);
    expect(
      f.database.read((sql) => conversation_attention(sql, 'core').get('core')!.unread_message),
    ).toBe(false);
    expect(
      f.database.read((sql) =>
        sql.all(
          "SELECT * FROM notification_attention_edges WHERE notification_type='awaiting_reply'",
        ),
      ),
    ).toEqual([]);
    expect(
      f.database.read((sql) =>
        sql.get("SELECT * FROM conversation_view_rows WHERE conversation_id='core'"),
      ),
    ).toBeUndefined();
    expect(f.core.conversation_state('core')!.save_failure).toBeNull();
    fail_commit = false;
    const receipt = await send();
    expect(receipt.disposition).toBe('accepted');
    expect(acceptance()?.receipt).toEqual(receipt);
    expect(await send()).toEqual(receipt);
    expect(f.store.read_events_after('core', 0)).toHaveLength(1);
    expect(published).toHaveLength(1);
    expect(
      f.database.read((sql) => conversation_attention(sql, 'core').get('core')!.unread_message),
    ).toBe(true);
    expect(
      f.database.read((sql) =>
        sql.all(
          "SELECT * FROM notification_attention_edges WHERE notification_type='awaiting_reply'",
        ),
      ),
    ).toHaveLength(1);
    expect(f.database.read((sql) => read_row(sql, 'core', 1)[0])).toMatchObject({
      source_sequence: 1,
      row_kind: 'agent_message',
      body: {
        kind: 'inline',
        value: { to_owner: true, content: [{ piece: 'text', text: 'Complete' }] },
      },
    });
  } finally {
    fail_commit = false;
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});
