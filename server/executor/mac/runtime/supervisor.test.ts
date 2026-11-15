/** The real supervisor keeps or reverts owned trials before credential handover can close. */
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { IncomingMessage } from 'node:http';
import { Socket } from 'node:net';
import { expect, test, vi } from 'vitest';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { ExecutorPaths } from '../configuration.ts';
import { FilesService } from '../files.ts';
import { atomic_record } from './records.ts';
import { supervise } from './supervisor.ts';
import { runtime_archive } from '../updates/testing/runtime_archive.ts';
import { LINUX_EXECUTOR } from '../../../environments/release/contracts.ts';
import { LINUX_EXECUTOR_ENTRYPOINT } from '../../../environments/linux_executor/configuration.ts';
import { linux_executor_path } from '../../../environments/linux_executor/logic/paths.ts';
import { MacUpdateController } from '../updates/controller.ts';
import { HttpSessions } from '../transport/sessions.ts';
import { CONNECTORS } from '../transport/http.ts';
import type { HttpRequest, ReplyReader } from '../transport/contracts.ts';
import { EXECUTOR_HANDOVER_CLOSE_PATH } from '../../configuration.ts';
import { observe_promise } from '../../../core/promise_observer.ts';

const timing = vi.hoisted(() => ({ poll: 0.01 }));
vi.mock('../configuration.ts', async (importOriginal) => {
  const original = await importOriginal<typeof import('../configuration.ts')>();
  return {
    ...original,
    MAC_RUNTIME_READY_TIMEOUT_SECONDS: 1,
    get MAC_UPDATE_POLL_SECONDS() {
      return timing.poll;
    },
  };
});

