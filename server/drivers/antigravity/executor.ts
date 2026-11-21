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
    return {
      status: create_antigravity_status({
        catalog: (executable) => custody.probe((signal) => catalog.read(executable, signal)),
      }),
      shutdown: () => catalog.shutdown(custody.stop_options()),
    };
  },
};
