/** Deliberate service environment, independent of the installer's runtime settings. */
import { dirname, join, resolve } from 'node:path';
import { find_release_executable } from '../environments/release/process.ts';
import { resolved_path } from '../environments/paths.ts';
import { BUILT_IN_MANIFESTS } from '../drivers/built_in_manifests.ts';
import { home_path } from '../drivers/core/paths.ts';
import { RELEASE_REPOSITORY } from '../environments/release/source.ts';
import { HOME_UPDATE_TOKEN_FILE } from './configuration.ts';
import type { HomeInstallRequest, HomeInstallHost } from './contracts.ts';

export async function home_service_environment(
  request: HomeInstallRequest,
  host: HomeInstallHost,
): Promise<Record<string, string>> {
  const root = request.root;
  const node_directory = join(root, 'app/node/bin');
  const directories =
    host.system === 'darwin'
      ? [node_directory, join(root, 'app/agent_backends/node_modules/.bin')]
      : [join(root, 'app/agent_backends/node_modules/.bin'), node_directory];
  if (host.system === 'darwin') {
    directories.push('/opt/homebrew/bin');
    for (const { manifest } of BUILT_IN_MANIFESTS) {
      if (!manifest.install?.darwin_path_executable) continue;
      const executable = await find_release_executable(manifest.executable.name, host.environment);
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
    PLAN_UPDATE_REPOSITORY: RELEASE_REPOSITORY,
    PLAN_UPDATE_TOKEN_FILE: join(root, HOME_UPDATE_TOKEN_FILE),
    ...(host.system === 'linux'
      ? { PLAN_SERVER_CONTROL_SOCKET: join(root, 'data/server-control.sock') }
      : { PLAN_WORKSPACE_ROOT: join(root, 'workspaces') }),
  };
  return environment;
}

/** Provider homes and machine authority never enter the server service environment. */
export async function machine_service_environment(
  request: HomeInstallRequest,
  host: HomeInstallHost,
): Promise<Record<string, string>> {
  const server = await home_service_environment(request, host);
  const root = request.root;
  const environment: Record<string, string> = {
    HOME: host.account_home,
    PATH: server.PATH!.replaceAll(join(root, 'app'), join(root, 'machine/runtime')),
    ...(host.system === 'linux' ? { LANG: 'C.UTF-8' } : {}),
    PLAN_EXECUTOR_ROOT: join(root, 'machine/data'),
  };
  for (const { manifest } of BUILT_IN_MANIFESTS) {
    const home = manifest.home;
    if (!home) continue;
    // A set variable makes macOS Claude read a suffixed Keychain item, which hides the login the
    // owner already saved. Leave it unset unless the owner set it or the providers are isolated.
    if (
      host.system === 'darwin' &&
      home.leave_unset_on_macos &&
      !request.isolated_providers &&
      !host.environment[home.environment_name]
    )
      continue;
    const configured = request.isolated_providers
      ? join(root, 'machine/providers', home.isolated_directory_name)
      : home_path(home, host.environment, host.account_home, {
          unset: '||',
          expand_configured: false,
        });
    environment[home.environment_name] = await resolved_path(configured, host.account_home);
  }
  // An executable override is kept only on macOS.
  if (host.system === 'darwin')
    for (const { manifest } of BUILT_IN_MANIFESTS) {
      const variable = manifest.executable.override_environment_name;
      if (variable && host.environment[variable])
        environment[variable] = host.environment[variable]!;
    }
  return environment;
}
