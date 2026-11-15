/** LaunchAgent descriptors and exact-label control for one new graphical home. */
import { lstat } from 'node:fs/promises';
import { join } from 'node:path';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import { home_service_environment } from './environment.ts';
import { HOME_SERVICE_COMMAND_TIMEOUT_SECONDS, HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS,
  MAC_HOME_SCHEDULE_SUFFIXES } from './configuration.ts';
import type { HomeInstallRequest, HomeInstallHost, HomeServicePlan, HomeInstallationFile } from './contracts.ts';

export function darwin_home_jobs(
  request: HomeInstallRequest,
  environment: Readonly<Record<string, string>>,
): Record<string, Record<string, unknown>> {
  const root = request.root, label = request.service;
  const command = [join(root, 'app/bin/panels')];
  const common = { WorkingDirectory: root, EnvironmentVariables: environment,
    LimitLoadToSessionType: 'Aqua', AbandonProcessGroup: false };
  const log_paths = (name: string) => ({
    StandardOutPath: join(root, 'logs', name + '.stdout.log'),
    StandardErrorPath: join(root, 'logs', name + '.stderr.log'),
  });
  return {
    [label]: { ...common, Label: label, ProgramArguments: [...command, 'serve'],
      RunAtLoad: true, KeepAlive: true, ThrottleInterval: 5, ...log_paths('home') },
    [label + '.backup']: { ...common, Label: label + '.backup',
      ProgramArguments: [...command, 'environment', 'backup-current', '--source-db',
        join(root, 'data/planner.db'), '--backup-dir', join(root, 'data/backups'),
        '--current-app', join(root, 'app')], StartCalendarInterval: { Hour: 2, Minute: 30 },
      ...log_paths('backup') },
    [label + '.maintenance']: { ...common, Label: label + '.maintenance',
      ProgramArguments: [...command, 'environment', 'cleanup', '--apply'],
      StartCalendarInterval: { Hour: 3, Minute: 15 }, ...log_paths('maintenance') },
    [label + '.stall-watchdog']: { ...common, Label: label + '.stall-watchdog',
      ProgramArguments: [...command, 'stalls', 'watchdog', '--db', join(root, 'data/planner.db'),
        '--state-dir', join(root, 'data/stall-watchdog')], StartInterval: 30,
      ...log_paths('stall-watchdog') },
  };
}
export async function create_darwin_home_service(
  request: HomeInstallRequest,
  host: HomeInstallHost,
): Promise<HomeServicePlan> {
  if (!/^[A-Za-z0-9][A-Za-z0-9.-]*$/.test(request.service)) throw new Error('Use a valid launchd label');
  const jobs = darwin_home_jobs(request, await home_service_environment(request, host));
  const labels = [request.service, request.service + '.backup',
    ...MAC_HOME_SCHEDULE_SUFFIXES.map((suffix) => request.service + suffix)];
  const files: HomeInstallationFile[] = labels.map((label) => ({ path: join(request.root, 'agents', label + '.plist'),
    content: JSON.stringify(jobs[label]) + '\n', mode: 0o600, format: 'plist-json' }));
  files.push({ path: join(request.root, 'data/mac-home-installation.json'), content: JSON.stringify({
    uid: host.uid, root: request.root, label: request.service,
    home_plist: files[0]!.path, backup_plist: files[1]!.path,
    installer_python: request.installer_python,
  }) + '\n', mode: 0o600 });
  const links = labels.map((label, index) => ({
    path: join(host.account_home, 'Library/LaunchAgents', label + '.plist'), target: files[index]!.path,
  }));
  const domain = `gui/${host.uid}`;
  const attempted: number[] = [];
  const command = (args: readonly string[], execution: ReleaseExecution, observation = false, allow_nonzero = false) =>
    host.command(['/bin/launchctl', ...args], execution,
      observation ? HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS : HOME_SERVICE_COMMAND_TIMEOUT_SECONDS, allow_nonzero);
  const missing = (result: { code: number; stderr: string }) =>
    result.code !== 0 && result.stderr.includes('Could not find service');
  const start = async (index: number, execution: ReleaseExecution) => {
    attempted.push(index);
    await command(['bootstrap', domain, files[index]!.path], execution);
  };
  return {
    files, links,
    async preflight(execution) {
      for (let index = 0; index < labels.length; index++) {
        try { await lstat(links[index]!.path); throw new Error(`The LaunchAgent file already exists: ${labels[index]}`); }
        catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
        const result = await command(['print', domain + '/' + labels[index]], execution, true, true);
        if (!missing(result)) throw new Error(`The LaunchAgent exists or cannot be inspected: ${labels[index]}`);
      }
    },
    start_home: (execution) => start(0, execution),
    async start_auxiliaries(execution) { for (const index of [1, 2, 3]) await start(index, execution); },
    async stop(execution, assert_owned) {
      for (const index of [...attempted].reverse()) {
        const label = labels[index]!, path = files[index]!.path;
        const result = await command(['print', domain + '/' + label], execution, true, true);
        if (missing(result)) continue;
        await assert_owned(path);
        if (result.code !== 0 || !result.stdout.split('\n').some((line) => line.trim() === `path = ${path}`))
          throw new Error(`The LaunchAgent no longer belongs to this installation: ${label}`);
        await command(['bootout', domain + '/' + label], execution);
        if (!missing(await command(['print', domain + '/' + label], execution, true, true)))
          throw new Error(`Cannot confirm that the new LaunchAgent stopped: ${label}`);
      }
    },
  };
}
