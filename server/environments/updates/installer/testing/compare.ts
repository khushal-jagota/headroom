/** Export-only comparison driver. No service, health listener, or fixture app executes. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import type { SpawnOptions } from 'node:child_process';
import { ChildProcesses } from '../../../../lifecycle/owned_processes.ts';
import { INSTALLED_RUNTIME_CLASSIFIER_SOURCE } from '../../../release/units.ts';
import { install } from '../install.ts';
import { create_installer_effects } from '../commands.ts';
import type { InstallerEffects, InstallerRequest } from '../contracts.ts';

const FROM = 'a'.repeat(40),
  TO = 'b'.repeat(40),
  ATTEMPT = 'attempt-1';
interface Case {
  root: string;
  scenario: string;
  capture_detail: string;
}
interface Input {
  cases: readonly Case[];
  configured_root: string;
  now_ms: number;
}

function tree(root: string): Record<string, unknown>[] {
  const rows: Record<string, unknown>[] = [];
  function visit(directory: string, prefix: string): void {
    for (const name of fs.readdirSync(directory).sort()) {
      const relative = prefix + name,
        path = join(directory, name),
        info = fs.lstatSync(path);
      const row: Record<string, unknown> = { path: relative, mode: info.mode & 0o7777 };
      if (info.isSymbolicLink())
        Object.assign(row, { kind: 'symlink', target: fs.readlinkSync(path) });
      else if (info.isDirectory()) Object.assign(row, { kind: 'directory' });
      else Object.assign(row, { kind: 'file', bytes: fs.readFileSync(path).toString('hex') });
      rows.push(row);
      if (info.isDirectory()) visit(path, relative + '/');
    }
  }
  visit(root, '');
  return rows.sort((a, b) =>
    String(a.path) < String(b.path) ? -1 : String(a.path) > String(b.path) ? 1 : 0,
  );
}
function request(root: string): InstallerRequest {
  return {
    current_root: root,
    database: join(root, 'data/planner.db'),
    data_directory: join(root, 'data'),
    attempt_id: ATTEMPT,
    service_unit: 'synthetic.service',
    health_url: 'http://127.0.0.1:0',
    health_timeout_seconds: 3,
    node_executable: process.execPath,
    admitted_legacy_null_sha: null,
  };
}
function write(path: string, text: string, mode = 0o644): void {
  fs.mkdirSync(join(path, '..'), { recursive: true });
  fs.writeFileSync(path, text, { mode });
}
/** Only the classifier consumes this structure. This is not an admitted release. */
function structural_app(app: string, sha: string): void {
  const contract = { version: 2 };
  write(
    join(app, 'manifest.json'),
    JSON.stringify({
      format: 'panels-app-v1',
      app_sha: sha,
      source_digest: 'd'.repeat(64),
      artifact_digest: 'e'.repeat(64),
      runtime_contract: contract,
    }),
  );
  write(join(app, 'runtime/contract.json'), JSON.stringify(contract));
  for (const file of ['bin/panels', 'bin/panels-launcher'])
    write(join(app, file), '#!/bin/sh\nexit 99\n', 0o755);
  for (const file of [
    'server/environments/release/launcher_main.ts',
    'server/lifecycle/foreground_main.ts',
    'server/lifecycle/managed_main.ts',
    'server/lifecycle/application.ts',
    'server/lifecycle/stop_main.ts',
    'server/environments/updates/interruption_capture_main.ts',
    'server/environments/updates/restore_main.ts',
    'runtime/installer-files.json',
    'server/environments/updates/installer/main.ts',
  ])
    write(join(app, file), '// Inert structural routing fixture. Never execute.\n');
}

