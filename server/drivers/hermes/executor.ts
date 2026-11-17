/** Hermes on an executor machine: its ACP launch and its model list owner. */
import { ModelCatalogCleanupFailed } from '../../conversation/backends/catalog_cleanup.ts';
import { with_machine_environment } from '../core/executor.ts';
import type { ExecutorDriver, ExecutorDriverHost } from '../core/executor.ts';
import { configured_file_path } from '../core/paths.ts';
import { hermes_manifest } from './manifest.ts';
import { create_hermes_backend_child_factory } from './runtime/adapter.ts';
import { HermesModelCatalogOwner, hermes_model_catalog_launch } from './runtime/catalog.ts';
import { hermes_acp_child_launch, hermes_source_root } from './runtime/launch.ts';
import { create_hermes_status } from './runtime/status.ts';

/** The executor host has already resolved the configured Python and home for this machine. */
function hermes_paths(host: ExecutorDriverHost) {
  const python = configured_file_path(
    hermes_manifest.executable.configured_beside!,
    host.environment,
  );
  return { python, home: host.home!, source_root: hermes_source_root(python) };
}
export const hermes_executor: ExecutorDriver = {
  child_factory(host) {
    const paths = hermes_paths(host);
    return create_hermes_backend_child_factory(
      with_machine_environment(
        hermes_acp_child_launch({
          hermes_python: paths.python,
          hermes_home: paths.home,
          hermes_python_source_root: paths.source_root,
          panels_server_url: host.panels_server_url,
        }),
        host,
      ),
      { child_processes: host.child_processes, worker_supervision: host.worker_supervision },
    );
  },
  catalog(host, custody) {
    const paths = hermes_paths(host);
    const owner = new HermesModelCatalogOwner(
      with_machine_environment(
        hermes_model_catalog_launch({
          hermes_python: paths.python,
          hermes_home: paths.home,
          hermes_python_source_root: paths.source_root,
        }),
        host,
      ),
      { child_processes: host.child_processes, log: (error) => custody.log(error) },
    );
    return {
      status: create_hermes_status({
        catalog: async (request) => {
          try {
            return await owner.source(request);
          } catch (error) {
            if (error instanceof ModelCatalogCleanupFailed) throw custody.retain(error);
            throw error;
          }
        },
      }),
      cancel: (reason) => owner.cancel(reason),
      shutdown: () => owner.shutdown(),
    };
  },
};
