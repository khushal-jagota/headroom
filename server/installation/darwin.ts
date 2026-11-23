/** LaunchAgent descriptors and exact-label control for one new graphical home. */
import { lstat, readlink, unlink } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { setTimeout as pause } from 'node:timers/promises';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import { first_machine_arguments } from './machine.ts';
import { home_service_environment, machine_service_environment } from './environment.ts';
import { external_home_node } from '../environments/updates/darwin/identity.ts';
import { darwin_schedule_jobs, read_home_job } from '../environments/updates/darwin/jobs.ts';
import { create_darwin_home_io } from '../environments/updates/darwin/service.ts';
import {
  authenticate_loaded_job,
  launchd_field,
} from '../environments/updates/darwin/logic/launchd.ts';
import {
  DARWIN_HOME_COMMAND_SECONDS,
  DARWIN_HOME_POLL_MILLISECONDS,
} from '../environments/updates/darwin/configuration.ts';
import { assert_release_execution } from '../environments/release/process.ts';
import {
  HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
  HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS,
  HOME_SCHEDULE_SUFFIXES,
} from './configuration.ts';
import type {
  HomeInstallRequest,
  HomeInstallHost,
  HomeServicePlan,
  HomeInstallationFile,
  HomeServiceRemoval,
} from './contracts.ts';

