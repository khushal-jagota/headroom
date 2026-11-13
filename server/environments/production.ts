/** Bind real environment adapters to one server invocation. Default activation stays separate. */
import { readFile } from 'node:fs/promises';
import { update_repository_configuration } from '../core/config.ts';
import { is_os_error } from '../core/system_errors.ts';
import { splitlines } from '../core/unidentified_agent_calls/logic.ts';
import type { ProductionEnvironmentFactory } from './production_contracts.ts';
import { resolved_path } from './paths.ts';
import { create_release_staging } from './updates/staging.ts';
import { create_installer_operations } from './updates/installation.ts';
import { GitHubUpdateProvider } from './updates/provider.ts';
import { create_mac_update_composition } from './updates/mac/composition.ts';
import { assert_installer_path, installer_test_mode } from './updates/installer/configuration.ts';
import { collect_vps_status } from './vps_status/collect.ts';

/** Python selects the unified cgroup's service leaf and excludes the user manager. */
export function own_service_unit(cgroup: string): string | null {
  for (const line of splitlines(cgroup)) {
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
  const service_unit = await read_own_service_unit(environment, context.config.test_mode);
  signal.throwIfAborted();
  const repository = update_repository_configuration(environment);

  return {
    app_root: application_root,
    service_unit,
    // Runtime invokes this only after installed_update_root accepts the actual paths/unit.
    provider: () =>
      repository === null
        ? null
        : new GitHubUpdateProvider(repository.repository, repository.token_file),
    staging: create_release_staging(context.child_processes),
    installer: create_installer_operations({
      application_root,
      node_executable: dependencies.node_executable,
      admitted_legacy_null_sha: dependencies.admitted_legacy_null_sha,
      child_processes: context.child_processes,
      signal,
      environment,
    }),
    create_mac: (mac_context) => create_mac_update_composition(mac_context, dependencies.executor),
    collector: {
      module_url: new URL('./vps_metrics/collectors/index.ts', import.meta.url).href,
      export_name: 'build_linux_collector',
    },
    collect_status: collect_vps_status,
  };
};
