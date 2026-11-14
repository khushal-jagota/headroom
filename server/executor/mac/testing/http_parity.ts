/** Export-only companion. The Python driver owns the TLS fixture and every input path. */
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { MacHttpClient } from '../transport/http.ts';
import { MacFileTransport } from '../transport/files.ts';
import { FilesService } from '../files.ts';
import { decode_bounded, encode_bounded } from '../../protocol.ts';
import type { WireRootName } from '../../protocol.ts';
import { ExecutorHttpError, failure_name } from '../transport/errors.ts';
import { ConnectionDiagnostics } from '../transport/diagnostics.ts';
import { CONNECTORS } from '../transport/http.ts';
import { HttpSessions, read_bounded } from '../transport/sessions.ts';
import { ShutdownDeadlineExceeded, ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { ExecutorIdentity } from '../../../execution_identity.ts';
interface Spec {
  origin: string;
  owned_origin: string;
  ca: string;
  source: string;
  target: string;
  identity: ExecutorIdentity;
  roots: Record<string, string>;
}
function fixture_origin(value: string, owned: string): string {
  // Compare the allocated listener before constructing any client or opening a socket.
  const parsed = new URL(value);
  if (
    parsed.hostname !== '127.0.0.1' ||
    parsed.port === '8767' ||
    !parsed.port ||
    parsed.origin !== owned ||
    parsed.pathname !== '/' ||
    parsed.search ||
    parsed.hash
  )
    throw new Error('HTTP fixture does not own destination');
  return parsed.origin;
}
export async function run(spec: Spec): Promise<unknown> {
  for (const refused of ['https://example.com', 'https://127.0.0.1:9', 'https://127.0.0.1:8767'])
    assert.throws(() => fixture_origin(refused, spec.owned_origin));
  assert.throws(() => fixture_origin('https://127.0.0.1:8767', 'https://127.0.0.1:8767'));
  const origin = fixture_origin(spec.origin, spec.owned_origin);
  const options = { tls: { ca: fs.readFileSync(spec.ca) } };
  const client = new MacHttpClient(origin, 'synthetic-executor', spec.identity, options);
  const files = new FilesService(),
    transfer = new MacFileTransport(client, files),
    results: unknown[] = [];
  const signal = new AbortController().signal;
  const root = <K extends WireRootName>(name: K) =>
    decode_bounded(Buffer.from(spec.roots[name]!), name, { limit: 4 * 1024 * 1024 });
  const save = async (work: () => Promise<unknown>, name?: WireRootName) => {
    try {
      const result = await work();
      results.push(
        name
          ? Buffer.from(
              encode_bounded(result as never, name, { limit: 4 * 1024 * 1024 }),
            ).toString()
          : (result ?? null),
      );
    } catch (error) {
      results.push(
        error instanceof ExecutorHttpError
          ? {
              status: error.status,
              code: error.code,
              message: error.message,
              failure: failure_name(error),
            }
          : { failure: failure_name(error) },
      );
    }
  };
  try {
    for (let i = 0; i < 4; i++)
      await save(
        () => client.lease(root('LeaseRequest')),
        i === 0 ? 'LeaseReply' : i === 1 ? 'LeaseRefused' : undefined,
      );
    await save(
      async () =>
        decode_bounded(await client.poll_commands(123456789, 1.9, signal), 'MailboxPollReply', {
          limit: 4 * 1024 * 1024,
        }),
      'MailboxPollReply',
    );
    const report = Buffer.from(spec.roots.CommandOutcomeReport!);
    let closed = false;
    await save(
      () =>
        client.report_outcomes(
          {
            byte_count: report.length,
            async *[Symbol.asyncIterator]() {
              yield report.subarray(0, 17);
              yield report.subarray(17);
            },
            async close() {
              closed = true;
            },
          },
          signal,
        ),
      'CommandOutcomeReply',
    );
    assert.equal(closed, false, 'the reporter retains the borrowed reader');
    await save(() => client.post_items('conversation', root('ItemBatch')), 'ItemBatchReply');
    await save(() => client.read(root('LiveReadRequest')), 'JsonObject');
    const parent = join(dirname(spec.target), 'retained-transfer'),
      moved = parent + '-moved',
      outside = parent + '-outside';
    fs.mkdirSync(parent);
    fs.mkdirSync(outside);
    const held = await files.open_transfer(join(parent, 'file'), 'write');
    fs.renameSync(parent, moved);
    fs.symlinkSync(outside, parent);
    const bytes = Buffer.from('retained inode');
    await files.write_transfer(held, bytes);
    await files.finish_transfer(
      held,
      createHash('sha256').update(bytes).digest('hex'),
      Number(bytes.length),
    );
    await files.close_transfer(held);
    assert.deepEqual(fs.readFileSync(join(moved, 'file')), bytes);
    assert.deepEqual(fs.readdirSync(outside), []);
    await assert.rejects(files.open_transfer(join(parent, 'escape'), 'write'));
    const capped = await files.open_transfer(spec.source, 'read');
    assert.equal((await files.read_transfer(capped, 17)).length, 17);
    assert.equal((await files.read_transfer(capped, 1)).length, 1);
    await files.close_transfer(capped);
    await save(() => client.post_live('conversation', root('LiveEventBatch')));
    await save(() => transfer.upload_file(root('FileUploadRequest'), spec.source));
    await save(() => transfer.download_command_file('command', 'file', spec.target));
    await save(() =>
      transfer.download_command_file('command', 'corrupt', spec.target + '-corrupt'),
    );
    await save(() => client.poll_commands(1, 0, signal));
    await client.reset();
    await save(() => client.read(root('LiveReadRequest')), 'JsonObject');
  } finally {
    const stop = { deadline: performance.now() / 1000 + 5 };
    await Promise.all([client.stop(stop), files.stop(stop)]);
    await Promise.all([client.settled(), files.settled()]);
  }
  // These additional claims concern Node ownership rather than another Python behavior matrix.
  const sessions = new HttpSessions(origin, CONNECTORS, options);
  const held_poll = sessions.request(
    { loop: 'poll', method: 'GET', path: '/hold-poll', timeout: 1 },
    {},
    (response) => read_bounded(response, 1024),
  );
  const independent_lease = sessions.request(
    { loop: 'lease', method: 'GET', path: '/release-poll', timeout: 1 },
    {},
    (response) => read_bounded(response, 1024),
  );
  assert.equal((await independent_lease).toString(), 'lease complete');
  assert.equal((await held_poll).toString(), 'poll complete');
  let release!: () => void, entered!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const blocked = sessions.request(
    {
      loop: 'outcomes',
      method: 'POST',
      path: '/custody',
      body: {
        async *[Symbol.asyncIterator]() {
          entered();
          await gate;
          yield Buffer.from('held');
        },
      },
    },
    {},
    async (response) => read_bounded(response, 1024),
  );
  void blocked.catch(() => {});
  await started;
  let settled = false;
  const closing = sessions.close().then(() => {
    settled = true;
  });
  let second_settled = false;
  const second_closing = sessions.close().then(() => {
    second_settled = true;
  });
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(settled, false, 'reset joins the actual producer');
  assert.equal(second_settled, false, 'a repeated close retains the original join');
  release();
  await Promise.allSettled([blocked]);
  await closing;
  await second_closing;
  await assert.rejects(
    sessions.request({ loop: 'lease', method: 'GET', path: '/' }, {}, async () => null),
    ServerLifecycleError,
  );
  const deadline_client = new MacHttpClient(origin, 'synthetic-executor', spec.identity, options);
  let release_late!: () => void;
  const late = new Promise<void>((resolve) => {
    release_late = resolve;
  });
  let ready!: () => void;
  const begin = new Promise<void>((resolve) => {
    ready = resolve;
  });
  const owned = deadline_client.sessions.request(
    {
      loop: 'outcomes',
      method: 'POST',
      path: '/early-response',
      timeout: 0.1,
      body: {
        async *[Symbol.asyncIterator]() {
          yield Buffer.from('prefix');
          await late;
          yield Buffer.from('late');
        },
      },
    },
    {},
    async (response) => {
      await read_bounded(response, 1024);
      ready();
      return null;
    },
  );
  void owned.catch(() => {});
  await begin;
  await assert.rejects(owned, { name: 'TimeoutError' });
  await assert.rejects(
    deadline_client.stop({ deadline: performance.now() / 1000 + 0.02 }),
    ShutdownDeadlineExceeded,
  );
  release_late();
  await Promise.allSettled([owned]);
  await deadline_client.settled();
  const timeouts = new HttpSessions(origin, CONNECTORS, options);
  await assert.rejects(
    timeouts.request(
      { loop: 'lease', method: 'GET', path: '/stall', timeout: 0.03 },
      {},
      async (response) => read_bounded(response, 1024),
    ),
    { name: 'TimeoutError' },
  );
  await timeouts.close();
  const logs: string[] = [];
  let now = 0;
  const diagnostics = new ConnectionDiagnostics(
    (line) => logs.push(line),
    () => now,
  );
  diagnostics.report(new ExecutorHttpError(403, 'denied', 'private'), 'lease');
  diagnostics.report(new ExecutorHttpError(403, 'denied', 'private'), 'lease');
  now = 61;
  diagnostics.report(new ExecutorHttpError(403, 'denied', 'private'), 'lease');
  return { results, logs, custody: true, destination_guard: true };
}
