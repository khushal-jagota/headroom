/** Journal worker lifecycle gaps that value/SQL comparison cannot prove. */
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { Worker } from 'node:worker_threads';
import { afterEach, expect, test, vi } from 'vitest';
import { parse_numeric_json, stringify_python_json } from '../../../core/http/numeric_json.ts';
import { createReadAccess } from '../../../core/database/index.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import { FilesService } from '../files.ts';
import { FILE_CHUNK_BYTES } from '../configuration.ts';
import { acquire_process_lock } from '../platform.ts';
import { OutboxStore } from './outbox.ts';
import { open_command_journal } from './journal.ts';
import { JOURNAL_NAME } from './journal_schema.ts';
import type { CanonicalReader, JournalDependencies } from './journal_contracts.ts';
import type { OutboxCleanup } from './resources.ts';

const deadline = () => ({ deadline: performance.now() / 1000 + 5 });
const roots: { root: string; owner?: OutboxStore }[] = [];
const expected_failures = new WeakSet<OutboxCleanup>();
function messages(error: unknown): string[] {
  if (!(error instanceof Error)) return [String(error)];
  return [
    error.message,
    ...(error instanceof AggregateError ? error.errors.flatMap(messages) : []),
    ...(error.cause === undefined ? [] : messages(error.cause)),
  ];
}
async function retained_failure(task: Promise<unknown>, message?: string): Promise<void> {
  const error = await task.then(
    () => null,
    (failure) => failure,
  );
  expect(error).toBeInstanceOf(Error);
  expect(error).not.toBeInstanceOf(ShutdownDeadlineExceeded);
  if (message) expect(messages(error)).toContain(message);
}
async function actual_failure(owner: OutboxCleanup, message: string): Promise<void> {
  expected_failures.add(owner);
  await retained_failure(
    observe_shutdown(owner.settled(), deadline(), 'actual cleanup stalled'),
    message,
  );
}
const command =
  '{"command_sequence":1,"command_id":"stop","created_at_vps_ms":1,"child_command":{"command_id":"stop","child":{"executor_incarnation":"inc","child_id":"child","conversation_id":"c"},"operation":"stop","turn":null,"arguments":{"kind":"stop"},"file_dependencies":[]},"model_list_request":null}';
