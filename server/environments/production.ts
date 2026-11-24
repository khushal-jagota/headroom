/** Bind real environment adapters to one server invocation. Default activation stays separate. */
import { dirname, join } from 'node:path';
import { readFile } from 'node:fs/promises';
import { update_repository_configuration } from '../core/config.ts';
import { is_os_error } from '../core/system_errors.ts';
import type { ProductionEnvironmentFactory } from './production_contracts.ts';
import { resolved_path } from './paths.ts';
import { create_release_staging } from './updates/staging.ts';
import { create_installer_operations } from './updates/installation.ts';
import { GitHubUpdateProvider } from './updates/provider.ts';
import { create_mac_update_composition } from './updates/mac/composition.ts';
import { assert_installer_path, installer_test_mode } from './updates/installer/configuration.ts';
import { collect_vps_status } from './vps_status/collect.ts';
import { MAC_HOME } from './release/contracts.ts';
import { GitHubReleaseServerPackages } from '../server_move/packages/github_releases.ts';

/** Python selects the unified cgroup's service leaf and excludes the user manager. */
export function own_service_unit(cgroup: string): string | null {
  for (const line of cgroup.split(/\r?\n/)) {
    const separator = line.indexOf('::');
    if (separator < 0 || line.slice(0, separator) !== '0') continue;
    const path = line.slice(separator + 2).replace(/\/+$/u, '');
    const leaf = path.slice(path.lastIndexOf('/') + 1);
    if (leaf.endsWith('.service') && !leaf.startsWith('user@')) return leaf;
  }
  return null;
}

async function read_own_service_unit(
  environment: Readonly<NodeJS.ProcessEnv>,
  test_mode: boolean,
): Promise<string | null> {
  if (test_mode || installer_test_mode(environment)) return null;
  try {
    const bytes = await readFile('/proc/self/cgroup');
    return own_service_unit(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch (error) {
    if (is_os_error(error)) return null;
    throw error;
  }
}

export const create_production_environment_operations: ProductionEnvironmentFactory = async (
  context,
  dependencies,
) => {
  if (context.config.executor_id !== null && dependencies.executor === null)
    throw new Error('Configured production executor bindings are required');

  const environment = Object.freeze({ ...process.env });
  const signal = context.one_shot_children.signal;
  signal.throwIfAborted();
  assert_installer_path(dependencies.application_root, environment);
  const application_root = await resolved_path(dependencies.application_root);
  signal.throwIfAborted();
  let darwin_home: import('./updates/darwin/service.ts').DarwinHomeService | undefined;
  if (
    process.platform === 'darwin' &&
    !context.config.test_mode &&
    !installer_test_mode(environment)
  ) {
    try {
      const { current_darwin_home } = await import('./updates/darwin/launch.ts');
      darwin_home = await current_darwin_home(
        application_root,
        { child_processes: context.child_processes, signal, deadline: null },
        environment,
      );
    } catch (error) {
      /* A source checkout or an unproven login job cannot offer home updates. */
      console.warn('Home updates are unavailable: the login job is not proven', error);
    }
  }
  const service_unit =
    process.platform === 'darwin'
      ? darwin_home
        ? `${darwin_home.identity.domain}/${darwin_home.identity.label}`
        : null
      : await read_own_service_unit(environment, context.config.test_mode);
  signal.throwIfAborted();
  const repository = update_repository_configuration(environment, dirname(context.config.db_path));

  if (service_unit && context.config.app_sha && !context.config.test_mode && !installer_test_mode(environment)) {
    const { installed_update_root } = await import('./updates/service.ts');
    const root = await installed_update_root(context.config.app_sha, application_root, context.config.db_path, service_unit);
    if (root) {
      const { FileUpdateAttemptStore, attempt_path } = await import('./updates/attempts.ts');
      const data = join(root, 'data');
      const store = new FileUpdateAttemptStore(attempt_path(data), () => {});
      const attempt = store.read() ?? store.import_legacy_request(data, context.config.app_sha);
      if (attempt && (attempt.state === 'installing' || attempt.state === 'done') && attempt.to_sha === context.config.app_sha) {
        const { ensure_upgrade_machine } = await import('../installation/upgrade_machine.ts');
        const { production_home_dependencies } = await import('../installation/production.ts');
        await ensure_upgrade_machine(root, service_unit, context.config.port, attempt.to_sha, context.database,
          await production_home_dependencies({child_processes:context.child_processes, signal, deadline:null}, environment));
      }
    }
  }
  return {
    app_root: application_root,
    service_unit,
    // Runtime invokes this only after installed_update_root accepts the actual paths/unit.
    provider: () =>
      repository === null
        ? null
        : new GitHubUpdateProvider(
            repository.repository,
            repository.token_file,
            process.platform === 'darwin' ? { target: MAC_HOME } : {},
          ),
    server_packages: () =>
      repository === null
        ? null
        : new GitHubReleaseServerPackages(repository.repository, repository.token_file),
    staging: create_release_staging(
      context.child_processes,
      signal,
      undefined,
      process.platform === 'darwin' ? MAC_HOME : undefined,
    ),
    installer: create_installer_operations({
      application_root,
      node_executable: dependencies.node_executable,
      child_processes: context.child_processes,
      signal,
      environment,
      ...(darwin_home ? { darwin_home } : {}),
    }),
    create_mac: (mac_context) => create_mac_update_composition(mac_context, dependencies.executor),
    collector: {
      module_url: new URL('./vps_metrics/collectors/index.ts', import.meta.url).href,
      export_name: 'build_home_collector',
    },
    collect_status: collect_vps_status,
  };
};
