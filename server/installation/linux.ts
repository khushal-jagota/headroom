/** User systemd units for one new home, with loaded-fragment checks before cleanup. */
import { lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { resolved_path } from '../environments/paths.ts';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import { home_service_environment } from './environment.ts';
import {
  HOME_SCHEDULE_SUFFIXES,
  HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
  HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS,
} from './configuration.ts';
import type { HomeInstallRequest, HomeInstallHost, HomeServicePlan } from './contracts.ts';

function unit_argument(value: string): string {
  if (/[\x00-\x1f\x7f]/.test(value))
    throw new Error('Unit values cannot contain control characters');
  return JSON.stringify(value.replaceAll('%', '%%'));
}
export function linux_home_units(
  request: HomeInstallRequest,
  environment: Readonly<Record<string, string>>,
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
export async function create_linux_home_service(
  request: HomeInstallRequest,
  host: HomeInstallHost,
): Promise<HomeServicePlan> {
  const units = linux_home_units(request, await home_service_environment(request, host));
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
      for (const name of [...names].reverse()) {
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