const reply = '{"commands":[' + command + ']}';
const result = '{"command_id":"stop","child_outcome":null,"model_list":null}';
const duplicate = command.replace('"command_sequence":1', '"command_sequence":2');
const repeated = '{"commands":[' + duplicate + ']}';
const large_result = result.replace(
  '"model_list":null',
  '"model_list":{"text":"' + 'x'.repeat(FILE_CHUNK_BYTES + 1) + '"}',
);
async function fixture(gated = false) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-journal-'))),
    record = { root } as { root: string; owner?: OutboxStore };
  roots.push(record);
  const spool = join(root, 'spool'),
    workers = new Set<Worker>(),
    post = Worker.prototype.postMessage;
  vi.spyOn(Worker.prototype, 'postMessage').mockImplementation(function (
    this: Worker,
    ...args: Parameters<Worker['postMessage']>
  ) {
    workers.add(this);
    return post.call(this, ...args);
  });
  const root_fault = vi.fn(),
    journal_fault = vi.fn(),
    shutdown: Promise<unknown>[] = [];
  let host: OutboxStore | undefined;
  const stop_host = () => {
    if (host) shutdown.push(host.stop(deadline()).catch((error) => error));
  };
  const owner = await OutboxStore.open(
    spool,
    { monotonic: () => performance.now() / 1000, now_ns: () => BigInt(Date.now()) * 1_000_000n },
    deadline(),
    (error) => {
      root_fault(error);
      stop_host();
    },
  );
  host = owner;
  record.owner = owner;
  const existing = new Set(workers),
    database = join(spool, JOURNAL_NAME);
  const entered = join(root, 'entered'),
    release = join(root, 'release'),
    fail_close = join(root, 'fail-close'),
    crash = join(root, 'crash');
  const truncate = join(root, 'truncate');
  const block_policy = join(root, 'block-policy'),
    policy_entered = join(root, 'policy-entered'),
    policy_release = join(root, 'policy-release');
  const source = new URL('./testing/journal_fixture.ts', import.meta.url),
    table = join(root, 'fixture');
  source.searchParams.set('fixture', table);
  writeFileSync(
    table,
    JSON.stringify({
      entries: [
        ['MailboxCommand', command],
        ['MailboxPollReply', reply],
        ['CommandResult', result],
        ['MailboxCommand', duplicate],
        ['MailboxPollReply', repeated],
        ['CommandResult', large_result],
      ].map(([root, text]) => ({
        root,
        hex: Buffer.from(text!).toString('hex'),
        aliases: [
          stringify_python_json(parse_numeric_json(text!), { sort_keys: true, compact: true }),
        ],
      })),
      owed: [[3, 2, false]],
    }),
  );
  const wrapper = join(root, 'dependency.mjs');
  writeFileSync(
    wrapper,
    `export * from ${JSON.stringify(source.href)};
import {command_owes_outputs as original_owed} from ${JSON.stringify(source.href)};
import {isMainThread, parentPort} from 'node:worker_threads';
let pause_policy;
export function command_owes_outputs(...args) { if (pause_policy) pause_policy(); return original_owed(...args); }
if (!isMainThread) {
  const fs = await import('node:fs');
  const post = parentPort.postMessage.bind(parentPort);
  parentPort.postMessage = (message, ...args) => {
    if (message.kind === 'reply' && message.value instanceof ArrayBuffer && fs.existsSync(${JSON.stringify(truncate)})) {
      fs.unlinkSync(${JSON.stringify(truncate)});
      return post({...message, value: null});
    }
    return post(message, ...args);
  };
  const {default: Database} = await import(${JSON.stringify(new URL('../../../node_modules/better-sqlite3/lib/index.js', import.meta.url).href)});
  const close = Database.prototype.close;
  Database.prototype.close = function() {
    if (this.name === ${JSON.stringify(database)} && fs.existsSync(${JSON.stringify(fail_close)})) throw new Error('journal close blocked');
    return close.call(this);
  };
  setInterval(() => { if (fs.existsSync(${JSON.stringify(crash)})) throw new Error('synthetic journal worker failure'); }, 5).unref();
  pause_policy = () => {
    if (!fs.existsSync(${JSON.stringify(block_policy)})) return;
    fs.writeFileSync(${JSON.stringify(policy_entered)}, 'entered');
    const until = Date.now() + 5000, wait = new Int32Array(new SharedArrayBuffer(4));
    while (!fs.existsSync(${JSON.stringify(policy_release)})) {
      if (Date.now() > until) throw new Error('synthetic policy gate expired');
      Atomics.wait(wait, 0, 0, 5);
    }
  };
  if (${gated}) {
    fs.writeFileSync(${JSON.stringify(entered)}, 'entered');
    const until = Date.now() + 5000;
    while (!fs.existsSync(${JSON.stringify(release)})) {
      if (Date.now() > until) throw new Error('synthetic module gate expired');
      await new Promise(resolve => setTimeout(resolve, 5));
    }
  }
}
`,
  );
  const url = pathToFileURL(wrapper);
  const dependencies: JournalDependencies = {
    protocol_module: url,
    outputs_module: url,
    now_ms: () => 1n,
    on_failure: (error) => {
      journal_fault(error);
      stop_host();
    },
  };
  const worker = () => [...workers].find((value) => !existing.has(value))!;
  return {
    root,
    spool,
    owner,
    database,
    dependencies,
    entered,
    release,
    fail_close,
    crash,
    worker,
    file_worker: [...existing][0]!,
    block_policy,
    policy_entered,
    policy_release,
    root_fault,
    journal_fault,
    shutdown,
    truncate,
  };
}
async function read(reader: CanonicalReader): Promise<Buffer> {
  const chunks: Uint8Array[] = [];
  for await (const chunk of reader) chunks.push(chunk);
  return Buffer.concat(chunks);
}
afterEach(async () => {
  vi.restoreAllMocks();
  for (const { root, owner } of roots.splice(0).reverse()) {
    writeFileSync(join(root, 'release'), 'release');
    writeFileSync(join(root, 'policy-release'), 'release');
    rmSync(join(root, 'fail-close'), { force: true });
    if (owner) {
      if (expected_failures.has(owner)) {
        await retained_failure(owner.stop(deadline()));
        await retained_failure(
          observe_shutdown(owner.settled(), deadline(), 'owned journal cleanup stalled'),
        );
      } else {
        await owner.stop(deadline());
        await observe_shutdown(owner.settled(), deadline(), 'owned journal cleanup stalled');
      }
    }
    rmSync(root, { recursive: true, force: true });
  }
});