/** A null machine environment leaves out the machine code job: the machine already runs it. */
export function darwin_home_jobs(
  request: HomeInstallRequest,
  environment: Readonly<Record<string, string>>,
  machine_environment: Readonly<Record<string, string>> | null,
  host: HomeInstallHost,
): Record<string, Record<string, unknown>> {
  const root = request.root,
    label = request.service;
  const command = [join(root, 'app/bin/panels')];
  const common = {
    WorkingDirectory: root,
    EnvironmentVariables: environment,
    LimitLoadToSessionType: 'Aqua',
    AbandonProcessGroup: false,
  };
  const log_paths = (name: string) => ({
    StandardOutPath: join(root, 'logs', name + '.stdout.log'),
    StandardErrorPath: join(root, 'logs', name + '.stderr.log'),
  });
  return {
    [label]: {
      ...common,
      Label: label,
      ProgramArguments: [...command, 'serve'],
      RunAtLoad: true,
      KeepAlive: true,
      ThrottleInterval: 5,
      ...log_paths('home'),
    },
    ...(machine_environment && {
      [label + '.machine']: {
        ...common,
        Label: label + '.machine',
        WorkingDirectory: join(root, 'machine'),
        EnvironmentVariables: machine_environment,
        ProgramArguments: first_machine_arguments(request, host),
        RunAtLoad: true,
        KeepAlive: true,
        ThrottleInterval: 5,
        ...log_paths('machine'),
      },
    }),
    [label + '.backup']: {
      ...common,
      Label: label + '.backup',
      ProgramArguments: [
        ...command,
        'environment',
        'backup-current',
        '--source-db',
        join(root, 'data/planner.db'),
        '--backup-dir',
        join(root, 'data/backups'),
        '--current-app',
        join(root, 'app'),
      ],
      StartCalendarInterval: { Hour: 2, Minute: 30 },
      ...log_paths('backup'),
    },
    ...darwin_schedule_jobs(root, label, command, environment),
  };
}
/** The home's own LaunchAgents, without the machine code job. */
export function darwin_home_labels(service: string): string[] {
  return [
    service,
    service + '.backup',
    ...HOME_SCHEDULE_SUFFIXES.map((suffix) => service + suffix),
  ];
}
/** `machine: false` plans the home without machine code, for a machine that already runs it. */
export async function create_darwin_home_service(
  request: HomeInstallRequest,
  host: HomeInstallHost,
  machine = true,
): Promise<HomeServicePlan> {
  if (!/^[A-Za-z0-9][A-Za-z0-9.-]*$/.test(request.service))
    throw new Error('Use a valid launchd label');
  const jobs = darwin_home_jobs(
    request,
    await home_service_environment(request, host),
    machine ? await machine_service_environment(request, host) : null,
    host,
  );
  const labels = [
    ...darwin_home_labels(request.service),
    ...(machine ? [request.service + '.machine'] : []),
  ];
  const machine_index = machine ? labels.length - 1 : -1;
  const files: HomeInstallationFile[] = labels.map((label) => ({
    path: join(request.root, 'agents', label + '.plist'),
    content: JSON.stringify(jobs[label]) + '\n',
    mode: 0o600,
    format: 'plist-json',
  }));
  files.push({
    path: join(request.root, 'data/mac-home-installation.json'),
    content:
      JSON.stringify({
        version: 2,
        uid: host.uid,
        root: request.root,
        label: request.service,
        home_plist: files[0]!.path,
        backup_plist: files[1]!.path,
        installer_node: external_home_node(request.root, host.node_executable),
      }) + '\n',
    mode: 0o600,
  });
  const links = labels.map((label, index) => ({
    path: join(host.account_home, 'Library/LaunchAgents', label + '.plist'),
    target: files[index]!.path,
  }));
  const domain = `gui/${host.uid}`;
  const attempted: number[] = [];
  const command = (
    args: readonly string[],
    execution: ReleaseExecution,
    observation = false,
    allow_nonzero = false,
  ) =>
    host.command(
      ['/bin/launchctl', ...args],
      execution,
      Math.min(
        observation
          ? HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS
          : HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
        execution.deadline === null ? Infinity : execution.deadline - performance.now() / 1000,
      ),
      allow_nonzero,
    );
  const missing = (result: { code: number; stderr: string }) =>
    result.code !== 0 && result.stderr.includes('Could not find service');
  const start = async (index: number, execution: ReleaseExecution) => {
    attempted.push(index);
    await command(['bootstrap', domain, files[index]!.path], execution);
  };
  return {
    files,
    links,
    async preflight(execution) {
      for (let index = 0; index < labels.length; index++) {
        try {
          await lstat(links[index]!.path);
          throw new Error(`The LaunchAgent file already exists: ${labels[index]}`);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
        }
        const result = await command(
          ['print', domain + '/' + labels[index]],
          execution,
          true,
          true,
        );
        if (!missing(result))
          throw new Error(`The LaunchAgent exists or cannot be inspected: ${labels[index]}`);
      }
    },
    start_home: (execution) => start(0, execution),
    async start_machine(execution) {
      if (machine_index < 0) throw new Error('This home plan has no machine code service');
      await start(machine_index, execution);
    },
    async start_auxiliaries(execution) {
      for (const index of [1, 2, 3]) await start(index, execution);
    },
    async stop(execution, assert_owned) {
      const scope = {
        ...execution,
        deadline: Math.min(
          execution.deadline ?? Infinity,
          performance.now() / 1000 + DARWIN_HOME_COMMAND_SECONDS,
        ),
      };
      const io = create_darwin_home_io(scope, host.environment, {
        command: (argv, budget, seconds, allow_nonzero) =>
          host.command(
            argv,
            budget,
            Math.min(seconds, scope.deadline - performance.now() / 1000),
            allow_nonzero,
          ),
        uid: host.uid,
        account_home: host.account_home,
      });
      for (const index of [...attempted].sort((a, b) =>
        a === machine_index ? -1 : b === machine_index ? 1 : b - a,
      )) {
        assert_release_execution(scope);
        const label = labels[index]!,
          path = files[index]!.path;
        await assert_owned(path);
        const plist = await read_home_job(path, io, scope);
        if (!isDeepStrictEqual(plist, jobs[label]))
          throw new Error(`The LaunchAgent definition changed after creation: ${label}`);
        await assert_owned(path);
        const result = await command(['print', domain + '/' + label], scope, true, true);
        if (missing(result)) continue;
        await assert_owned(path);
        if (result.code !== 0)
          throw new Error(`The LaunchAgent no longer belongs to this installation: ${label}`);
        authenticate_loaded_job(
          result.stdout,
          path,
          plist,
          Object.keys(plist.EnvironmentVariables),
          realpathSync,
        );
        await command(['bootout', domain + '/' + label], scope);
        for (;;) {
          assert_release_execution(scope);
          await assert_owned(path);
          const after = await command(['print', domain + '/' + label], scope, true, true);
          assert_release_execution(scope);
          if (missing(after)) break;
          if (after.code !== 0)
            throw new Error(`Cannot confirm that the new LaunchAgent stopped: ${label}`);
          await assert_owned(path);
          authenticate_loaded_job(
            after.stdout,
            path,
            plist,
            Object.keys(plist.EnvironmentVariables),
            realpathSync,
          );
          await pause(
            Math.min(
              DARWIN_HOME_POLL_MILLISECONDS,
              Math.max(0, (scope.deadline - performance.now() / 1000) * 1000),
            ),
            undefined,
            { signal: scope.signal },
          );
        }
      }
    },
  };
}
/**
 * Boot out and remove the LaunchAgents of a moved home that did not take over. A job belongs to
 * `root` only when its descriptor is the root's own plist and it works in `root`. It never
 * names the machine code job.
 */
