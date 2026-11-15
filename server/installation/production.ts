/** Native effects share the install invocation's canonical child owner. */
import { homedir } from 'node:os';
import { realpath } from 'node:fs/promises';
import { basename } from 'node:path';
import { extract_app_archive } from '../environments/release/archive.ts';
import { admit_runtime } from '../environments/release/admission.ts';
import { run_release_command, assert_release_execution } from '../environments/release/process.ts';
import { MAC_HOME, LINUX_HOME } from '../environments/release/contracts.ts';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import { require_host_node } from '../environments/release/logic/host.ts';
import { healthy } from '../environments/updates/installer/health.ts';
import { installer_test_mode } from '../environments/updates/installer/configuration.ts';
import { HOME_HEALTH_TIMEOUT_SECONDS } from './configuration.ts';
import { create_linux_home_service } from './linux.ts';
import { create_darwin_home_service } from './darwin.ts';
import type { HomeInstallDependencies, HomeCommand } from './contracts.ts';

export async function production_home_dependencies(
  execution: ReleaseExecution,
  environment: Readonly<NodeJS.ProcessEnv> = process.env,
): Promise<HomeInstallDependencies> {
  const system = process.platform,
    architecture = process.arch === 'x64' ? 'x86_64' : process.arch;
  if (!(
    (system === 'linux' && architecture === 'x86_64') ||
    (system === 'darwin' && architecture === 'arm64')
  ))
    throw new Error('Use an Apple Silicon Mac or a Linux x86_64 machine');
  const account_home = await realpath(environment.HOME || homedir()),
    uid = process.getuid?.();
  if (uid === undefined) throw new Error('Home installation requires a Unix account');
  const command_environment: NodeJS.ProcessEnv = {
    HOME: account_home,
    PATH: environment.PATH || '/usr/local/bin:/usr/bin:/bin',
    LANG: 'C.UTF-8',
    ...(environment.XDG_RUNTIME_DIR ? { XDG_RUNTIME_DIR: environment.XDG_RUNTIME_DIR } : {}),
    ...(environment.DBUS_SESSION_BUS_ADDRESS
      ? { DBUS_SESSION_BUS_ADDRESS: environment.DBUS_SESSION_BUS_ADDRESS }
      : {}),
  };
  const command: HomeCommand = (argv, scope, timeout_seconds, allow_nonzero = false) => {
    if (installer_test_mode(environment) && ['systemctl', 'launchctl'].includes(basename(argv[0]!)))
      throw new Error('Home service actions are unavailable in tests');
    return run_release_command(
      argv,
      { timeout_seconds, allow_nonzero, environment: command_environment },
      scope,
    );
  };
  return {
    execution,
    environment,
    system,
    architecture,
    account_home,
    uid,
    command,
    extract: (archive, destination, digest, revision, scope) =>
      extract_app_archive(
        archive,
        destination,
        digest,
        revision,
        scope.child_processes,
        undefined,
        scope,
        system === 'darwin' ? MAC_HOME : LINUX_HOME,
      ),
    async admit(app, manifest, scope) {
      assert_release_execution(scope);
      if (manifest.runtime_contract?.version !== 2)
        throw new Error('First home installation requires a sealed Node version-2 release');
      await admit_runtime(app, manifest, scope.child_processes);
      require_host_node(process.version, manifest.runtime_contract);
      assert_release_execution(scope);
    },
    healthy: (port, revision, signal) =>
      healthy(
        `http://127.0.0.1:${port}/api/health`,
        revision,
        HOME_HEALTH_TIMEOUT_SECONDS,
        signal,
        { ...environment },
        true,
      ),
    service: (request, host) =>
      system === 'darwin'
        ? create_darwin_home_service(request, host)
        : create_linux_home_service(request, host),
    warning: (message) => console.warn(`Warning: ${message}`),
  };
}