test('stop during module load owns the unstarted journal and never opens its database', async () => {
  const f = await fixture(true);
  const opening = open_command_journal(f.owner, 'inc', f.dependencies, deadline()).then(
    () => null,
    (error) => error as Error,
  );
  await vi.waitFor(() => expect(existsSync(f.entered)).toBe(true));
  await expect(f.owner.stop({ deadline: performance.now() / 1000 - 1 })).rejects.toBeInstanceOf(
    ServerLifecycleError,
  );
  expect(() => acquire_process_lock(f.spool)).toThrow('outbox is already owned');
  writeFileSync(f.release, 'release');
  expect(await opening).toBeInstanceOf(ServerLifecycleError);
  // Observed timeout does not require a retry just to resume real cleanup.
  await observe_shutdown(f.owner.settled(), deadline(), 'module-load cleanup stalled');
  expect(existsSync(f.database)).toBe(false);
  const lease = acquire_process_lock(f.spool);
  lease.close();
});

test('failed startup flush retains a live cleanup continuation while journal close fails', async () => {
  const f = await fixture();
  writeFileSync(f.fail_close, 'hold');
  vi.spyOn(FilesService.prototype, 'fsync_directory').mockRejectedValueOnce(
    new Error('startup flush failed'),
  );
  const failure = await open_command_journal(f.owner, 'inc', f.dependencies, deadline()).then(
    () => null,
    (error) => error as Error & { cleanup?: OutboxCleanup },
  );
  expect(failure?.cleanup).toBeDefined();
  expect(f.worker().threadId).not.toBe(-1);
  expect(() => acquire_process_lock(f.spool)).toThrow('outbox is already owned');
  rmSync(f.fail_close);
  await retained_failure(failure!.cleanup!.stop(deadline()), 'journal close blocked');
  await actual_failure(failure!.cleanup!, 'journal close blocked');
  await retained_failure(f.owner.stop(deadline()), 'journal close blocked');
  await actual_failure(f.owner, 'journal close blocked');
  const lease = acquire_process_lock(f.spool);
  lease.close();
});

test('shutdown drains one admitted multi-chunk finish and retains the lock across a failed native close', async () => {
  const f = await fixture();
  const journal = await open_command_journal(f.owner, 'inc', f.dependencies, deadline());
  const fresh = await journal.record_poll_reply(Buffer.from(reply), 'inc', 0n);
  const item = await fresh[Symbol.asyncIterator]().next();
  const delivery = item.value!.command[Symbol.asyncIterator]();
  expect(Buffer.from((await delivery.next()).value!)).toEqual(Buffer.from(command));
  await fresh.close();
  // Issued cursors have their own bound and explicit close returns their capacity.
  const cursors = [];
  for (let i = 0; i < 32; i++) cursors.push(await journal.unreported(1));
  await expect(journal.unreported(1)).rejects.toThrow('cursor capacity');
  const occupying = Array.from({ length: 32 }, () => journal.poll_cursor());
  await expect(cursors[0]!.close()).rejects.toThrow('operation capacity');
  await Promise.all(occupying);
  await cursors[0]!.close();
  await (await journal.unreported(1)).close();
  await Promise.all(cursors.map((cursor) => cursor.close()));
  writeFileSync(f.fail_close, 'hold');
  const finishing = journal.finish('stop', Buffer.from(large_result), false);
  const queued = Array.from({ length: 31 }, () => journal.poll_cursor());
  const excess = expect(journal.poll_cursor()).rejects.toThrow('operation capacity');
  const stopping = expect(f.owner.stop(deadline())).rejects.toThrow('journal close blocked');
  await Promise.all([finishing, ...queued, excess, stopping]);
  // Every advertised byte arrived before stop. EOF needs no further admission.
  expect((await delivery.next()).done).toBe(true);
  expect(f.journal_fault).not.toHaveBeenCalled();
  const snapshot = createReadAccess(f.database);
  try {
    expect(
      snapshot.read((sql) =>
        sql.get('SELECT phase,length(result_json) AS bytes FROM commands WHERE command_id=?', [
          'stop',
        ]),
      ),
    ).toEqual({ phase: 'finished', bytes: Buffer.byteLength(large_result) });
  } finally {
    snapshot.close();
  }
  expect(f.worker().threadId).not.toBe(-1);
  expect(() => acquire_process_lock(f.spool)).toThrow('outbox is already owned');
  rmSync(f.fail_close);
  await retained_failure(f.owner.stop(deadline()), 'journal close blocked');
  await actual_failure(f.owner, 'journal close blocked');
  const lease = acquire_process_lock(f.spool);
  lease.close();
});

