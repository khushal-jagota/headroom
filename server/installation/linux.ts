/** User systemd units for one new home, with loaded-fragment checks before cleanup. */
import { lstat, readFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { resolved_path } from '../environments/paths.ts';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import { assert_release_execution } from '../environments/release/process.ts';
import { first_machine_arguments } from './machine.ts';
import { home_service_environment, machine_service_environment } from './environment.ts';
import {
  HOME_SCHEDULE_SUFFIXES,
  HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
  HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS,
} from './configuration.ts';
import type {
  HomeInstallRequest,
  HomeInstallHost,
  HomeServicePlan,
  HomeServiceRemoval,
} from './contracts.ts';

function unit_argument(value: string): string {
  if (/[\x00-\x1f\x7f]/.test(value))
    throw new Error('Unit values cannot contain control characters');
  return JSON.stringify(value.replaceAll('%', '%%'));
}
/** A null machine environment leaves out the machine code unit: the machine already runs it. */
export function linux_home_units(
  request: HomeInstallRequest,
  environment: Readonly<Record<string, string>>,
  machine_environment: Readonly<Record<string, string>> | null,
  host: HomeInstallHost,
): Record<string, string> {
  const root = request.root,
    panels = join(root, 'app/bin/panels');
  unit_argument(root);
  const shared =
    `WorkingDirectory=${root.replaceAll('%', '%%')}\n` +
    Object.entries(environment)
      .map(([key, value]) => `Environment=${unit_argument(key + '=' + value)}\n`)
      .join('');
  const oneshot = (description: string, command: readonly string[], extra = '') =>
    `[Unit]\nDescription=${description}\n[Service]\nType=oneshot\n` +
    shared +
    'ExecStart=' +
    command.map(unit_argument).join(' ') +
    '\nKillMode=control-group\n' +
    extra;
  const timer = (description: string, service: string, schedule: string) =>
    `[Unit]\nDescription=${description}\n[Timer]\n` +
    schedule +
    `Unit=${service}\n` +
    '[Install]\nWantedBy=timers.target\n';
  const scheduled: Record<
    (typeof HOME_SCHEDULE_SUFFIXES)[number],
    { service: string; timer: string; schedule: string }
  > = {
    '.maintenance': {
      service: oneshot('Panels home maintenance', [panels, 'environment', 'cleanup', '--apply']),
      timer: 'Daily Panels home maintenance',
      schedule: 'OnCalendar=*-*-* 03:15:00\nPersistent=true\n',
    },
    '.stall-watchdog': {
      service: oneshot(
        'Panels home stall watchdog',
        [
          panels,
          'stalls',
          'watchdog',
          '--db',
          join(root, 'data/planner.db'),
          '--state-dir',
          join(root, 'data/stall-watchdog'),
        ],
        'TimeoutStartSec=300\n',
      ),
      timer: 'Run the Panels home stall watchdog every 30 seconds',
      schedule: 'OnBootSec=60s\nOnUnitActiveSec=30s\nAccuracySec=5s\n',
    },
  };
  const units: Record<string, string> = {
    [request.service + '.service']:
      '[Unit]\nDescription=Panels home\nAfter=network-online.target\n' +
      '[Service]\nType=simple\n' +
      shared +
      `ExecStart=${unit_argument(join(root, 'app/bin/panels-launcher'))} serve\n` +
      'Restart=always\nRestartSec=5\nKillMode=control-group\nTimeoutStopSec=30\nDelegate=yes\n' +
      '[Install]\nWantedBy=default.target\n',
    ...(machine_environment && {
      [request.service + '.machine.service']:
        '[Unit]\nDescription=Panels machine code\n[Service]\nType=simple\n' +
        `WorkingDirectory=${root.replaceAll('%', '%%')}/machine\n` +
        Object.entries(machine_environment)
          .map(([key, value]) => `Environment=${unit_argument(key + '=' + value)}\n`)
          .join('') +
        'ExecStart=' +
        first_machine_arguments(request, host)
          .map((value) => unit_argument(value.replaceAll('$', '$$')))
          .join(' ') +
        '\n' +
        'Restart=always\nRestartSec=5\nKillMode=control-group\nTimeoutStopSec=90\nDelegate=yes\n' +
        'UnsetEnvironment=NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH\n' +
        '[Install]\nWantedBy=default.target\n',
    }),
    [request.service + '.backup.service']: oneshot('Panels home backup', [
      panels,
      'environment',
      'backup-current',
      '--source-db',
      join(root, 'data/planner.db'),
      '--backup-dir',
      join(root, 'data/backups'),
      '--current-app',
      join(root, 'app'),
    ]),
    [request.service + '.backup.timer']: timer(
      'Daily Panels home backup',
      request.service + '.backup.service',
      'OnCalendar=*-*-* 02:30:00\nPersistent=true\n',
    ),
  };
  for (const suffix of HOME_SCHEDULE_SUFFIXES) {
    const name = request.service + suffix;
    units[name + '.service'] = scheduled[suffix].service;
    units[name + '.timer'] = timer(
      scheduled[suffix].timer,
      name + '.service',
      scheduled[suffix].schedule,
    );
  }
  return units;
}
async function require_absent(path: string): Promise<void> {
  try {
    await lstat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
    throw error;
  }
  throw new Error(`The service file already exists: ${path}`);
}
/** `machine: false` plans the home without machine code, for a machine that already runs it. */
export async function create_linux_home_service(
  request: HomeInstallRequest,
  host: HomeInstallHost,
  machine = true,
): Promise<HomeServicePlan> {
  const units = linux_home_units(
    request,
    await home_service_environment(request, host),
    machine ? await machine_service_environment(request, host) : null,
    host,
  );
  const files = Object.entries(units).map(([name, content]) => ({
    path: join(host.account_home, '.config/systemd/user', name),
    content,
    mode: 0o600,
  }));
  const names = Object.keys(units);
  const attempted: string[] = [];
  const command = (
    args: readonly string[],
    execution: ReleaseExecution,
    observation = false,
    allow_nonzero = false,
  ) =>
    host.command(
      ['systemctl', '--user', ...args],
      execution,
      observation ? HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS : HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
      allow_nonzero,
    );
  const bound_fragment = async (
    name: string,
    execution: ReleaseExecution,
    assert_owned: (path: string) => Promise<void>,
  ) => {
    const path = files[names.indexOf(name)]!.path;
    await assert_owned(path);
    const result = await command(
      ['show', name, '--property=FragmentPath', '--value'],
      execution,
      true,
      true,
    );
    if (
      result.code !== 0 ||
      !result.stdout.trim() ||
      (await resolved_path(result.stdout.trim())) !== (await resolved_path(path))
    )
      throw new Error(`The service no longer belongs to this installation: ${name}`);
  };
  return {
    files,
    links: [],
    async preflight(execution) {
      const linger = await host.command(
        ['loginctl', 'show-user', String(host.uid), '--property=Linger', '--value'],
        execution,
        HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS,
        true,
      );
      if (linger.code !== 0 || linger.stdout.trim() !== 'yes')
        throw new Error('Enable user lingering before installation');
      await command(['show-environment'], execution);
      for (const file of files) {
        await require_absent(file.path);
        const name = names[files.indexOf(file)]!;
        const result = await command(
          ['show', name, '--property=LoadState', '--value'],
          execution,
          true,
          true,
        );
        if (result.stdout.trim() !== 'not-found')
          throw new Error(`The service exists or cannot be inspected: ${name}`);
      }
    },
    async start_home(execution) {
      await command(['daemon-reload'], execution);
      const name = request.service + '.service';
      attempted.push(name);
      await command(['enable', '--now', name], execution);
    },
    async start_machine(execution) {
      const name = request.service + '.machine.service';
      if (!names.includes(name)) throw new Error('This home plan has no machine code service');
      attempted.push(name);
      await command(['enable', '--now', name], execution);
    },
    async start_auxiliaries(execution) {
      for (const suffix of ['.backup', ...HOME_SCHEDULE_SUFFIXES]) {
        const name = request.service + suffix + '.timer';
        attempted.push(name);
        await command(['enable', '--now', name], execution);
      }
    },
    async finish_cleanup(execution) {
      await command(['daemon-reload'], execution);
    },
    async stop(execution, assert_owned) {
      if (!attempted.length) return;
      const machine_name = request.service + '.machine.service';
      for (const name of [
        ...names.filter((name) => name === machine_name),
        ...[...names].reverse().filter((name) => name !== machine_name),
      ]) {
        await bound_fragment(name, execution, assert_owned);
        await command(['stop', name], execution);
      }
      for (const name of [...attempted].reverse()) {
        await bound_fragment(name, execution, assert_owned);
        await command(['disable', name], execution);
      }
      for (const name of names) {
        const result = await command(['is-active', name], execution, true, true);
        if (!['inactive', 'failed'].includes(result.stdout.trim()))
          throw new Error(`Cannot confirm that the new service stopped: ${name}`);
      }
    },
  };
}
/**
 * Stop and remove the home units of a moved home that did not take over. A service unit belongs
 * to `root` only when it works in `root`; a timer belongs only with its service. It never names
 * a machine code unit.
 */
export const remove_linux_home_service: HomeServiceRemoval = async (
  root,
  service,
  host,
  execution,
) => {
  const directory = join(host.account_home, '.config/systemd/user');
  const working = `WorkingDirectory=${root.replaceAll('%', '%%')}`;
  const suffixes = ['.backup', ...HOME_SCHEDULE_SUFFIXES];
  // Timers first, so a service file never leaves before the timer that starts it.
  const names = [
    ...suffixes.map((suffix) => service + suffix + '.timer'),
    ...suffixes.map((suffix) => service + suffix + '.service'),
    service + '.service',
  ];
  const command = (args: readonly string[], observation = false, allow_nonzero = false) =>
    host.command(
      ['systemctl', '--user', ...args],
      execution,
      observation ? HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS : HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
      allow_nonzero,
    );
  const read = async (name: string): Promise<string | null> => {
    try {
      const info = await lstat(join(directory, name));
      if (!info.isFile()) throw new Error(`The service file is not a regular file: ${name}`);
      return await readFile(join(directory, name), 'utf8');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
      throw error;
    }
  };
  const contents = new Map<string, string | null>();
  for (const name of names) contents.set(name, await read(name));
  const owned = (name: string): boolean => {
    const content = contents.get(name);
    if (content == null) return false;
    if (name.endsWith('.service')) return content.split('\n').includes(working);
    return (
      content.split('\n').includes(`Unit=${name.replace(/\.timer$/, '.service')}`) &&
      owned(name.replace(/\.timer$/, '.service'))
    );
  };
  const loaded: string[] = [];
  for (const name of names) {
    if (contents.get(name) != null && !owned(name))
      throw new Error(`The service belongs to another installation: ${name}`);
    const result = await command(['show', name, '--property=FragmentPath', '--value'], true, true);
    const fragment = result.code === 0 ? result.stdout.trim() : '';
    if (!fragment) continue;
    if ((await resolved_path(fragment)) !== (await resolved_path(join(directory, name))))
      throw new Error(`The service belongs to another installation: ${name}`);
    loaded.push(name);
  }
  for (const name of loaded) {
    assert_release_execution(execution);
    await command(['stop', name]);
    if (contents.get(name) != null) await command(['disable', name]);
  }
  for (const name of names) {
    const result = await command(['is-active', name], true, true);
    if (!['inactive', 'failed'].includes(result.stdout.trim()))
      throw new Error(`Cannot confirm that the moved home service stopped: ${name}`);
  }
  for (const name of names)
    if (contents.get(name) != null) {
      assert_release_execution(execution);
      await unlink(join(directory, name));
    }
  if (loaded.length || [...contents.values()].some((content) => content != null))
    await command(['daemon-reload']);
};
