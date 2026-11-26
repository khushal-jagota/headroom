/** Gaps: latest-position receipts cannot prove durable message custody or retained bytes.
 * Reuse Ticket SQLite fixtures. No browser or native backend is necessary for local commit/reopen.
 */
import { createHash } from 'node:crypto';
import { mkdtemp, open, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { fixture, fixture_path, create } from '../../tests/ticket_fixture.ts';
import { createDatabase } from '../../core/database/index.ts';
import type { TransactionSql } from '../../core/database/contracts.ts';
import type { AcceptedAgentMessage, RetainedMessageFile } from '../contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID, type ExecutorIdentity } from '../../execution_identity.ts';
import { PROTOCOL_VERSION } from '../../execution_configuration.ts';
import { InboxStore, type PublishedFiles } from '../../executor/data/inbox.ts';
import type { FileStaging } from '../../executor/vps_files.ts';
import {
  pending_agent_messages,
  read_agent_message,
  replay_agent_message,
  retained_agent_message_files,
  retain_published_message_files,
  write_agent_message_acceptance,
} from './agent_acceptances.ts';

vi.mock('../configuration.ts', () => ({
  MAX_PENDING_AGENT_MESSAGES: 2,
  MAX_PENDING_AGENT_MESSAGE_BYTES: 6000,
}));
const identity: ExecutorIdentity = {
  executor_incarnation: '11111111-1111-4111-8111-111111111111',
  application_revision: 'test',
  protocol_version: PROTOCOL_VERSION,
  spool_format_version: 5,
  connection_generation: 0,
};
function world() {
  const database = fixture();
  const ticket = create(database);
  const source = { kind: 'ticket' as const, id: ticket.id };
  const child = {
    executor_incarnation: identity.executor_incarnation,
    child_id: 'child',
    conversation_id: 'source',
  };
  database.write((tx) => {
    tx.run("UPDATE tickets SET conversation_id='source' WHERE id=?", [ticket.id]);
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
      [
        identity.executor_incarnation,
        child.child_id,
        child.conversation_id,
        source.kind,
        source.id,
      ],
    );
  });
  const store = new InboxStore(database, { machine_id: ORIGINAL_MAC_MACHINE_ID } as FileStaging);
  const message = (id: string, sequence = 1): Omit<AcceptedAgentMessage, 'recipient_order'> => {
    const recipient = { kind: 'owner' as const, id: 'owner' };
    return {
      submission_id: id,
      machine_id: ORIGINAL_MAC_MACHINE_ID,
      sender_id: 'sender',
      source_conversation_id: 'source',
      source,
      recipient,
      mode: 'queue',
      message: 'Same text',
      target_conversation_id: 'source',
      pending: false,
      envelope: {
        submission_id: id,
        sequence,
        sender_id: 'sender',
        conversation_id: 'source',
        kind: 'cli_write',
        file_dependencies: [],
        payload: {
          child,
          operation: 'message.send',
          arguments: { body: { target: recipient, message: 'Same text', mode: 'queue' } },
          turn: null,
        },
      },
      receipt: {
        sender_id: 'sender',
        submission_id: id,
        disposition: 'accepted',
        accepted_at_vps_ms: 100,
        result: { fate: 'recorded', conversation_id: 'source' },
      },
    };
  };
  const accept = (value: ReturnType<typeof message>, files: readonly RetainedMessageFile[] = []) =>
    database.write((tx) => {
      const result = write_agent_message_acceptance(tx, value, files);
      store.write_position(tx, value.envelope, result.receipt);
      return result;
    });
  return { database, store, source, message, accept };
}
test('receipt loss and a later position preserve the original message across a database reopen', () => {
  const { database, store, message, accept } = world();
  const first = message('first');
  const original = accept(first);
  accept(message('second', 2)); // Identical text is a distinct send under a distinct ID.
  expect(
    database.read((sql) => sql.all('SELECT submission_id FROM agent_message_acceptances')),
  ).toHaveLength(2);
  expect(store.preflight({ ...first.envelope, sequence: 3 }, { identity })).toEqual(
    original.receipt,
  );
  expect(
    database.read(
      (sql) => sql.get<{ sequence: number }>('SELECT sequence FROM mac_inbox_positions')!.sequence,
    ),
  ).toBe(2);
  const path = fixture_path(database);
  database.close();
  const reopened = createDatabase(path);
  try {
    expect(reopened.read((sql) => read_agent_message(sql, 'first'))).toEqual(original);
    const replay = new InboxStore(reopened, { machine_id: ORIGINAL_MAC_MACHINE_ID } as FileStaging);
    expect(replay.preflight(first.envelope, { identity })).toEqual(original.receipt);
  } finally {
    reopened.close();
  }
});
test('replay authenticates source and rejects changed message, mode, destination or older unknown IDs', () => {
  const { database, store, source, message, accept } = world();
  const first = message('first');
  accept(first);
  for (const change of [
    { message: 'Changed' },
    { mode: 'steer' },
    { target: { kind: 'chief', id: 'chief' } },
  ]) {
    const envelope = structuredClone(first.envelope);
    const args = envelope.payload.arguments as { body: Record<string, unknown> };
    args.body = { ...args.body, ...change };
    expect(() => store.preflight(envelope, { identity })).toThrow('conflicts');
  }
  expect(() =>
    database.read((sql) => replay_agent_message(sql, first.envelope, 'other-machine', source)),
  ).toThrow('another source');
  expect(() =>
    database.read((sql) =>
      replay_agent_message(sql, first.envelope, ORIGINAL_MAC_MACHINE_ID, {
        kind: 'chief',
        id: 'chief',
      }),
    ),
  ).toThrow('another source');
  accept(message('second', 2));
  expect(() => store.preflight(message('unknown').envelope, { identity })).toThrow(
    'No retained acceptance',
  );
});
test('a storage failure rolls custody, file bytes and the sender position back together', () => {
  const { database, store, message } = world();
  const value = message('fails');
  const bytes = Buffer.from('retained bytes');
  const manifest = {
    scope: 'ticket' as const,
    owner_id: value.source.id,
    relative_path: 'artifacts/file.txt',
    byte_count: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
  };
  value.envelope.file_dependencies = [manifest];
  expect(() =>
    database.write((tx) => {
      write_agent_message_acceptance(tx, value, [{ manifest, bytes }]);
      const fault: TransactionSql = {
        ...tx,
        get: tx.get.bind(tx),
        all: tx.all.bind(tx),
        savepoint: tx.savepoint.bind(tx),
        run() {
          throw Object.assign(new Error('disk full'), { code: 'SQLITE_FULL' });
        },
      };
      store.write_position(fault, value.envelope, value.receipt);
    }),
  ).toThrow('disk full');
  expect(database.read((sql) => read_agent_message(sql, value.submission_id))).toBeNull();
  expect(database.read((sql) => sql.all('SELECT * FROM agent_message_files'))).toEqual([]);
  expect(database.read((sql) => sql.all('SELECT * FROM mac_inbox_positions'))).toEqual([]);
});
test('pending readers preserve recipient order and capacity refusal leaves no receipt or cursor advance', () => {
  const { database, message, accept } = world();
  const a = { ...message('a'), pending: true, recipient: { kind: 'chief' as const, id: 'chief' } };
  const b = { ...message('b', 2), pending: true, recipient: a.recipient };
  accept(a);
  accept(b);
  expect(
    database
      .read((sql) => pending_agent_messages(sql, a.recipient))
      .map((row) => [row.submission_id, row.recipient_order]),
  ).toEqual([
    ['a', 1],
    ['b', 2],
  ]);
  expect(() => accept({ ...message('c', 3), pending: true, recipient: a.recipient })).toThrow(
    'cannot retain',
  );
  expect(database.read((sql) => read_agent_message(sql, 'c'))).toBeNull();
  expect(
    database.read(
      (sql) => sql.get<{ sequence: number }>('SELECT sequence FROM mac_inbox_positions')!.sequence,
    ),
  ).toBe(2);
  database.write((tx) => tx.run('UPDATE agent_message_acceptances SET pending=0'));
  const huge = { ...message('huge', 3), pending: true, message: 'é'.repeat(4000) };
  expect(() => accept(huge)).toThrow('cannot retain');
  expect(database.read((sql) => read_agent_message(sql, 'huge'))).toBeNull();
});
test('home retains verified bytes while missing or changed file bytes cannot receive acceptance', async () => {
  const { database, message, accept } = world();
  const value = message('with-file');
  const bytes = Buffer.from('original bytes');
  const manifest = {
    scope: 'ticket' as const,
    owner_id: value.source.id,
    relative_path: 'artifacts/file.txt',
    byte_count: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
  };
  value.envelope.file_dependencies = [manifest];
  expect(() => accept(value)).toThrow('not retained');
  expect(() => accept(value, [{ manifest, bytes: Buffer.from('wrong bytes') }])).toThrow(
    'no longer match',
  );
  const dir = await mkdtemp(join(tmpdir(), 'message-custody-'));
  const target = join(dir, 'file.txt');
  const parent = await open(dir, 'r');
  const published = {
    files: [{ manifest, target, parent, leaf: 'file.txt' }],
  } as unknown as PublishedFiles;
  try {
    await expect(retain_published_message_files(published)).rejects.toThrow();
    await writeFile(target, 'changed bytes');
    await expect(retain_published_message_files(published)).rejects.toThrow('no longer match');
    await writeFile(target, bytes);
    const retained = await retain_published_message_files(published);
    accept(value, retained);
    await writeFile(target, 'later change');
    expect(database.read((sql) => retained_agent_message_files(sql, value.submission_id))).toEqual([
      { manifest, bytes },
    ]);
  } finally {
    await parent.close();
    await rm(dir, { recursive: true, force: true });
  }
});
