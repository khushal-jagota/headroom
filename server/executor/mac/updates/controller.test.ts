/** Scripted update responses exercise activation and the two admission gates. */
import { IncomingMessage } from 'node:http';
import { Socket } from 'node:net';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { afterEach, expect, test, vi } from 'vitest';
import { PROTOCOL_VERSION } from '../../../execution_configuration.ts';
import { Deferred } from '../../../conversation/runtime/state.ts';
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import { decode_bounded } from '../../protocol.ts';
import type { MacUpdateCheck, MacUpdateTarget } from '../../wire.generated.ts';
import { ExecutorPaths } from '../configuration.ts';
import { FilesService } from '../files.ts';
import { atomic_record } from '../runtime/records.ts';
import { HttpSessions } from '../transport/sessions.ts';
import { CONNECTORS } from '../transport/http.ts';
import type { HttpRequest, ReplyReader } from '../transport/contracts.ts';
import { MacUpdateController } from './controller.ts';
import { runtime_archive } from './testing/runtime_archive.ts';
import { EXECUTOR_HANDOVER_CLOSE_PATH } from '../../configuration.ts';

vi.mock('node:timers/promises', async (importOriginal) => {
  const original = await importOriginal<typeof import('node:timers/promises')>();
  return { ...original, setTimeout: vi.fn(original.setTimeout) };
});

const roots: string[] = [],
  controllers: MacUpdateController[] = [],
  workers: FilesService[] = [];
