/** Scripted update responses exercise activation and the two admission gates. */
import { IncomingMessage } from 'node:http';
import { Socket } from 'node:net';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
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

const roots: string[] = [],
  controllers: MacUpdateController[] = [],
  workers: FilesService[] = [];
function fixture(replay = false, trial = false) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-update-')));
  roots.push(root);
  const files = new FilesService();
  workers.push(files);
  const identity: ExecutorIdentity = {
    protocol_version: 22,
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
  class Peer extends HttpSessions {
    constructor() {
      super('https://fixture.invalid', CONNECTORS);
    }
    override async request<T>(
      input: HttpRequest,
      headers: Readonly<Record<string, string>>,
      consume: ReplyReader<T>,
    ): Promise<T> {
      expect(headers).toEqual({ 'X-Panels-Executor-ID': 'scripted-mac' });
      let body: Buffer;
      if (input.method === 'POST') {
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
      response.statusCode = 200;
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
    executor_id: 'scripted-mac',
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
});
test('prefetch supplies an offered activation and records handoff before exit', async () => {
  const f = fixture();
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
  f.controller.offer(f.check({ ...f.target, activate: true }));
  await f.exit_requested;
  expect(f.download_paths).toEqual([prefetch_path]);
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
  });
  await atomic_record(f.files, join(f.root, 'updates', 'pending.json'), { request_id: 'current' });
  const current = {
    ...f.target,
    request_id: 'current',
    application_revision: f.identity.application_revision,
  };
  await f.controller.apply(f.check(current));
  expect(f.runner.finish_planned_replay).not.toHaveBeenCalled();
  expect(JSON.parse(readFileSync(f.runtime.ready_path!, 'utf8')).request_id).toBe('current');
  await f.files.remove_runtime_record(join(f.root, 'updates', 'pending.json'));
  await f.controller.apply(f.check(current));
  expect(f.runner.finish_planned_replay).toHaveBeenCalledTimes(1);
  expect(JSON.parse(readFileSync(f.runtime.handoff_path!, 'utf8')).replay_pending).toBe(false);
  await f.controller.apply(f.check(f.target));
  await f.controller.apply(f.check(null));
  expect(f.runner.resume_after_cancelled_update).toHaveBeenCalledTimes(2);
  expect(f.downloads()).toBe(0);
});
