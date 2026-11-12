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
import { FilesService } from '../files.ts';
import { acquire_process_lock } from '../platform.ts';
import { OutboxStore } from './outbox.ts';
import { open_command_journal } from './journal.ts';
import { JOURNAL_NAME } from './journal_schema.ts';
import type { CanonicalReader, JournalDependencies } from './journal_contracts.ts';
import type { OutboxCleanup } from './resources.ts';

const deadline = () => ({ deadline: performance.now() / 1000 + 5 });
const roots: { root: string; owner?: OutboxStore }[] = [];
const command =
  '{"command_sequence":1,"command_id":"stop","created_at_vps_ms":1,"child_command":{"command_id":"stop","child":{"executor_incarnation":"inc","child_id":"child","conversation_id":"c"},"operation":"stop","turn":null,"arguments":{"kind":"stop"},"file_dependencies":[]},"model_list_request":null}';
const reply = '{"commands":[' + command + ']}';
const result = '{"command_id":"stop","child_outcome":null,"model_list":null}';
const duplicate = command.replace('"command_sequence":1', '"command_sequence":2');
const repeated = '{"commands":[' + duplicate + ']}';
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
  const owner = await OutboxStore.open(
    spool,
    { monotonic: () => performance.now() / 1000, now_ns: () => BigInt(Date.now()) * 1_000_000n },
    deadline(),
  );
  record.owner = owner;
  const existing = new Set(workers),
    database = join(spool, JOURNAL_NAME);
  const entered = join(root, 'entered'),
    release = join(root, 'release'),
    fail_close = join(root, 'fail-close'),
    crash = join(root, 'crash');
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
import {isMainThread} from 'node:worker_threads';
let pause_policy;
export function command_owes_outputs(...args) { if (pause_policy) pause_policy(); return original_owed(...args); }
if (!isMainThread) {
  const fs = await import('node:fs');
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
      await owner.stop(deadline());
      await owner.settled();
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
  await expect(f.owner.stop({ deadline: performance.now() / 1000 - 1 })).rejects.toMatchObject({
    name: 'ServerLifecycleError',
  });
  expect(() => acquire_process_lock(f.spool)).toThrow('outbox is already owned');
  writeFileSync(f.release, 'release');
  expect(await opening).toMatchObject({ name: 'ServerLifecycleError' });
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
  await failure!.cleanup!.stop(deadline());
  await failure!.cleanup!.settled();
  await f.owner.stop(deadline());
  await f.owner.settled();
  const lease = acquire_process_lock(f.spool);
  lease.close();
});

test('live close failure keeps the main lock until a retry joins native journal closure', async () => {
  const f = await fixture();
  await open_command_journal(f.owner, 'inc', f.dependencies, deadline());
  writeFileSync(f.fail_close, 'hold');
  await expect(f.owner.stop(deadline())).rejects.toThrow('journal close blocked');
  expect(f.worker().threadId).not.toBe(-1);
  expect(() => acquire_process_lock(f.spool)).toThrow('outbox is already owned');
  rmSync(f.fail_close);
  await f.owner.stop(deadline());
  await f.owner.settled();
  const lease = acquire_process_lock(f.spool);
  lease.close();
});

test('an observed resource fault rolls back a blocked journal transaction before actual worker exit releases native resources', async () => {
  const f = await fixture(),
    journal = await open_command_journal(f.owner, 'inc', f.dependencies, deadline());
  const cursor = await journal.record_poll_reply(Buffer.from(reply), 'inc', 0n);
  const item = await cursor[Symbol.asyncIterator]().next();
  expect(item.done).toBe(false);
  await cursor.close();
  expect(await read(item.value!.command)).toHaveLength(0);
  await journal.finish('stop', Buffer.from(result), false);
  writeFileSync(f.block_policy, 'hold');
  const updating = journal.record_poll_reply(Buffer.from(repeated), 'inc', 1n);
  void updating.catch(() => {});
  try {
    await vi.waitFor(() => expect(existsSync(f.policy_entered)).toBe(true));
    // The pure policy gate runs after UPDATE inside the journal's single transaction.
    const file_exit = new Promise<void>((resolve) => f.file_worker.once('exit', () => resolve()));
    f.file_worker.postMessage({ operation: 'stop' });
    await file_exit;
    writeFileSync(f.policy_release, 'release');
    await expect(updating).rejects.toMatchObject({ name: 'ServerLifecycleError' });
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
  const worker = f.worker();
  let held_at_error = false;
  worker.once('error', () => {
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
  await journal.settled();
  expect(() => acquire_process_lock(f.spool)).toThrow('outbox is already owned');
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
  await f.owner.stop(deadline());
  await f.owner.settled();
  const lease = acquire_process_lock(f.spool);
  lease.close();
});