function fixture(replay = false, trial = false) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-update-')));
  roots.push(root);
  const files = new FilesService();
  workers.push(files);
  const identity: ExecutorIdentity = {
    protocol_version: PROTOCOL_VERSION,
    spool_format_version: 5,
    application_revision: 'b'.repeat(40),
    executor_incarnation: '11111111-1111-1111-1111-111111111111',
    connection_generation: 7,
  };
  const archive = runtime_archive('a'.repeat(40));
  const phases: string[] = [];
  let ready!: () => void, exit!: () => void;
  const ready_received = new Promise<void>((resolve) => {
    ready = resolve;
  });
  const exit_requested = new Promise<void>((resolve) => {
    exit = resolve;
  });
  const download_paths: string[] = [];
  let credential = 'a'.repeat(43);
  const authentication_trace: [string, string][] = [];
  const closes: string[] = [];
  const close_errors: Error[] = [];
  let close_status = 200;
  let close_hold: Promise<void> | undefined;
  class Peer extends HttpSessions {
    constructor() {
      super('https://fixture.invalid', CONNECTORS);
    }
    override async request<T>(
      input: HttpRequest,
      headers: Readonly<Record<string, string>>,
      consume: ReplyReader<T>,
    ): Promise<T> {
      expect(headers).toEqual({
        'X-Panels-Executor-ID': 'scripted-mac',
        'X-Panels-Executor-Credential': credential,
        'X-Panels-Executor-Platform': process.platform,
      });
      authentication_trace.push([input.method, credential]);
      credential = 'c'.repeat(43);
      let body: Buffer;
      if (input.path === EXECUTOR_HANDOVER_CLOSE_PATH) {
        closes.push(credential);
        const error = close_errors.shift();
        if (error) throw error;
        await close_hold;
        body = Buffer.from('{"closed":true}');
      } else if (input.method === 'POST') {
        expect(input.path).toBe('/api/executor/update');
        const report = decode_bounded(input.body as Uint8Array, 'MacUpdateReport');
        phases.push(report.phase);
        if (report.phase === 'ready_to_restart') ready();
        body = Buffer.from('{"confirmed":true}');
      } else {
        download_paths.push(input.path);
        body = archive.bytes;
      }
      const response = new IncomingMessage(new Socket());
      response.statusCode = input.path === EXECUTOR_HANDOVER_CLOSE_PATH ? close_status : 200;
      response.complete = true;
      response.push(body);
      response.push(null);
      try {
        return await consume(response);
      } finally {
        response.destroy();
      }
    }
  }
  const runner = {
    get planned_update_replay() {
      return replay;
    },
    prepare_update: vi.fn(async () => true),
    finish_planned_replay: vi.fn(() => {
      replay = false;
    }),
    resume_after_cancelled_update: vi.fn(),
  };
  const on_failure = vi.fn();
  const runtime = {
    child: true,
    ready_path: trial ? join(root, 'updates', 'ready.json') : null,
    handoff_path: trial ? join(root, 'updates', 'handoff.json') : null,
  };
  const controller = new MacUpdateController({
    paths: new ExecutorPaths(root),
    files,
    runner,
    presence: { identity, leased: () => true },
    outbox: { pending_count: () => 1 },
    authentication_headers: () => ({
      'X-Panels-Executor-ID': 'scripted-mac',
      'X-Panels-Executor-Credential': credential,
      'X-Panels-Executor-Platform': process.platform,
    }),
    runtime,
    session_factory: () => new Peer(),
    request_exit: exit,
    on_failure,
  });
  controllers.push(controller);
  const target: MacUpdateTarget = {
    request_id: 'offered',
    application_revision: 'a'.repeat(40),
    archive_sha256: archive.sha,
    archive_size: archive.bytes.length,
    archive_path: '/api/executor/update/offered/archive',
    mode: 'idle',
    activate: false,
  };
  const check = (value: MacUpdateTarget | null): MacUpdateCheck => ({
    schema_version: 1,
    prefetch: null,
    target: value,
  });
  return {
    root,
    controller,
    files,
    runner,
    identity,
    runtime,
    target,
    check,
    phases,
    on_failure,
    ready_received,
    exit_requested,
    downloads: () => download_paths.length,
    download_paths,
    authentication_trace,
    closes,
    close_errors,
    close_response(status: number, hold?: Promise<void>) {
      close_status = status;
      close_hold = hold;
    },
    authenticate: () => {
      credential = 'b'.repeat(43);
    },
  };
}
afterEach(async () => {
  for (const controller of controllers.splice(0)) {
    await controller.stop({ deadline: performance.now() / 1000 + 10 });
    await controller.settled();
  }
  for (const files of workers.splice(0)) {
    await files.stop({ deadline: performance.now() / 1000 + 10 });
    await files.settled();
  }
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
  const original =
    await vi.importActual<typeof import('node:timers/promises')>('node:timers/promises');
  vi.mocked(sleep).mockReset().mockImplementation(original.setTimeout);
});
test('prefetch supplies an offered activation and records handoff before exit', async () => {
  const f = fixture();
  f.authenticate();
  f.controller.start();
  const prefetch_path = `/api/executor/update/prefetch/${f.target.archive_sha256}`;
  f.controller.offer({
    ...f.check(f.target),
    prefetch: {
      application_revision: f.target.application_revision,
      archive_sha256: f.target.archive_sha256,
      archive_size: f.target.archive_size,
      archive_path: prefetch_path,
    },
  });
  await f.ready_received;
  expect(f.closes).toHaveLength(1);
  f.controller.offer(f.check({ ...f.target, activate: true }));
  await f.exit_requested;
  expect(f.download_paths).toEqual([prefetch_path]);
  expect(f.authentication_trace[0]![1]).toBe('b'.repeat(43));
  expect(f.authentication_trace.slice(1).every((entry) => entry[1] === 'c'.repeat(43))).toBe(true);
  expect(f.runner.prepare_update).toHaveBeenCalledTimes(1);
  expect(f.phases).toEqual([
    'ready_to_restart',
    'ready_to_restart',
    'downloading',
    'ready_to_restart',
  ]);
  expect(existsSync(join(f.root, 'updates', 'prefetch.tar.gz'))).toBe(false);
  const pending = JSON.parse(readFileSync(join(f.root, 'updates', 'pending.json'), 'utf8'));
  expect(pending).toMatchObject({
    request_id: 'offered',
    previous_runtime_path: null,
    executor_incarnation: f.identity.executor_incarnation,
    connection_generation: 7,
  });
  expect(readFileSync(join(pending.runtime_path, 'node/bin/node'), 'utf8')).toBe(
    'synthetic executable',
  );
  expect(JSON.parse(readFileSync(join(f.root, 'updates', 'handoff.json'), 'utf8'))).toEqual({
    executor_incarnation: f.identity.executor_incarnation,
    connection_generation: 7,
    replay_pending: true,
  });
  let settled = false;
  const actual = f.controller.settled().then(() => {
    settled = true;
  });
  await Promise.resolve();
  expect(settled).toBe(false);
  await f.controller.stop({ deadline: performance.now() / 1000 + 10 });
  await actual;
  expect(f.on_failure).not.toHaveBeenCalled();
});
test('trial admission waits for supervisor selection, then cancellation resumes a prepared runtime', async () => {
  const f = fixture(true, true);
  await atomic_record(f.files, f.runtime.handoff_path!, {
    executor_incarnation: f.identity.executor_incarnation,
    connection_generation: 7,
    replay_pending: true,
  });
  await atomic_record(f.files, join(f.root, 'updates', 'selection.json'), {
    request_id: 'current',
    application_revision: f.identity.application_revision,
  });
  await atomic_record(f.files, join(f.root, 'updates', 'pending.json'), { request_id: 'current' });
  const current = {
    ...f.target,
    request_id: 'current',
    application_revision: f.identity.application_revision,
  };
  await f.controller.apply(f.check(current));
  expect(await f.controller.kept_by_supervisor()).toBe(false);
  expect(f.runner.finish_planned_replay).not.toHaveBeenCalled();
  expect(JSON.parse(readFileSync(f.runtime.ready_path!, 'utf8')).request_id).toBe('current');
  await f.files.remove_runtime_record(join(f.root, 'updates', 'pending.json'));
  await atomic_record(f.files, join(f.root, 'updates', 'selection.json'), {
    request_id: 'current',
    application_revision: 'a'.repeat(40),
  });
  await f.controller.apply(f.check(current));
  expect(f.runner.finish_planned_replay).not.toHaveBeenCalled();
  expect(await f.controller.kept_by_supervisor()).toBe(false);
  await atomic_record(f.files, join(f.root, 'updates', 'selection.json'), {
    request_id: 'other',
    application_revision: f.identity.application_revision,
  });
  expect(await f.controller.kept_by_supervisor()).toBe(false);
  await atomic_record(f.files, join(f.root, 'updates', 'selection.json'), {
    request_id: 'current',
    application_revision: f.identity.application_revision,
  });
  await f.controller.apply(f.check(current));
  expect(await f.controller.kept_by_supervisor()).toBe(true);
  await f.controller.apply(f.check({ ...current, request_id: 'same-revision-new-request' }));
  expect(JSON.parse(readFileSync(f.runtime.ready_path!, 'utf8')).request_id).toBe(
    'same-revision-new-request',
  );
  expect(JSON.parse(readFileSync(join(f.root, 'updates', 'selection.json'), 'utf8'))).toEqual({
    request_id: 'current',
    application_revision: f.identity.application_revision,
  });
  await atomic_record(f.files, join(f.root, 'updates', 'pending.json'), {
    request_id: 'later-attempt',
  });
  expect(await f.controller.kept_by_supervisor()).toBe(true);
  await f.controller.close_handover_once_kept();
  expect(f.closes).toHaveLength(1);
  await f.files.remove_runtime_record(join(f.root, 'updates', 'pending.json'));
  expect(f.runner.finish_planned_replay).toHaveBeenCalledTimes(1);
  expect(JSON.parse(readFileSync(f.runtime.handoff_path!, 'utf8')).replay_pending).toBe(false);
  await f.controller.apply(f.check(f.target));
  await f.controller.apply(f.check(null));
  expect(f.runner.resume_after_cancelled_update).toHaveBeenCalledTimes(2);
  expect(f.downloads()).toBe(0);
});

