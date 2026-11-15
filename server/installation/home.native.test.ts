/** Hosted service-manager proof. The mini app is not a sealed release/admission proof. */
import { test, expect } from 'vitest';
import { randomUUID } from 'node:crypto';
import {
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readlink,
  realpath,
  rm,
  rmdir,
  unlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir, userInfo } from 'node:os';
import { basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { install_home, check_home_port } from './home.ts';
import { create_linux_home_service } from './linux.ts';
import { create_darwin_home_service } from './darwin.ts';
import type { HomeInstallDependencies, HomeServicePlan, HomeCommand } from './contracts.ts';
import { ChildProcesses, OneShotChildren } from '../lifecycle/owned_processes.ts';
import { observe_promise } from '../core/promise_observer.ts';
import { run_release_command } from '../environments/release/process.ts';
import { healthy } from '../environments/updates/installer/health.ts';
import { MAC_HOME } from '../environments/release/contracts.ts';
import { extract_app_archive } from '../environments/release/archive.ts';
import { admit_runtime } from '../environments/release/admission.ts';
import { build_native_mini_homes, native_copied_updates } from './testing/darwin_home.native.ts';
import type { AppManifest, ReleaseExecution } from '../environments/release/contracts.ts';
import { process_inspector } from '../previews/platform.ts';
import { DarwinWorkerProcesses } from '../platform_runtime/darwin/worker_processes.ts';
import { LinuxWorkerProcesses } from '../platform_runtime/linux/worker_processes.ts';
import type { WorkerProcess } from '../worker_supervision/contracts.ts';

const admitted =
  process.env.GITHUB_ACTIONS === 'true' && process.env.PANELS_NATIVE_HOME_TEST === '1';
const revision = 'a'.repeat(40);
const supervisor = fileURLToPath(new URL('../lifecycle/supervisor.ts', import.meta.url));
const paths = fileURLToPath(new URL('../lifecycle/paths.ts', import.meta.url));

async function wait_for<T>(read: () => Promise<T | undefined>, seconds = 15): Promise<T> {
  const deadline = performance.now() + seconds * 1000;
  while (performance.now() < deadline) {
    const value = await read();
    if (value !== undefined) return value;
    await delay(50);
  }
  throw new Error('Native home observation exceeded its deadline');
}
async function free_port(): Promise<number> {
  const server = createServer();
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      server.close((error) =>
        error
          ? reject(error)
          : address && typeof address !== 'string'
            ? resolve(address.port)
            : reject(new Error('Missing fixture port')),
      );
    });
  });
}