export const remove_darwin_home_service: HomeServiceRemoval = async (
  root,
  service,
  host,
  execution,
) => {
  if (!/^[A-Za-z0-9][A-Za-z0-9.-]*$/.test(service)) throw new Error('Use a valid launchd label');
  const domain = `gui/${host.uid}`;
  const scope = {
    ...execution,
    deadline: Math.min(
      execution.deadline ?? Infinity,
      performance.now() / 1000 + DARWIN_HOME_COMMAND_SECONDS,
    ),
  };
  const command = (args: readonly string[], observation = false, allow_nonzero = false) =>
    host.command(
      ['/bin/launchctl', ...args],
      scope,
      Math.min(
        observation
          ? HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS
          : HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
        scope.deadline - performance.now() / 1000,
      ),
      allow_nonzero,
    );
  const missing = (result: { code: number; stderr: string }) =>
    result.code !== 0 && result.stderr.includes('Could not find service');
  const jobs = darwin_home_labels(service).map((label) => ({
    label,
    plist: join(root, 'agents', label + '.plist'),
    link: join(host.account_home, 'Library/LaunchAgents', label + '.plist'),
  }));
  const belongs = (text: string, job: (typeof jobs)[number]) =>
    [job.plist, job.link].includes(launchd_field(text, 'path') ?? '') &&
    launchd_field(text, 'working directory') === root;
  const links: string[] = [],
    loaded: (typeof jobs)[number][] = [];
  for (const job of jobs) {
    try {
      const info = await lstat(job.link);
      if (!info.isSymbolicLink() || (await readlink(job.link)) !== job.plist)
        throw new Error(`The LaunchAgent belongs to another installation: ${job.label}`);
      links.push(job.link);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    const result = await command(['print', domain + '/' + job.label], true, true);
    if (missing(result)) continue;
    if (result.code !== 0 || !belongs(result.stdout, job))
      throw new Error(`The LaunchAgent belongs to another installation: ${job.label}`);
    loaded.push(job);
  }
  for (const job of loaded.reverse()) {
    assert_release_execution(scope);
    await command(['bootout', domain + '/' + job.label]);
    for (;;) {
      assert_release_execution(scope);
      const after = await command(['print', domain + '/' + job.label], true, true);
      if (missing(after)) break;
      if (after.code !== 0 || !belongs(after.stdout, job))
        throw new Error(`Cannot confirm that the moved home LaunchAgent stopped: ${job.label}`);
      await pause(
        Math.min(
          DARWIN_HOME_POLL_MILLISECONDS,
          Math.max(0, (scope.deadline - performance.now() / 1000) * 1000),
        ),
        undefined,
        { signal: scope.signal },
      );
    }
  }
  for (const link of links) {
    assert_release_execution(scope);
    await unlink(link);
  }
};
