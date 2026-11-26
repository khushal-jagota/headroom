import { join } from 'node:path';
import { AntigravitySignIn } from './runtime/sign_in.ts';
import { AntigravityUsageAdapter, antigravity_usage_runner } from './runtime/allowance_usage.ts';
import { AGY_APP_DATA_DIRECTORY_NAME, AGY_LOG_DIRECTORY_NAME } from './runtime/configuration.ts';
/** Antigravity on an executor machine: its ACP child factory and throwaway model list probe. */
import type { ExecutorDriver } from '../core/executor.ts';
import { create_acp_child_factory } from '../acp/child.ts';
import { AntigravityModelCatalog } from './runtime/catalog.ts';
import { create_antigravity_flavor } from './runtime/flavor.ts';
import { create_antigravity_status } from './runtime/status.ts';

function required_home(home: string | null): string {
  if (home === null) throw new Error('Antigravity executor requires a resolved home directory.');
  return home;
}

export const antigravity_executor: ExecutorDriver = {
  child_factory(host) {
    return create_acp_child_factory(
      create_antigravity_flavor(
        {
          executable: host.executable,
          home: required_home(host.home),
          panels_server_url: host.panels_server_url,
          environment: host.environment,
        },
        host.one_shot_children,
      ),
      {
        child_processes: host.child_processes,
        worker_supervision: host.worker_supervision,
      },
    );
  },
  catalog(host, custody) {
    const catalog = new AntigravityModelCatalog(
      {
        home: required_home(host.home),
        panels_server_url: host.panels_server_url,
        environment: host.environment,
      },
      {
        child_processes: host.child_processes,
        one_shot_children: host.one_shot_children,
        stop_options: () => custody.stop_options(),
      },
    );
    const log_directory = join(
      required_home(host.home),
      AGY_APP_DATA_DIRECTORY_NAME,
      AGY_LOG_DIRECTORY_NAME,
    );
    const sign_in = new AntigravitySignIn(
      {
        home: required_home(host.home),
        panels_server_url: host.panels_server_url,
        environment: host.environment,
      },
      {
        executable: () => host.executable,
        child_processes: host.child_processes,
        one_shot_children: host.one_shot_children,
        verify: async (executable) => (await catalog.read(executable)).signed_in,
        now: () => new Date(),
      },
    );
    return {
      sign_in,
      allowance_usage: new AntigravityUsageAdapter({
        run_usage: antigravity_usage_runner(
          {
            home: required_home(host.home),
            panels_server_url: host.panels_server_url,
            environment: host.environment,
          },
          () => host.executable,
          host.one_shot_children,
        ),
        now: () => new Date(),
      }),
      status: create_antigravity_status({
        catalog: (executable) => custody.probe((signal) => catalog.read(executable, signal)),
        log_directory,
      }),
      shutdown: async () => {
        await Promise.all([
          catalog.shutdown(custody.stop_options()),
          sign_in.shutdown(custody.stop_options()),
        ]);
      },
    };
  },
};
