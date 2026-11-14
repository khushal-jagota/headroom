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
import type { StopOptions } from '../../../lifecycle/contracts.ts';
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
    options.environment ??
    new SubprocessBackendProbeEnvironment(
      options.one_shot_children,
      options.host.environment,
      options.host.executable_path,
    );
  const paths = options.host.hermes_paths;
  const failed_cleanup = new Set<ModelCatalogCleanupFailed>();
  const cleanup_history = new Set<unknown>();
  let environment_stop: Promise<PromiseSettledResult<void | undefined>[]> | undefined;
  let source_stop_options: StopOptions | undefined;
  const retain = (error: ModelCatalogCleanupFailed) => {
    failed_cleanup.add(error);
    return catalog.resource_failure(error);
  };
  const hermes_launch = hermes_model_catalog_launch({
    hermes_python: paths.python,
    hermes_home: paths.home,
    hermes_python_source_root: paths.source_root,
  });
  const hermes = new HermesModelCatalogOwner(
    {
      ...hermes_launch,
      environment_overrides: [
        ...Object.entries(options.host.environment).filter(
          (entry): entry is [string, string] => entry[1] !== undefined,
        ),
        ...hermes_launch.environment_overrides,
      ],
    },
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
    read_codex_login_standing: () =>
      read_codex_login_standing(null, options.host.native_skill_home('codex')),
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
                environment: options.host.environment,
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
          const controller = new AbortController();
          controllers.add(controller);
          try {
            return await own(
              probe_claude_model_catalog(executable, options.one_shot_children, {
                signal: controller.signal,
                stop_options: () => source_stop_options,
                environment: options.host.environment,
              }),
            );
          } catch (error) {
            if (error instanceof ModelCatalogCleanupFailed) throw retain(error);
            if (error instanceof NativeClaudeUnavailable)
              throw new ClaudeModelCatalogUnavailable(error.message);
            throw error;
          } finally {
            controllers.delete(controller);
          }
        },
      }),
    ),
  });
  const catalog = new MacCatalog({
    snapshots,
    on_failure: options.on_failure,
    async cancel_sources(reason) {
      for (const controller of controllers) controller.abort(reason);
      const cleanup = await Promise.allSettled([hermes.cancel(reason), ...probes]);
      for (const result of cleanup)
        if (result.status === 'rejected' && result.reason instanceof ModelCatalogCleanupFailed)
          throw retain(result.reason);
      if (cleanup[0]!.status === 'rejected') throw cleanup[0]!.reason;
    },
    async stop_sources(stop_options) {
      source_stop_options ??= stop_options;
      closed = true;
      const reason = new DOMException('Mac catalogs stopped.', 'AbortError');
      for (const controller of controllers) controller.abort(reason);
      // Start the invocation barrier, but retry failed native closes before
      // joining it: its process-close wait can depend on exactly that retry.
      environment_stop ??= Promise.allSettled([
        Promise.resolve().then(() => environment.shutdown?.()),
      ]);
      const cleanup = await Promise.allSettled([
        Promise.resolve().then(() => hermes.shutdown()),
        ...probes,
      ]);
      for (const result of cleanup)
        if (result.status === 'rejected' && result.reason instanceof ModelCatalogCleanupFailed)
          retain(result.reason);
      // Business reads return their own optional answer. Only cleanup owners certify release.
      await retry_model_catalog_cleanup(
        failed_cleanup,
        cleanup_history,
        environment_stop.then((results) => [...results, ...cleanup.slice(0, 1)]),
      );
    },
  });
  return catalog;
}