test.skipIf(!admitted)(
  'actual first-home service starts, rolls back, and releases only its owned jobs',
  async () => {
    expect(['linux', 'darwin']).toContain(process.platform);
    const system = process.platform as 'linux' | 'darwin';
    expect(process.arch).toBe(system === 'darwin' ? 'arm64' : 'x64');
    const evidence = process.env.NATIVE_EVIDENCE;
    if (!evidence) throw new Error('Native home proof requires an evidence directory');
    const base = await realpath(await mkdtemp(join(tmpdir(), 'p7home-')));
    const account = system === 'linux' ? userInfo().homedir : join(base, 'account');
    await mkdir(join(base, 'account'), { mode: 0o700 });
    const report: Record<string, unknown> = {
      system,
      architecture: process.arch,
      boundary:
        'Actual installer and native services; injected mini-app extraction/admission. No sealed-package claim.',
      cases: [],
    };
    const cases = report.cases as Record<string, unknown>[];
    const build_processes = new ChildProcesses();
    let mini_builds: Awaited<ReturnType<typeof build_native_mini_homes>> | undefined;
    try {
      if (system === 'darwin') {
        mini_builds = await build_native_mini_homes(base, {
          child_processes: build_processes,
          signal: AbortSignal.timeout(360_000),
          deadline: performance.now() / 1000 + 360,
        });
        report.mini_builds = mini_builds;
        report.boundary =
          'Real sealed mini-home archives, admission, launchd and copied updater. Production archive foreground proof is separate.';
      }
      for (const fault of ['none', 'health', 'auxiliary'] as const) {
        const started = performance.now(),
          record: Record<string, unknown> = { fault };
        cases.push(record);
        const token = randomUUID().replaceAll('-', ''),
          // Keep Linux's root-local Unix socket within its native pathname limit.
          root = join(base, token.slice(0, 12)),
          service_name = 'panels-native-' + token;
        const sentinel = join(base, 'foreign-' + token);
        await writeFile(sentinel, 'unrelated sentinel', { mode: 0o600 });
        const processes = new ChildProcesses(),
          commands = new OneShotChildren(processes);
        const primitives =
          system === 'darwin'
            ? new DarwinWorkerProcesses({ children: commands })
            : new LinuxWorkerProcesses();
        const births: WorkerProcess[] = [];
        const execution = (): ReleaseExecution => ({
          child_processes: processes,
          signal: AbortSignal.timeout(120_000),
          deadline: performance.now() / 1000 + 120,
        });
        let plan: HomeServicePlan | undefined,
          installed = false,
          clean = false,
          activated = false,
          retain_resources = false;
        let root_identity: { dev: number; ino: number } | undefined;
        const retained = new Map<string, { dev: number; ino: number; content: Buffer | string }>();
        const assert_owned = async (path: string): Promise<void> => {
          const proof = retained.get(path),
            stat = await lstat(path);
          if (
            !proof ||
            stat.dev !== proof.dev ||
            stat.ino !== proof.ino ||
            (stat.isSymbolicLink()
              ? (await readlink(path)) !== proof.content
              : !(await readFile(path)).equals(proof.content as Buffer))
          )
            throw new Error(`Native proof resource changed: ${path}`);
        };
        const capture = async () => {
          const current = await lstat(root);
          root_identity ??= { dev: current.dev, ino: current.ino };
          if (
            !current.isDirectory() ||
            current.dev !== root_identity.dev ||
            current.ino !== root_identity.ino
          )
            throw new Error('Native fixture installation root changed');
          for (const file of [...(plan?.files ?? []), ...(plan?.links ?? [])]) {
            if (retained.has(file.path)) continue;
            const stat = await lstat(file.path);
            retained.set(file.path, {
              dev: stat.dev,
              ino: stat.ino,
              content: stat.isSymbolicLink()
                ? await readlink(file.path)
                : await readFile(file.path),
            });
          }
        };
        const command: HomeCommand = async (argv, scope, seconds, allow_nonzero = false) => {
          const activation = argv.includes('bootstrap') || argv.includes('enable');
          if (activation) {
            await capture();
            activated = true;
          }
          if (
            system === 'darwin' &&
            fault === 'health' &&
            activation &&
            argv.at(-1)?.endsWith('/' + service_name + '.plist')
          )
            await writeFile(
              join(root, 'data/probe-policy.json'),
              JSON.stringify({ fail_sha: mini_builds!.a.manifest.app_sha }),
            );
          if (fault === 'auxiliary' && activation && argv.at(-1)?.includes('.backup'))
            throw new Error('Native fixture auxiliary activation refused');
          return run_release_command(
            argv,
            {
              timeout_seconds: seconds,
              allow_nonzero,
              environment: {
                PATH: process.env.PATH,
                XDG_RUNTIME_DIR: process.env.XDG_RUNTIME_DIR,
                DBUS_SESSION_BUS_ADDRESS: process.env.DBUS_SESSION_BUS_ADDRESS,
                HOME: account,
              },
            },
            scope,
          );
        };
        const request = {
          root,
          service: service_name,
          port: await free_port(),
          isolated_providers: true,
          archive: mini_builds?.a.archive ?? join(base, 'mini-app-fixture'),
          archive_sha256: mini_builds?.a.archive_sha256 ?? 'b'.repeat(64),
          revision: mini_builds?.a.manifest.app_sha ?? revision,
        };
        Object.assign(record, { root, service: service_name, port: request.port });
        const inspector = process_inspector();
        const observe = async () => {
          const identity = await inspector.discover(request.port, root);
          const snapshot = await primitives.snapshot(performance.now() / 1000 + 5);
          const app = snapshot.get(identity.pid),
            parent = app && snapshot.get(app.parent);
          if (!app || !parent) throw new Error('Native fixture process births are missing');
          if (system === 'darwin') expect(app.group).toBe(parent.group);
          births.push(app, parent);
          return { app, parent };
        };
        const deps: HomeInstallDependencies = {
          system,
          architecture: system === 'linux' ? 'x86_64' : 'arm64',
          account_home: account,
          node_executable: await realpath(process.execPath),
          uid: process.getuid!(),
          environment: { PATH: process.env.PATH, PLAN_SERVER_URL: 'http://127.0.0.1:1' },
          execution: execution(),
          command,
          warning: (message) => {
            throw new Error(message);
          },
          service: async (value, host) => {
            plan = await (system === 'darwin'
              ? create_darwin_home_service(value, host)
              : create_linux_home_service(value, host));
            return plan;
          },
          extract: async (_archive, destination, digest, sha, scope) => {
            if (system === 'darwin')
              return extract_app_archive(
                _archive,
                destination,
                digest,
                sha,
                scope.child_processes,
                undefined,
                scope,
                MAC_HOME,
              );
            await mkdir(join(destination, 'bin'), { recursive: true });
            const wrapper = `#!${process.execPath}\nimport{run_server_supervisor}from ${JSON.stringify(supervisor)};\nimport{native_control_socket_path}from ${JSON.stringify(paths)};\nimport{join}from'node:path';\nif(process.argv[2]!=='serve')process.exit(0);\nconst root=process.cwd();process.exitCode=await run_server_supervisor({mode:{kind:'foreground'},application:{node_executable:process.execPath,entrypoint:join(root,'app/app.mjs'),launch_root:root,environment:{...process.env}},control_socket_path:native_control_socket_path(join(root,'data/control.sock')),lease_path:join(root,'data/lease'),port:Number(process.env.PLAN_PORT)});\n`;
            await writeFile(join(destination, 'package.json'), '{"type":"module"}\n');
            for (const name of ['panels', 'panels-launcher'])
              await writeFile(join(destination, 'bin', name), wrapper, { mode: 0o700 });
            await writeFile(
              join(destination, 'app.mjs'),
              `import{createServer}from'node:http';const server=createServer((req,res)=>{res.setHeader('content-type','application/json');res.end(JSON.stringify({app_sha:${JSON.stringify(revision)},ready:${fault !== 'health'}}));});server.listen(Number(process.env.PLAN_PORT),'127.0.0.1');process.on('SIGTERM',()=>server.close(()=>process.exit(0)));\n`,
            );
            return {
              app_sha: revision,
              artifact_digest: 'c'.repeat(64),
              source_digest: 'd'.repeat(64),
              // Admission is injected. Only the orchestration's Node-version boundary is real.
              runtime_contract: { version: 2 } as AppManifest['runtime_contract'],
            };
          },
          admit: async (candidate, manifest, scope) => {
            if (system === 'darwin')
              await admit_runtime(candidate, manifest, scope.child_processes);
          },
          healthy: async (port, sha, signal) => {
            await wait_for(async () => {
              try {
                return await observe();
              } catch {
                return undefined;
              }
            });
            return healthy(
              `http://127.0.0.1:${port}/api/health`,
              sha,
              fault === 'health' ? 1 : 15,
              signal,
              deps.environment,
              true,
            );
          },
        };
        try {
          if (fault === 'none') {
            expect(await install_home(request, deps)).toBe(`http://127.0.0.1:${request.port}`);
            installed = true;
            record.install_seconds = (performance.now() - started) / 1000;
            if (system === 'darwin') {
              const identity = JSON.parse(
                await readFile(join(root, 'data/mac-home-installation.json'), 'utf8'),
              );
              expect(Object.keys(identity).sort()).toEqual(
                [
                  'version',
                  'uid',
                  'root',
                  'label',
                  'home_plist',
                  'backup_plist',
                  'installer_node',
                ].sort(),
              );
              expect(identity).toMatchObject({
                version: 2,
                root,
                uid: deps.uid,
                label: service_name,
                installer_node: await realpath(process.execPath),
              });
              const old = await observe(),
                began = performance.now(),
                target = `gui/${deps.uid}/${service_name}`;
              const info = await command(['/bin/launchctl', 'print', target], execution(), 10);
              expect(
                info.stdout
                  .split('\n')
                  .some((line) => line.trim() === `path = ${plan!.files[0]!.path}`),
              ).toBe(true);
              const snapshot = await primitives.snapshot(performance.now() / 1000 + 5);
              expect(snapshot.get(old.parent.pid)?.birth).toBe(old.parent.birth);
              await command(['/bin/launchctl', 'kill', 'SIGKILL', target], execution(), 10);
              await wait_for(async () =>
                (await primitives.is_absent(old.app, performance.now() / 1000 + 5))
                  ? true
                  : undefined,
              );
              await wait_for(async () => {
                try {
                  const next = await observe();
                  return next.parent.birth !== old.parent.birth ? next : undefined;
                } catch {
                  return undefined;
                }
              });
              expect(
                await healthy(
                  `http://127.0.0.1:${request.port}/api/health`,
                  request.revision,
                  15,
                  deps.execution.signal,
                  deps.environment,
                  true,
                ),
              ).toBe(true);
              record.crash_recovery_seconds = (performance.now() - began) / 1000;
              try {
                await native_copied_updates({
                  root,
                  account,
                  label: service_name,
                  port: request.port,
                  base,
                  builds: mini_builds!,
                  execution,
                  primitives: primitives as DarwinWorkerProcesses,
                  births,
                  record,
                });
              } catch (error) {
                retain_resources = true;
                throw error;
              }
            }
          } else {
            await expect(install_home(request, deps)).rejects.toThrow(
              fault === 'health' ? /ready release/ : /auxiliary activation refused/,
            );
            await expect(lstat(root)).rejects.toMatchObject({ code: 'ENOENT' });
          }
        } finally {
          try {
            const existing = await lstat(plan?.files[0]?.path ?? root).then(
              () => true,
              (error: NodeJS.ErrnoException) => {
                if (error.code === 'ENOENT') return false;
                throw error;
              },
            );
            if (activated && existing) {
              await plan!.stop(execution(), assert_owned);
              if (!installed) {
                record.cleanup_intervention = true;
                throw new Error(
                  'Installer cleanup required native probe intervention. Retained ' + root,
                );
              }
              if (!retain_resources)
                for (const path of [...retained.keys()].reverse()) {
                  await assert_owned(path);
                  await unlink(path);
                }
              if (system === 'linux')
                await command(['systemctl', '--user', 'daemon-reload'], execution(), 30);
            }
            if (system === 'darwin') {
              for (const link of plan?.links ?? []) {
                const result = await command(
                  ['/bin/launchctl', 'print', `gui/${deps.uid}/${basename(link.path, '.plist')}`],
                  execution(),
                  10,
                  true,
                );
                expect(result.code).not.toBe(0);
                expect(result.stderr).toContain('Could not find service');
              }
            } else {
              for (const file of plan?.files ?? []) {
                const result = await command(
                  ['systemctl', '--user', 'is-active', basename(file.path)],
                  execution(),
                  10,
                  true,
                );
                expect(['inactive', 'failed']).toContain(result.stdout.trim());
              }
            }
            await wait_for(async () =>
              (
                await Promise.all(
                  births.map((birth) => primitives.is_absent(birth, performance.now() / 1000 + 5)),
                )
              ).every(Boolean)
                ? true
                : undefined,
            );
            await check_home_port(request.port);
            expect(await readFile(sentinel, 'utf8')).toBe('unrelated sentinel');
            clean = true;
            record.cleanup_verified = true;
          } finally {
            commands.seal();
            processes.seal();
            expect(
              (
                await observe_promise(
                  Promise.all([commands.settled(), processes.settled()]),
                  AbortSignal.timeout(5000),
                )
              ).completed,
            ).toBe(true);
            record.seconds = (performance.now() - started) / 1000;
            record.resource_paths = [...retained.keys()];
            if (system === 'darwin')
              await cp(join(root, 'logs'), join(evidence, 'home-logs-' + token), {
                recursive: true,
              }).catch((error: NodeJS.ErrnoException) => {
                if (error.code !== 'ENOENT') throw error;
              });
            if (clean && !retain_resources) {
              if (installed) {
                const current = await lstat(root);
                if (
                  !root_identity ||
                  !current.isDirectory() ||
                  current.dev !== root_identity.dev ||
                  current.ino !== root_identity.ino
                )
                  throw new Error('Native fixture root changed before removal');
                await rm(root, { recursive: true });
              }
              await unlink(sentinel);
            }
          }
        }
      }
    } catch (error) {
      report.failure = error instanceof Error ? error.stack : String(error);
      throw error;
    } finally {
      try {
        build_processes.seal();
        build_processes.force_kill(performance.now() / 1000 + 5);
        expect(
          (await observe_promise(build_processes.settled(), AbortSignal.timeout(5000))).completed,
        ).toBe(true);
      } catch (error) {
        report.build_cleanup_error = String(error);
        throw error;
      } finally {
        report.fixture_root = base;
        await mkdir(evidence, { recursive: true });
        await writeFile(
          join(evidence, `first-home-${system}.json`),
          JSON.stringify(report, null, 2) + '\n',
        );
        // Failed cleanup retains fixture paths. The report records their exact root.
        await rmdir(base).catch((error: NodeJS.ErrnoException) => {
          if (error.code !== 'ENOTEMPTY') throw error;
        });
      }
    }
  },
  600_000,
);