export async function installer_probe(input: Input) {
  let stopping = false,
    force_error: unknown;
  let active: { owner: ChildProcesses | null; controller: AbortController } | undefined;
  const on_term = () => {
    stopping = true;
    active?.owner?.seal();
    active?.controller.abort(new Error('Comparison interrupted'));
    try {
      active?.owner?.force_kill(performance.now() / 1000);
    } catch (error) {
      force_error = error;
    }
  };
  const check_stop = () => {
    if (stopping) throw new Error('Comparison interrupted');
  };
  process.on('SIGTERM', on_term);
  const RealDate = globalThis.Date,
    original_rename = fs.renameSync;
  class FrozenDate extends RealDate {
    constructor(value?: string | number) {
      super(value === undefined ? input.now_ms : value);
    }
    static now(): number {
      return input.now_ms;
    }
  }
  globalThis.Date = FrozenDate as DateConstructor;
  try {
    const canonical = [];
    for (const spec of input.cases) {
      check_stop();
      const value = request(spec.root),
        events: string[][] = [],
        controller = new AbortController();
      active = { owner: null, controller };
      let stderr = '';
      fs.renameSync = (source, destination) => {
        if (
          spec.scenario === 'rename' &&
          source === join(spec.root, '.app-next-' + TO) &&
          destination === join(spec.root, 'app')
        )
          throw Object.assign(new Error('Input/output error'), {
            code: 'EIO',
            errno: -5,
            syscall: 'rename',
            path: source,
            dest: destination,
          });
        return original_rename(source, destination);
      };
      syncBuiltinESMExports();
      const effects: InstallerEffects = {
        async service(action) {
          events.push(['service', action]);
        },
        async capture() {
          events.push(['capture']);
          return spec.scenario === 'rename'
            ? { kind: 'unavailable', detail: spec.capture_detail }
            : { kind: 'returned', returncode: spec.scenario === 'rollback' ? 7 : 0 };
        },
        async restore(snapshot) {
          events.push(['restore', snapshot, value.database]);
          fs.copyFileSync(join(snapshot, 'database.sqlite'), value.database);
        },
        async healthy(sha) {
          events.push(['health', sha]);
          return spec.scenario !== 'rollback' || sha === FROM;
        },
      };
      try {
        const code = await install(
          value,
          effects,
          {
            stderr: (text) => {
              stderr += text;
            },
          },
          controller.signal,
        );
        canonical.push({ code, events, stderr, tree: tree(spec.root) });
      } finally {
        fs.renameSync = original_rename;
        syncBuiltinESMExports();
        active = undefined;
      }
    }
    check_stop();
    const root = input.configured_root,
      current = join(root, 'current');
    const value: InstallerRequest = {
      ...request(current),
      database: join(root, 'database/custom-name.sqlite'),
      data_directory: join(root, 'attempt-records'),
    };
    structural_app(join(current, 'app'), FROM);
    structural_app(join(current, '.app-next-' + TO), TO);
    write(value.database, 'configured database remains unchanged');
    const snapshot = join(root, 'snapshot');
    fs.mkdirSync(snapshot, { recursive: true });
    const record_path = join(value.data_directory, 'update-attempt.json');
    write(
      record_path,
      JSON.stringify({
        attempt_id: ATTEMPT,
        state: 'installing',
        from_sha: FROM,
        to_sha: TO,
        backup: snapshot,
        steps: {},
      }),
    );
    assert.equal(fs.existsSync(join(current, 'data')), false);
    const owner = new ChildProcesses(),
      controller = new AbortController(),
      abort_reason = new Error('restore witness ready');
    active = { owner, controller };
    const spawn = owner.spawn.bind(owner),
      ready = join(root, 'restore-ready');
    const counts = {
      spawns: 0,
      closes: 0,
      classifiers: 0,
      capture_witnesses: 0,
      restore_witnesses: 0,
    };
    const events: string[][] = [];
    let restore_closed = false,
      ready_flight: Promise<void> | undefined;
    const started = performance.now();
    owner.spawn = ((command: string, args: readonly string[], options: SpawnOptions) => {
      check_stop();
      assert.equal(command, process.execPath);
      assert.equal(options.env?.HOME, process.env.HOME);
      assert.equal(options.env?.PLAN_DB_PATH, process.env.PLAN_DB_PATH);
      let witness: readonly string[] = args;
      if (args[0] === '--input-type=module') {
        assert.deepEqual(args, [
          '--input-type=module',
          '-e',
          INSTALLED_RUNTIME_CLASSIFIER_SOURCE,
          join(current, 'app'),
          '',
        ]);
        counts.classifiers++;
      } else if (
        args[0] === join(current, 'app/server/environments/updates/interruption_capture_main.ts')
      ) {
        assert.deepEqual(args.slice(1, 3), [value.database, ATTEMPT]);
        assert.match(args[3]!, /^[a-f0-9]{32}$/);
        assert.equal(args.length, 4);
        assert.deepEqual(options.stdio, ['ignore', 'ignore', 'ignore']);
        counts.capture_witnesses++;
        witness = ['-e', 'process.exitCode=7'];
      } else {
        assert.deepEqual(args, [
          join(current, 'app/server/environments/updates/restore_main.ts'),
          snapshot,
          value.database,
        ]);
        assert.deepEqual(options.stdio, ['ignore', 'inherit', 'inherit']);
        assert.equal(
          JSON.parse(fs.readFileSync(join(current, 'app/manifest.json'), 'utf8')).app_sha,
          FROM,
        );
        counts.restore_witnesses++;
        witness = [
          '-e',
          "require('node:fs').writeFileSync(process.argv[1],'ready');setInterval(()=>{},1000)",
          ready,
        ];
      }
      const child = spawn(command, witness, options);
      counts.spawns++;
      const restore = counts.restore_witnesses > 0;
      child.once('close', () => {
        counts.closes++;
        if (restore) restore_closed = true;
      });
      if (restore)
        ready_flight = (async () => {
          try {
            const deadline = performance.now() + 3000;
            while (!fs.existsSync(ready)) {
              check_stop();
              assert(performance.now() < deadline, 'restore witness did not become ready');
              await delay(5);
            }
            owner.seal();
            controller.abort(abort_reason);
          } catch (error) {
            owner.seal();
            controller.abort(error);
            try {
              owner.force_kill(performance.now() / 1000);
            } catch (failure) {
              force_error = failure;
            }
          }
        })();
      return child;
    }) as ChildProcesses['spawn'];
    try {
      const effects = create_installer_effects(value, {
        child_processes: owner,
        signal: controller.signal,
        environment: { ...process.env },
      });
      effects.service = async (action) => {
        events.push(['service', action]);
      };
      effects.healthy = async (sha) => {
        events.push(['health', sha]);
        return false;
      };
      let stderr = '';
      await assert.rejects(
        install(
          value,
          effects,
          {
            stderr: (text) => {
              stderr += text;
            },
          },
          controller.signal,
        ),
        (error) => {
          assert.equal(error, abort_reason);
          assert(restore_closed, 'installer returned before the exact restore child closed');
          return true;
        },
      );
      await ready_flight;
      await owner.settled();
      assert.deepEqual(events, [
        ['service', 'stop'],
        ['service', 'start'],
        ['health', TO],
        ['service', 'stop'],
      ]);
      assert.equal(stderr, '');
      assert.deepEqual(counts, {
        spawns: 4,
        closes: 4,
        classifiers: 2,
        capture_witnesses: 1,
        restore_witnesses: 1,
      });
      const record = JSON.parse(fs.readFileSync(record_path, 'utf8'));
      assert.equal(record.state, 'installing');
      assert.deepEqual(
        Object.keys(record.steps).sort(),
        [
          'installer_started',
          'service_stopped',
          'interruptions_captured',
          'app_swapped',
          'service_started',
          'rollback_service_stopped',
          'rollback_app_restored',
        ].sort(),
      );
      assert(fs.statSync(record_path + '.lock').isFile());
      assert.equal(fs.existsSync(join(current, 'data')), false);
      assert.equal(
        fs.readFileSync(value.database, 'utf8'),
        'configured database remains unchanged',
      );
      return {
        canonical,
        configured: {
          ...counts,
          restore_closed,
          canonical_data_absent: true,
          configured_seconds: (performance.now() - started) / 1000,
        },
      };
    } finally {
      owner.seal();
      controller.abort();
      try {
        owner.force_kill(performance.now() / 1000);
      } finally {
        await owner.settled();
        await ready_flight;
        active = undefined;
      }
    }
  } finally {
    globalThis.Date = RealDate;
    fs.renameSync = original_rename;
    syncBuiltinESMExports();
    process.removeListener('SIGTERM', on_term);
    if (force_error) throw force_error;
  }
}
