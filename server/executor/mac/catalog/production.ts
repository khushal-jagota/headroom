/** Standalone machine catalogs reuse provider probes without a VPS database or login service. */
import { BackendSnapshotService } from '../../../conversation/snapshot/service.ts';
import { create_backend_probe } from '../../../conversation/snapshot/probe.ts';
import { create_catalog_readers } from '../../../conversation/snapshot/catalogs/readers.ts';
import { SubprocessBackendProbeEnvironment } from '../../../conversation/snapshot/environment.ts';
import type { BackendProbeEnvironment } from '../../../conversation/snapshot/contracts.ts';
import { ClaudeModelCatalogUnavailable } from '../../../conversation/snapshot/provider_contracts.ts';
import {
  probe_claude_model_catalog,
  ClaudeModelCatalogUnavailable as NativeClaudeUnavailable,
} from '../../../conversation/backends/claude_model_catalog.ts';
import {
  ModelCatalogCleanupFailed,
  retry_model_catalog_cleanup,
} from '../../../conversation/backends/catalog_cleanup.ts';
import { probe_codex_model_catalog } from '../../../conversation/backends/codex_app_server/model_catalog.ts';
import { read_codex_login_standing } from '../../../conversation/backends/codex_app_server/providers.ts';
import {
  HermesModelCatalogOwner,
  hermes_model_catalog_launch,
} from '../../../conversation/backends/hermes_acp/catalog.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import type { MacAdapterHost } from '../adapters.ts';
import { MacCatalog } from './index.ts';
export function create_mac_catalog(options: {
  readonly host: MacAdapterHost;
  readonly child_processes: OwnedChildProcesses;
  readonly one_shot_children: OwnedOneShotChildren;
  readonly on_failure: (error: Error) => void;
  readonly environment?: BackendProbeEnvironment;
  readonly now?: () => Date;
}): MacCatalog {
  const environment =
    options.environment ?? new SubprocessBackendProbeEnvironment(options.one_shot_children);
  const paths = options.host.hermes_paths;
  const failed_cleanup = new Set<ModelCatalogCleanupFailed>();
  const retain = (error: ModelCatalogCleanupFailed) => {
    failed_cleanup.add(error);
    return catalog.resource_failure(error);
  };
  const hermes = new HermesModelCatalogOwner(
    hermes_model_catalog_launch({
      hermes_python: paths.python,
      hermes_home: paths.home,
      hermes_python_source_root: paths.source_root,
    }),
    {
      child_processes: options.child_processes,
      log: (error) => {
        if (error instanceof ModelCatalogCleanupFailed) retain(error);
        else catalog.resource_failure(error);
      },
    },
  );
  const controllers = new Set<AbortController>(),
    probes = new Set<Promise<unknown>>();
  let closed = false;
  const own = async <T>(operation: Promise<T>): Promise<T> => {
    probes.add(operation);
    try {
      return await operation;
    } catch (error) {
      if (error instanceof ModelCatalogCleanupFailed) retain(error);
      throw error;
    } finally {
      probes.delete(operation);
    }
  };
  const snapshots = new BackendSnapshotService({
    environment,
    now: options.now,
    machine_catalog: true,
    // Python's machine snapshot reads login standing independently of each child factory.
    read_codex_login_standing: () => read_codex_login_standing(null),
    probe: create_backend_probe(
      create_catalog_readers({
        hermes: async (request) => {
          try {
            return await hermes.source(request);
          } catch (error) {
            if (error instanceof ModelCatalogCleanupFailed) throw retain(error);
            throw error;
          }
        },
        codex: async (executable) => {
          if (closed) throw new DOMException('Mac catalogs stopped.', 'AbortError');
          const controller = new AbortController();
          controllers.add(controller);
          try {
            return await own(
              probe_codex_model_catalog(executable, {
                child_processes: options.child_processes,
                signal: controller.signal,
              }),
            );
          } catch (error) {
            if (error instanceof ModelCatalogCleanupFailed) throw retain(error);
            throw error;
          } finally {
            controllers.delete(controller);
          }
        },
        claude: async (executable) => {
          if (closed) throw new DOMException('Mac catalogs stopped.', 'AbortError');
          try {
            return await own(probe_claude_model_catalog(executable, options.one_shot_children));
          } catch (error) {
            if (error instanceof ModelCatalogCleanupFailed) throw retain(error);
            if (error instanceof NativeClaudeUnavailable)
              throw new ClaudeModelCatalogUnavailable(error.message);
            throw error;
          }
        },
      }),
    ),
  });
  const catalog = new MacCatalog({
    snapshots,
    on_failure: options.on_failure,
    async stop_sources() {
      closed = true;
      const reason = new DOMException('Mac catalogs stopped.', 'AbortError');
      for (const controller of controllers) controller.abort(reason);
      // Start the invocation barrier, but retry failed native closes before
      // joining it: its process-close wait can depend on exactly that retry.
      const environment_stop = Promise.allSettled([environment.shutdown?.()]);
      const cleanup = await Promise.allSettled([hermes.shutdown(), ...probes]);
      for (const result of cleanup)
        if (result.status === 'rejected' && result.reason instanceof ModelCatalogCleanupFailed)
          retain(result.reason);
      await retry_model_catalog_cleanup(failed_cleanup);
      const environment_result = await environment_stop;
      // Business reads return their own optional answer. Only cleanup owners certify release.
      const failed = [...environment_result, ...cleanup.slice(0, 1)].filter(
        (result): result is PromiseRejectedResult =>
          result.status === 'rejected' && !(result.reason instanceof ModelCatalogCleanupFailed),
      );
      if (failed.length)
        throw new AggregateError(
          failed.map((result) => result.reason),
          'Mac catalog sources did not close.',
        );
    },
  });
  return catalog;
}
