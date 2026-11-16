/** Deliberate service environment, independent of the installer's runtime settings. */
import { dirname, join, resolve } from 'node:path';
import { find_release_executable } from '../environments/release/process.ts';
import { resolved_path } from '../environments/paths.ts';
import type { HomeInstallRequest, HomeInstallHost } from './contracts.ts';

export async function home_service_environment(
  request: HomeInstallRequest,
  host: HomeInstallHost,
): Promise<Record<string, string>> {
  const root = request.root;
  const node_directory = dirname(host.node_executable);
  const directories =
    host.system === 'darwin'
      ? [node_directory, join(root, 'app/agent_backends/node_modules/.bin')]
      : [join(root, 'app/agent_backends/node_modules/.bin'), node_directory];
  if (host.system === 'darwin') {
    directories.push('/opt/homebrew/bin');
    for (const name of ['codex', 'claude']) {
      const executable = await find_release_executable(name, host.environment);
      if (executable) directories.push(dirname(resolve(executable)));
    }
  }
  directories.push(join(host.account_home, '.local/bin'), '/usr/local/bin', '/usr/bin', '/bin');
  if (host.system === 'darwin') directories.push('/usr/sbin', '/sbin');
  const environment: Record<string, string> = {
    HOME: host.account_home,
    PATH: [...new Set(directories)].join(':'),
    ...(host.system === 'linux' ? { LANG: 'C.UTF-8' } : {}),
    PLAN_CONFIG_PATH: join(root, 'data/config.yaml'),
    PLAN_DB_PATH: join(root, 'data/planner.db'),
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_PORT: String(request.port),
    PLAN_BACKUP_DIR: join(root, 'data/backups'),
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'data/dispatcher.lock'),
    ...(host.system === 'linux'
      ? { PLAN_SERVER_CONTROL_SOCKET: join(root, 'data/server-control.sock') }
      : { PLAN_WORKSPACE_ROOT: join(root, 'workspaces') }),
  };
  for (const [variable, folder] of [
    ['CODEX_HOME', '.codex'],
    ['CLAUDE_CONFIG_DIR', '.claude'],
    ['PLAN_HERMES_HOME', '.hermes'],
  ] as const) {
    const configured = request.isolated_providers
      ? join(root, 'providers', folder.slice(1))
      : host.environment[variable] || join(host.account_home, folder);
    environment[variable] = await resolved_path(configured, host.account_home);
  }
  const retained = ['PLAN_UPDATE_REPOSITORY', 'PLAN_UPDATE_TOKEN_FILE', 'PLAN_HERMES_PYTHON'];
  if (host.system === 'darwin') retained.push('PLAN_CLAUDE_EXECUTABLE');
  for (const variable of retained)
    if (host.environment[variable]) environment[variable] = host.environment[variable]!;
  return environment;
}