test.each([
  { platform: 'darwin', trial: 'dies_before_marker' },
  { platform: 'linux', trial: 'dies_before_marker' },
  { platform: 'darwin', trial: 'kept' },
  { platform: 'darwin', trial: 'marker_after_last_poll' },
] as const)(
  '$platform runtime $trial preserves handoff until the supervisor keeps it',
  async ({ platform, trial }) => {
    const original_platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
    const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-supervisor-')));
    const baseline = join(root, 'baseline');
    const base_revision = 'a'.repeat(40),
      next_revision = 'b'.repeat(40);
    const files = new FilesService(),
      owners = new ChildProcesses(),
      one_shot_children = new OneShotChildren(owners);
    const launches = vi.spyOn(owners, 'spawn');
    let controller: MacUpdateController | undefined;
    const closes: unknown[] = [];
    const failure = vi.fn();
    timing.poll = 0.01;
    Object.defineProperty(process, 'platform', { ...original_platform, value: platform });
    try {
      let runtime = join(root, 'candidate');
      const credential = 'e'.repeat(43);
      const credential_path = join(root, 'machine-credential.json');
      writeFileSync(credential_path, JSON.stringify({ credential }), { mode: 0o600 });
      mkdirSync(join(baseline, 'server/executor/mac'), { recursive: true });
      writeFileSync(join(baseline, 'package.json'), '{"type":"module"}');
      if (platform === 'linux') {
        const entrypoint = `
import fs from 'node:fs';import{join}from'node:path';
fs.writeFileSync(join(process.env.PLAN_EXECUTOR_ROOT,'trial.json'),JSON.stringify({
  node:process.execPath,args:process.argv.slice(2),path:process.env.PATH,
  app_root:process.env.PLAN_APP_ROOT,revision:process.env.PLAN_APP_SHA,
  handoff:process.env.PLAN_EXECUTOR_RUNTIME_HANDOFF,ready:process.env.PLAN_EXECUTOR_RUNTIME_READY,
  credentials:Object.keys(process.env).filter(key=>key.toLowerCase().includes('credential'))
}));process.exitCode=1;
`;
        const archive = runtime_archive(next_revision, false, LINUX_EXECUTOR, entrypoint);
        const archive_path = join(root, 'runtime.tar.gz');
        writeFileSync(archive_path, archive.bytes);
        runtime = await files.extract_runtime(
          archive_path,
          join(root, 'releases'),
          archive.sha,
          archive.bytes.length,
          next_revision,
          LINUX_EXECUTOR,
        );
        expect(existsSync(join(runtime, 'node'))).toBe(false);
        expect(existsSync(join(runtime, '.venv'))).toBe(false);
      } else {
        mkdirSync(join(runtime, 'node/bin'), { recursive: true });
        mkdirSync(join(runtime, 'server/executor/mac'), { recursive: true });
        const trial_work =
          trial === 'dies_before_marker'
            ? 'exit 1'
            : `
while [ ! -f "$PLAN_EXECUTOR_ROOT/${trial === 'kept' ? 'first-poll' : 'last-poll'}" ]; do sleep 0.01; done
mkdir -p "$PLAN_EXECUTOR_ROOT/updates/ready"
printf '%s\\n' '{"request_id":"trial","application_revision":"${next_revision}"}' > "$PLAN_EXECUTOR_RUNTIME_READY"
sleep ${trial === 'kept' ? '0.3' : '5'}
exit 0`;
        writeFileSync(
          join(runtime, 'mac-runtime.json'),
          JSON.stringify({
            schema_version: 1,
            application_revision: next_revision,
            platform: 'darwin',
            architecture: 'arm64',
          }),
        );
        writeFileSync(
          join(runtime, 'node/bin/node'),
          `#!/bin/sh
printf '%s\\n' "$PATH" "$PLAN_EXECUTOR_RUNTIME_HANDOFF" "$@" > "$PLAN_EXECUTOR_ROOT/trial.txt"
${trial_work}
`,
          { mode: 0o755 },
        );
        writeFileSync(join(runtime, 'server/executor/mac/entrypoint.ts'), '// scripted trial');
      }
      const pending = {
        request_id: 'trial',
        application_revision: next_revision,
        runtime_path: runtime,
        previous_runtime_path: null,
        previous_revision: base_revision,
        executor_incarnation: '11111111-1111-1111-1111-111111111111',
        connection_generation: 7,
        archive_sha256: 'c'.repeat(64),
      };
      writeFileSync(
        join(baseline, 'server/executor/mac/entrypoint.ts'),
        `
import fs from 'node:fs';import{join}from'node:path';
const root=process.env.PLAN_EXECUTOR_ROOT, path=join(root,'observed.json');
const rows=fs.existsSync(path)?JSON.parse(fs.readFileSync(path,'utf8')):[];
rows.push({args:process.argv.slice(2),path:process.env.PATH,handoff:process.env.PLAN_EXECUTOR_RUNTIME_HANDOFF,
  node:process.execPath,app_root:process.env.PLAN_APP_ROOT,revision:process.env.PLAN_APP_SHA,
  credentials:Object.keys(process.env).filter(key=>key.toLowerCase().includes('credential')),
  failure:JSON.parse(fs.readFileSync(join(root,'updates/failure.json'),'utf8')).request_id});
fs.writeFileSync(path,JSON.stringify(rows));
if(rows.length===1){fs.writeFileSync(join(root,'updates/pending.json'),JSON.stringify(${JSON.stringify(pending)}));process.exitCode=75;}
`,
      );
      await atomic_record(files, join(root, 'updates/pending.json'), {
        ...pending,
        request_id: 'interrupted',
      });
      await atomic_record(files, join(root, 'updates/handoff.json'), {
        executor_incarnation: pending.executor_incarnation,
        connection_generation: 7,
        replay_pending: true,
      });
      const read = files.read_runtime_record.bind(files);
      const remove = files.remove_runtime_record.bind(files);
      let ready_polls = 0;
      let nonempty_ready_polls = 0;
      let late_marker_boundary: number | null = null;
      let marker_at_removal: unknown;
      vi.spyOn(files, 'read_runtime_record').mockImplementation(async (path) => {
        const record = await read(path);
        if (dirname(path) === join(root, 'updates/ready')) {
          ready_polls++;
          if (record === null) writeFileSync(join(root, 'first-poll'), 'observed');
          else nonempty_ready_polls++;
        }
        return record;
      });
      vi.spyOn(files, 'remove_runtime_record').mockImplementation(async (path) => {
        if (dirname(path) === join(root, 'updates/ready') && trial === 'marker_after_last_poll') {
          late_marker_boundary = ready_polls;
          writeFileSync(join(root, 'last-poll'), 'completed');
          await vi.waitFor(
            () => {
              expect(JSON.parse(readFileSync(path, 'utf8'))).toEqual({
                request_id: 'trial',
                application_revision: next_revision,
              });
            },
            { timeout: 2000, interval: 10 },
          );
        }
        if (dirname(path) === join(root, 'updates/ready') && existsSync(path))
          marker_at_removal = JSON.parse(readFileSync(path, 'utf8'));
        await remove(path);
      });
      class Home extends HttpSessions {
        constructor() {
          super('https://fixture.invalid', CONNECTORS);
        }
        override async request<T>(
          input: HttpRequest,
          headers: Readonly<Record<string, string>>,
          consume: ReplyReader<T>,
        ): Promise<T> {
          expect(headers['X-Panels-Executor-Credential']).toBe(credential);
          expect(input.method).toBe('POST');
          if (input.path === EXECUTOR_HANDOVER_CLOSE_PATH) {
            const selected = JSON.parse(readFileSync(join(root, 'updates/selection.json'), 'utf8'));
            expect(selected).toEqual(pending);
            expect(existsSync(join(root, 'updates/pending.json'))).toBe(false);
            closes.push(selected);
          } else expect(input.path).toBe('/api/executor/update');
          const response = new IncomingMessage(new Socket());
          response.statusCode = 200;
          response.complete = true;
          response.push(
            Buffer.from(
              input.path === EXECUTOR_HANDOVER_CLOSE_PATH
                ? '{"closed":true}'
                : '{"confirmed":true}',
            ),
          );
          response.push(null);
          try {
            return await consume(response);
          } finally {
            response.destroy();
          }
        }
      }
      controller = new MacUpdateController({
        paths: new ExecutorPaths(root),
        files,
        runner: {
          planned_update_replay: false,
          prepare_update: async () => true,
          finish_planned_replay() {},
          resume_after_cancelled_update() {},
        },
        presence: {
          identity: {
            protocol_version: 22,
            spool_format_version: 5,
            application_revision: next_revision,
            executor_incarnation: pending.executor_incarnation,
            connection_generation: 7,
          },
          leased: () => true,
        },
        outbox: { pending_count: () => 0 },
        runtime: {
          child: true,
          ready_path: join(root, 'controller-ready.json'),
          handoff_path: null,
        },
        authentication_headers: () => ({ 'X-Panels-Executor-Credential': credential }),
        session_factory: () => new Home(),
        request_exit() {},
        on_failure: failure,
      });
      await controller.apply({
        schema_version: 1,
        prefetch: null,
        target: {
          ...pending,
          archive_path: '/api/executor/update/trial/archive',
          archive_size: 1,
          mode: 'idle',
          activate: false,
        },
      });
      const handover = controller.close_handover_once_kept();
      const args = [
        'executor',
        'run',
        '--executor-id',
        'synthetic-machine',
        '--application-revision',
        base_revision,
        '--panels-server-url',
        'https://home.invalid',
      ];
      expect(
        await supervise(args, {
          paths: new ExecutorPaths(root),
          baseline_root: baseline,
          baseline_node: process.execPath,
          environment: { PATH: '/usr/bin:/bin', HOME: root },
          files,
          child_processes: owners,
          one_shot_children,
          signal: new AbortController().signal,
        }),
      ).toBe(0);
      await owners.settled();
      if (trial === 'kept') {
        expect((await observe_promise(handover, 2000)).completed).toBe(true);
        expect(closes).toEqual([pending]);
      } else {
        expect(await controller.kept_by_supervisor()).toBe(false);
        expect(closes).toEqual([]);
      }
      if (trial === 'marker_after_last_poll') {
        expect(ready_polls).toBeGreaterThan(0);
        expect(nonempty_ready_polls).toBe(0);
        expect(late_marker_boundary).toBe(ready_polls);
        expect(marker_at_removal).toEqual({
          request_id: 'trial',
          application_revision: next_revision,
        });
      }
      expect(failure).not.toHaveBeenCalled();
      const observed = JSON.parse(readFileSync(join(root, 'observed.json'), 'utf8'));
      const forwarded = args.map((value) => (value === base_revision ? next_revision : value));
      expect(launches.mock.calls.map(([node]) => node)).toEqual([
        process.execPath,
        platform === 'linux' ? process.execPath : join(runtime, 'node/bin/node'),
        ...(trial === 'kept' ? [] : [process.execPath]),
      ]);
      for (const [, argv, options] of launches.mock.calls) {
        expect(argv).not.toContain(credential);
        expect(options?.env).toBeDefined();
        expect(Object.values(options!.env!)).not.toContain(credential);
      }
      if (platform === 'linux') {
        const trial = JSON.parse(readFileSync(join(root, 'trial.json'), 'utf8'));
        expect(trial).toMatchObject({
          node: process.execPath,
          args: forwarded,
          path: linux_executor_path(runtime, process.execPath, root),
          app_root: runtime,
          revision: next_revision,
          handoff: join(root, 'updates/handoff.json'),
          credentials: [],
        });
        expect(dirname(trial.ready)).toBe(join(root, 'updates/ready'));
        expect(existsSync(trial.ready)).toBe(false);
        expect(launches.mock.calls[1]![1]).toEqual([
          join(runtime, LINUX_EXECUTOR_ENTRYPOINT),
          ...forwarded,
        ]);
        for (const row of observed) {
          expect(row.node).toBe(process.execPath);
          expect(row.path).toBe(linux_executor_path(baseline, process.execPath, root));
          expect(row.app_root).toBe(baseline);
          expect(row.revision).toBe(base_revision);
          expect(row.credentials).toEqual([]);
        }
      } else {
        const trial = readFileSync(join(root, 'trial.txt'), 'utf8').trimEnd().split('\n');
        expect(trial[0]).toBe(join(runtime, 'bin') + ':/usr/bin:/bin');
        expect(trial.slice(1)).toEqual([
          join(root, 'updates/handoff.json'),
          join(runtime, 'server/executor/mac/entrypoint.ts'),
          ...forwarded,
        ]);
      }
      expect(observed.map((row: { failure: string }) => row.failure)).toEqual([
        'interrupted',
        ...(trial === 'kept' ? [] : ['trial']),
      ]);
      expect(
        observed.every(
          (row: { args: string[]; path: string; handoff: string }) =>
            JSON.stringify(row.args) === JSON.stringify(args) &&
            row.path.startsWith(join(baseline, 'bin') + ':') &&
            row.handoff === join(root, 'updates/handoff.json'),
        ),
      ).toBe(true);
      expect(JSON.parse(readFileSync(join(root, 'updates/failure.json'), 'utf8'))).toMatchObject({
        request_id: trial === 'kept' ? 'interrupted' : 'trial',
        phase: 'reverted',
      });
      expect(
        JSON.parse(readFileSync(join(root, 'updates/handoff.json'), 'utf8')).replay_pending,
      ).toBe(true);
      expect(JSON.parse(readFileSync(credential_path, 'utf8'))).toEqual({ credential });
    } finally {
      try {
        if (controller) {
          await controller.stop({ deadline: performance.now() / 1000 + 10 });
          await controller.settled();
        }
        one_shot_children.seal();
        owners.seal();
        await one_shot_children.settled();
        await owners.settled();
        await files.stop({ deadline: performance.now() / 1000 + 10 });
        await files.settled();
      } finally {
        launches.mockRestore();
        Object.defineProperty(process, 'platform', original_platform);
        rmSync(root, { recursive: true, force: true });
      }
    }
  },
);
