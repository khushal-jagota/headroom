import { run_backend_request } from '../../backend_controls.ts';
/** Standalone machine catalogs reuse each driver's probes without a VPS database or login service. */
import { BackendSnapshotService } from '../../../conversation/snapshot/service.ts';
import { SubprocessBackendProbeEnvironment } from '../../../conversation/snapshot/environment.ts';
import type { BackendProbeEnvironment } from '../../../conversation/snapshot/contracts.ts';
import {
  ModelCatalogCleanupFailed,
  retry_model_catalog_cleanup,
} from '../../../conversation/backends/catalog_cleanup.ts';
import type { ExecutorCatalogCustody } from '../../../drivers/core/executor.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { MacAdapterHost } from '../adapters.ts';
import { MacCatalog } from './index.ts';
export function create_mac_catalog(options: {
  readonly host: MacAdapterHost;
  readonly resolve_backend_secret?: (id: string) => Promise<string>;
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
  const failed_cleanup = new Set<ModelCatalogCleanupFailed>();
  const cleanup_history = new Set<unknown>();
  let environment_stop: Promise<PromiseSettledResult<void | undefined>[]> | undefined;
  let source_stop_options: StopOptions | undefined;
  const retain = (error: ModelCatalogCleanupFailed) => {
    failed_cleanup.add(error);
    return catalog.resource_failure(error);
  };
  const controllers = new Set<AbortController>(),
    probes = new Set<Promise<unknown>>();
  let closed = false;
  const custody: ExecutorCatalogCustody = {
    async probe(read) {
      if (closed) throw new DOMException('Mac catalogs stopped.', 'AbortError');
      const controller = new AbortController();
      controllers.add(controller);
      let operation: Promise<unknown> | undefined;
      try {
        const reading = read(controller.signal);
        probes.add((operation = reading));
        return await reading;
      } catch (error) {
        if (error instanceof ModelCatalogCleanupFailed) throw retain(error);
        throw error;
      } finally {
        if (operation) probes.delete(operation);
        controllers.delete(controller);
      }
    },
    retain,
    log(error) {
      if (error instanceof ModelCatalogCleanupFailed) retain(error);
      else catalog.resource_failure(error);
    },
    stop_options: () => source_stop_options,
  };
  const sources = options.host.drivers.map((driver) => ({
    instance_id: driver.instance_id,
    executable_name: driver.manifest.executable.name,
    source: driver.executor.catalog(driver.host, custody),
  }));
  const snapshots = new BackendSnapshotService({
    environment,
    now: options.now,
    machine_catalog: true,
    sources: sources.map(({ instance_id, executable_name, source }) => ({
      instance_id,
      executable_name,
      status: source.status,
    })),
  });
  const owned = sources.map(({ source }) => source);
  const catalog = new MacCatalog({
    snapshots,
    backend_request: (request, signal) =>
      run_backend_request(
        request,
        sources.find(({ instance_id }) => instance_id === request.backend_key)?.source,
        signal,
        options.resolve_backend_secret,
        snapshots,
      ),
    backend_ids: sources.map(({ instance_id }) => instance_id),
    on_failure: options.on_failure,
    async cancel_sources(reason) {
      for (const controller of controllers) controller.abort(reason);
      const cancels = owned.map((source) => Promise.resolve().then(() => source.cancel?.(reason)));
      const cleanup = await Promise.allSettled([...cancels, ...probes]);
      for (const result of cleanup)
        if (result.status === 'rejected' && result.reason instanceof ModelCatalogCleanupFailed)
          throw retain(result.reason);
      for (const result of cleanup.slice(0, cancels.length))
        if (result.status === 'rejected') throw result.reason;
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
      const shutdowns = owned.map((source) => Promise.resolve().then(() => source.shutdown?.()));
      const cleanup = await Promise.allSettled([...shutdowns, ...probes]);
      for (const result of cleanup)
        if (result.status === 'rejected' && result.reason instanceof ModelCatalogCleanupFailed)
          retain(result.reason);
      // Business reads return their own optional answer. Only cleanup owners certify release.
      await retry_model_catalog_cleanup(
        failed_cleanup,
        cleanup_history,
        environment_stop.then((results) => [...results, ...cleanup.slice(0, shutdowns.length)]),
      );
    },
  });
  return catalog;
}