test('handover retries back off, then shutdown joins its admitted close without ending the host', async () => {
  const f = fixture();
  expect(await f.controller.kept_by_supervisor()).toBe(true);
  f.close_response(503);
  f.close_errors.push(
    Object.assign(new Error('scripted HTTP parser failure'), { code: 'HPE_INVALID_HEADER_TOKEN' }),
    Object.assign(new Error('scripted response close'), { code: 'ERR_STREAM_PREMATURE_CLOSE' }),
  );
  const waits: { milliseconds: number; release: Deferred<void> }[] = [];
  vi.mocked(sleep).mockImplementation(async <T>(milliseconds?: number, value?: T) => {
    const release = new Deferred<void>();
    waits.push({ milliseconds: milliseconds!, release });
    await release.promise;
    return value as T;
  });
  const held = new Deferred<void>();
  try {
    const closing = f.controller.close_handover_once_kept();
    for (const expected of [2000, 4000, 8000, 16000, 32000, 60000, 60000]) {
      await vi.waitFor(() => expect(waits.at(-1)?.milliseconds).toBe(expected));
      const previous = waits.length;
      if (expected === 60000 && previous === 7) break;
      waits.at(-1)!.release.resolve();
      await vi.waitFor(() => expect(waits.length).toBeGreaterThan(previous));
    }
    f.close_response(200, held.promise);
    waits.at(-1)!.release.resolve();
    await vi.waitFor(() => expect(f.closes).toHaveLength(8));
    let stopped = false;
    const shutdown = f.controller.stop({ deadline: performance.now() / 1000 + 10 }).then(() => {
      stopped = true;
    });
    await Promise.resolve();
    expect(stopped).toBe(false);
    held.resolve();
    await closing;
    await shutdown;
    expect(f.on_failure).not.toHaveBeenCalled();
  } finally {
    held.resolve();
    for (const wait of waits) wait.release.resolve();
  }
});
