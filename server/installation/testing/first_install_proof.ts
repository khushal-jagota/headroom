/** Hosted proof of the real bootstrap, native services, and a second isolated machine. */
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { cp, lstat, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { ChildProcesses, OneShotChildren } from '../../lifecycle/owned_processes.ts';
import { DarwinWorkerProcesses } from '../../platform_runtime/darwin/worker_processes.ts';
import { LinuxWorkerProcesses } from '../../platform_runtime/linux/worker_processes.ts';
import type { WorkerProcess } from '../../worker_supervision/contracts.ts';
import { check_home_port } from '../home.ts';
import { run_release_command } from '../../environments/release/process.ts';
import type { ReleaseExecution } from '../../environments/release/contracts.ts';
import { shell_quote } from '../../conversation/worker_unit/logic.ts';
import { accepted_lease } from '../../environments/linux_executor/logic/lease.ts';
import { executor_link_socket_path } from '../../core/http/executor_link.ts';
import { mac_installation_paths } from '../../executor/mac/platform.ts';

assert.equal(process.env.GITHUB_ACTIONS, 'true', 'This proof requires a disposable hosted runner');
const system = process.platform;
assert.ok(
  (system === 'linux' && process.arch === 'x64') ||
    (system === 'darwin' && process.arch === 'arm64'),
);
const base = process.env.FIRST_INSTALL_ROOT!;
assert.ok(base && base.startsWith(process.env.RUNNER_TEMP!));
const evidence = join(base, 'evidence');
await mkdir(evidence, { recursive: true });
const source = process.env.GITHUB_WORKSPACE!;
const revision = process.env.GITHUB_SHA!;
const root = join(base, 'installed');
const service = 'com.panels.first-install-proof';
const home = homedir();
const server_build = join(base, 'build/server');
const machine_build = join(base, 'build/machine');
const archive = join(server_build, 'panels-app.tar.gz');
const machine_archive = join(
  machine_build,
  system === 'linux' ? 'panels-linux-executor.tar.gz' : 'panels-mac-runtime.tar.gz',
);
const machine_receipt = join(
  machine_build,
  system === 'linux' ? 'linux-executor-publication.json' : 'mac-runtime-publication.json',
);
const receipt_path = join(base, 'server-receipt.json');
const build = JSON.parse(
  await readFile(
    join(server_build, system === 'linux' ? 'linux-publication-build.json' : 'mac-home-build.json'),
    'utf8',
  ),
);
// Local archive mode consumes the exact candidate bytes, not an eligible production publication.
await writeFile(
  receipt_path,
  JSON.stringify(
    system === 'linux'
      ? {
          manifest: build.manifest,
          archive_sha256: build.archive.sha256,
          archive_size: build.archive.size,
        }
      : build,
  ),
);
const environment: NodeJS.ProcessEnv = { ...process.env, HOME: home, PLAN_TEST_MODE: '0' };
for (const key of Object.keys(environment)) if (key.startsWith('PLAN_')) delete environment[key];
const owner = new ChildProcesses();
const commands = new OneShotChildren(owner);
const primitives =
  system === 'darwin'
    ? new DarwinWorkerProcesses({ children: commands })
    : new LinuxWorkerProcesses();
const execution: ReleaseExecution = {
  child_processes: owner,
  signal: AbortSignal.timeout(12 * 60_000),
  deadline: performance.now() / 1000 + 12 * 60,
};
const phases: Record<string, number> = {};
const report: Record<string, unknown> = {
  revision,
  system,
  architecture: process.arch,
  node: process.version,
  boundary:
    'Real sealed candidate archives, the Python bootstrap, native service managers, first machine local socket, and second machine credential enrollment on the same clean runner account.',
  exclusions:
    'No browser, provider login, remote HTTPS, physical sleep or macOS permission consent. The hosted Apple launcher uses an ad-hoc signature.',
  phases_seconds: phases,
};
const run = async (
  name: string,
  argv: string[],
  env = environment,
  scope = execution,
  record = true,
) => {
  const started = performance.now();
  const result = await run_release_command(
    argv,
    { timeout_seconds: 240, environment: env, allow_nonzero: true },
    scope,
  );
  phases[name] = (performance.now() - started) / 1000;
  if (record) await writeFile(join(evidence, name + '.log'), result.stdout + result.stderr);
  assert.equal(result.code, 0, `${name}: ${result.stderr || result.stdout}`);
  return result.stdout;
};
const port_server = createServer();
await new Promise<void>((resolve, reject) => {
  port_server.once('error', reject);
  port_server.listen(0, '127.0.0.1', resolve);
});
const address = port_server.address();
assert.ok(address && typeof address !== 'string');
const port = address.port;
await new Promise<void>((resolve, reject) =>
  port_server.close((error) => (error ? reject(error) : resolve())),
);
const socket = executor_link_socket_path(join(root, 'data/planner.db'));
const mac = mac_installation_paths(home);
const linux_join_root = join(home, '.local/share/panels/executor-typescript');
const joined_state = system === 'linux' ? join(linux_join_root, 'data') : mac.state;
const first_state = join(root, 'machine/data');
const home_jobs = [
  service,
  service + '.machine',
  service + '.backup',
  service + '.maintenance',
  service + '.stall-watchdog',
];
const linux_jobs = [
  service + '.machine.service',
  ...['backup', 'maintenance', 'stall-watchdog'].flatMap((suffix) => [
    service + '.' + suffix + '.timer',
    service + '.' + suffix + '.service',
  ]),
  service + '.service',
];
const joined_job = system === 'linux' ? 'panels-executor-typescript.service' : mac.job;
const domain = `gui/${process.getuid!()}`;
let install_attempted = false;
let join_attempted = false;
let failed: unknown;
async function absent(path: string) {
  try {
    await lstat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
    throw error;
  }
  throw new Error('The clean-runner destination already exists: ' + path);
}
const started_ms = Date.now();
try {
  await absent(root);
  await absent(join(home, '.local/bin/panels'));
  for (const path of system === 'linux'
    ? [linux_join_root]
    : [mac.install, mac.app, mac.state, mac.plist])
    await absent(path);
  install_attempted = true;
  await run('first-install', [
    'python3',
    join(source, 'scripts/panels_install.py'),
    'home',
    '--root',
    root,
    '--service',
    service,
    '--port',
    String(port),
    '--archive',
    archive,
    '--receipt',
    receipt_path,
    '--revision',
    revision,
    '--isolated-providers',
  ]);
  const panels = join(root, 'app/bin/panels');
  const health = JSON.parse(
    await run('first-machine-health', [panels, 'executor', 'health'], {
      ...environment,
      PLAN_EXECUTOR_ROOT: first_state,
    }),
  );
  assert.ok(
    accepted_lease(health, revision, started_ms),
    'The first machine needs an accepted lease from this installation',
  );
  report.first_machine = health;
  const cli_environment = {
    ...environment,
    PLAN_SERVER_URL: `http://127.0.0.1:${port}`,
    PLAN_DB_PATH: join(root, 'data/planner.db'),
    PLAN_CONFIG_PATH: join(root, 'data/config.yaml'),
  };
  // The printed output contains the one-time secret and is never written to evidence.
  const output = await run(
    'issue-join-command',
    [panels, 'machine', 'join-command', 'Second proof machine', '--home', socket],
    cli_environment,
    execution,
    false,
  );
  const command = output.split('\n').find((line) => line.startsWith('panels_join_script='));
  const credential = output.split('\n').find((line) => /^[A-Za-z0-9_-]{43}$/.test(line));
  assert.ok(command && credential, 'The actual join command must supply a private credential');
  const private_input = join(base, 'join-credential');
  await writeFile(private_input, credential + '\n', { flag: 'wx', mode: 0o600 });
  join_attempted = true;
  try {
    await run(
      'second-machine-join',
      [
        'bash',
        '-c',
        command +
          ' --archive ' +
          shell_quote(machine_archive) +
          ' --receipt ' +
          shell_quote(machine_receipt) +
          ' < "$FIRST_INSTALL_CREDENTIAL"',
      ],
      { ...environment, FIRST_INSTALL_CREDENTIAL: private_input },
    );
  } finally {
    await rm(private_input, { force: true });
  }
  const joined_panels = system === 'linux' ? panels : mac.console;
  const second = JSON.parse(
    await run('second-machine-health', [joined_panels, 'executor', 'health'], {
      ...environment,
      PLAN_EXECUTOR_ROOT: joined_state,
    }),
  );
  assert.ok(
    accepted_lease(second, revision, started_ms),
    'The second machine needs an accepted credentialed lease',
  );
  report.second_machine = second;
  const machines = JSON.parse(
    await run('server-machine-list', [panels, 'machine', 'list', '--json'], cli_environment),
  );
  report.server_machines = machines;
  const first_identity = JSON.parse(await readFile(join(first_state, 'identity.json'), 'utf8'));
  assert.ok(first_identity.machine_id);
  assert.ok(
    machines.machines.some(
      (item: Record<string, unknown>) => item.machine_id === first_identity.machine_id,
    ),
  );
  const named = machines.machines.filter(
    (item: Record<string, unknown>) => item.name === 'Second proof machine',
  );
  assert.equal(named.length, 1);
  assert.notEqual(named[0].machine_id, first_identity.machine_id);
  const pids: number[] = [];
  for (const job of system === 'linux'
    ? [service + '.service', service + '.machine.service', joined_job]
    : [service, service + '.machine', joined_job]) {
    const result =
      system === 'linux'
        ? await run('process-' + job, [
            'systemctl',
            '--user',
            'show',
            job,
            '--property=MainPID',
            '--value',
          ])
        : await run('process-' + job, ['/bin/launchctl', 'print', `${domain}/${job}`]);
    const pid = Number(system === 'linux' ? result.trim() : result.match(/\bpid = (\d+)/)?.[1]);
    assert.ok(Number.isSafeInteger(pid) && pid > 0, 'Every program needs a running native job');
    pids.push(pid);
  }
  assert.equal(
    new Set(pids).size,
    3,
    'The server and both machine programs must be distinct processes',
  );
  report.service_pids = pids;
  report.result = 'passed';
} catch (error) {
  failed = error;
  report.result = 'failed';
  report.error = String(error);
} finally {
  const cleanup_execution: ReleaseExecution = {
    child_processes: owner,
    signal: AbortSignal.timeout(4 * 60_000),
    deadline: performance.now() / 1000 + 240,
  };
  try {
    // Stop only the exact jobs whose installers this proof attempted. Absence is valid after rollback.
    const jobs =
      system === 'linux'
        ? [...(join_attempted ? [joined_job] : []), ...(install_attempted ? linux_jobs : [])]
        : [
            ...(join_attempted ? [joined_job] : []),
            ...(install_attempted
              ? [
                  service + '.machine',
                  ...home_jobs.filter((job) => job !== service + '.machine').reverse(),
                ]
              : []),
          ];
    const births = new Map<number, WorkerProcess>();
    try {
      const pids = new Set<number>();
      for (const job of jobs) {
        const result = await run_release_command(
          system === 'linux'
            ? ['systemctl', '--user', 'show', job, '--property=MainPID', '--value']
            : ['/bin/launchctl', 'print', `${domain}/${job}`],
          { environment, timeout_seconds: 10, allow_nonzero: true },
          cleanup_execution,
        );
        if (result.code === 0) {
          const pid = Number(
            system === 'linux' ? result.stdout.trim() : result.stdout.match(/\bpid = (\d+)/)?.[1],
          );
          if (Number.isSafeInteger(pid) && pid > 0) pids.add(pid);
        }
      }
      for (const value of [report.first_machine, report.second_machine]) {
        const runtime = value as { process_id?: number } | undefined;
        if (runtime?.process_id) pids.add(runtime.process_id);
      }
      const snapshot = await primitives.snapshot(performance.now() / 1000 + 10);
      for (const pid of pids) {
        const birth = snapshot.get(pid);
        assert.ok(birth, 'Cannot capture the native birth of an installed process: ' + pid);
        births.set(pid, birth);
      }
      // Include runtime children and any commands that were still in flight at the snapshot.
      let expanded = true;
      while (expanded) {
        expanded = false;
        for (const birth of snapshot.values()) {
          if (births.has(birth.parent) && !births.has(birth.pid)) {
            births.set(birth.pid, birth);
            expanded = true;
          }
        }
      }
      report.process_births = [...births.values()];
    } catch (error) {
      report.process_capture_error = String(error);
      failed = new AggregateError([failed, error].filter(Boolean), 'Proof process capture failed');
    }
    for (const job of jobs) {
      if (system === 'linux') {
        const stopped = await run_release_command(
          ['systemctl', '--user', 'disable', '--now', job],
          { environment, timeout_seconds: 45, allow_nonzero: true },
          cleanup_execution,
        );
        await writeFile(join(evidence, 'stop-' + job + '.log'), stopped.stdout + stopped.stderr);
        const status = await run_release_command(
          ['systemctl', '--user', 'is-active', job],
          { environment, timeout_seconds: 10, allow_nonzero: true },
          cleanup_execution,
        );
        assert.ok(
          ['inactive', 'failed', 'unknown'].includes(status.stdout.trim()),
          'An installed proof job remains active: ' + job,
        );
      } else {
        await run_release_command(
          ['/bin/launchctl', 'bootout', `${domain}/${job}`],
          { environment, timeout_seconds: 45, allow_nonzero: true },
          cleanup_execution,
        );
        let absent = false;
        for (let attempt = 0; attempt < 50; attempt++) {
          const status = await run_release_command(
            ['/bin/launchctl', 'print', `${domain}/${job}`],
            { environment, timeout_seconds: 10, allow_nonzero: true },
            cleanup_execution,
          );
          if (status.code !== 0 && status.stderr.includes('Could not find service')) {
            absent = true;
            break;
          }
          await delay(100);
        }
        assert.ok(absent, 'An installed proof job remains loaded: ' + job);
      }
    }
    const retirement_deadline = Math.min(
      cleanup_execution.deadline!,
      performance.now() / 1000 + 30,
    );
    let retired = false;
    while (performance.now() / 1000 < retirement_deadline) {
      retired = (
        await Promise.all(
          [...births.values()].map((birth) => primitives.is_absent(birth, retirement_deadline)),
        )
      ).every(Boolean);
      if (retired) break;
      await delay(100);
    }
    assert.ok(retired, 'An installed process or captured descendant survives service stop');
    await check_home_port(port);
    report.cleanup =
      'all attempted jobs stopped, captured native process births retired, server port free';
  } catch (error) {
    report.cleanup = String(error);
    failed = new AggregateError([failed, error].filter(Boolean), 'Proof cleanup failed');
  }
  for (const [name, logs] of [
    ['server', join(root, 'logs')],
    ['first-machine', join(first_state, 'logs')],
    ['second-machine', join(joined_state, 'logs')],
  ]) {
    try {
      await cp(logs!, join(evidence, name!), { recursive: true });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
        failed ??= error;
        report.log_copy_error = String(error);
      }
    }
  }
  if (failed) report.result = 'failed';
  commands.seal();
  owner.seal();
  try {
    await Promise.all([commands.settled(), owner.settled()]);
  } catch (error) {
    failed = new AggregateError([failed, error].filter(Boolean), 'Proof child settlement failed');
    report.result = 'failed';
    report.child_settlement_error = String(error);
  }
  await writeFile(join(evidence, 'proof.json'), JSON.stringify(report, null, 2) + '\n');
}
if (failed) throw failed;
console.log(
  JSON.stringify({ result: report.result, cleanup: report.cleanup, phases_seconds: phases }),
);