test('an observed resource fault rolls back a blocked journal transaction before actual worker exit releases native resources', async () => {
  const f = await fixture(),
    journal = await open_command_journal(f.owner, 'inc', f.dependencies, deadline());
  const cursor = await journal.record_poll_reply(Buffer.from(reply), 'inc', 0n);
  const iterator = cursor[Symbol.asyncIterator](),
    item = await iterator.next();
  expect(item.done).toBe(false);
  // Cursor EOF does not cancel an issued reader whose bytes are still owed.
  expect((await iterator.next()).done).toBe(true);
  expect(await read(item.value!.command)).toEqual(Buffer.from(command));
  await cursor.close();
  await journal.finish('stop', Buffer.from(result), false);
  const snapshot_cursor = await journal.unreported(1);
  const snapshot_item = await snapshot_cursor[Symbol.asyncIterator]().next();
  await snapshot_cursor.close();
  expect(await read(snapshot_item.value!.result)).toHaveLength(0);
  writeFileSync(f.fail_close, 'hold');
  writeFileSync(f.block_policy, 'hold');
  const updating = journal.record_poll_reply(Buffer.from(repeated), 'inc', 1n);
  void updating.catch(() => {});
  try {
    await vi.waitFor(() => expect(existsSync(f.policy_entered)).toBe(true));
    // This observer expires before the file-worker fault arrives; actual failure must survive it.
    await expect(f.owner.stop({ deadline: performance.now() / 1000 - 1 })).rejects.toBeInstanceOf(
      ServerLifecycleError,
    );
    // The pure policy gate runs after UPDATE inside the journal's single transaction.
    const file_exit = new Promise<void>((resolve) => f.file_worker.once('exit', () => resolve()));
    f.file_worker.postMessage({ operation: 'stop' });
    await file_exit;
    writeFileSync(f.policy_release, 'release');
    await expect(updating).rejects.toBeInstanceOf(ServerLifecycleError);
    const snapshot = createReadAccess(f.database);
    try {
      expect(
        snapshot.read((sql) =>
          sql.get('SELECT command_sequence FROM commands WHERE command_id=?', ['stop']),
        ),
      ).toEqual({ command_sequence: 1 });
    } finally {
      snapshot.close();
    }
  } finally {
    writeFileSync(f.policy_release, 'release');
  }
  await Promise.all(f.shutdown);
  expect(f.root_fault).toHaveBeenCalledTimes(1);
  const worker = f.worker();
  let held_at_error = false;
  let racing_stop: Promise<unknown> | undefined;
  worker.once('error', () => {
    racing_stop = journal.stop(deadline()).catch((error) => error);
    try {
      const lease = acquire_process_lock(f.spool);
      lease.close();
    } catch {
      held_at_error = true;
    }
  });
  const exited = new Promise<void>((resolve) => worker.once('exit', () => resolve()));
  writeFileSync(f.crash, 'exit');
  await exited;
  expect(held_at_error).toBe(true);
  expect(await racing_stop).toBeInstanceOf(ServerLifecycleError);
  expect(f.journal_fault).toHaveBeenCalledTimes(1);
  expect(f.root_fault).toHaveBeenCalledTimes(1);
  await actual_failure(journal, 'journal close blocked');
  expect(() =>
    f.owner.append(
      { submission_id: 'late', conversation_id: 'c', child_id: 'child', kind: 'cli_write' },
      Buffer.from('{"payload":{},"files":[]}'),
      [],
      [],
      () => {},
    ),
  ).toThrow();
  expect(readFileSync(f.database).byteLength).toBeGreaterThan(0);
  await Promise.all(f.shutdown);
  await actual_failure(f.owner, 'journal close blocked');
  const lease = acquire_process_lock(f.spool);
  lease.close();
});

test('truncated committed fresh delivery faults the host and joins actual cleanup', async () => {
  const f = await fixture();
  const journal = await open_command_journal(f.owner, 'inc', f.dependencies, deadline());
  const cursor = await journal.record_poll_reply(Buffer.from(reply), 'inc', 0n);
  const item = await cursor[Symbol.asyncIterator]().next();
  expect(item.done).toBe(false);
  writeFileSync(f.fail_close, 'hold');
  writeFileSync(f.truncate, 'truncate');
  await expect(read(item.value!.command)).rejects.toThrow('byte count');
  expect(f.journal_fault).toHaveBeenCalledTimes(1);
  expect(f.root_fault).toHaveBeenCalledTimes(1);
  for (const error of await Promise.all(f.shutdown))
    expect(error).toMatchObject({ message: 'journal close blocked' });
  expect(f.worker().threadId).not.toBe(-1);
  expect(() => acquire_process_lock(f.spool)).toThrow('outbox is already owned');
  rmSync(f.fail_close);
  await expect(journal.stop(deadline())).rejects.toBeInstanceOf(ServerLifecycleError);
  await actual_failure(f.owner, 'journal close blocked');
  expect(f.worker().threadId).toBe(-1);
  const lease = acquire_process_lock(f.spool);
  lease.close();
});
