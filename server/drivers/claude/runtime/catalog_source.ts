/** Claude's production model catalog: owned probes, sealed at dispose with a native-close retry. */
import type { OwnedOneShotChildren, StopOptions } from '../../../lifecycle/contracts.ts';
import type { ClaudeModelCatalogSource } from './contracts.ts';
import { ClaudeModelCatalogUnavailable } from './contracts.ts';
import { backend_io_is_test } from '../../../conversation/snapshot/environment.ts';
import {
  ModelCatalogCleanupFailed,
  retry_model_catalog_cleanup,
} from '../../../conversation/backends/catalog_cleanup.ts';
import {
  probe_claude_model_catalog,
  ClaudeModelCatalogUnavailable as ProviderUnavailable,
} from './model_catalog.ts';

export interface ClaudeCatalogSource {
  readonly source: ClaudeModelCatalogSource;
  /** Seals the source, drains its probes and retries a failed native close. May be called again. */
  dispose(options?: StopOptions): Promise<void>;
}
export function create_claude_catalog_source(children: OwnedOneShotChildren): ClaudeCatalogSource {
  const failed_cleanup = new Set<ModelCatalogCleanupFailed>();
  const cleanup_history = new Set<unknown>();
  const probes = new Set<Promise<unknown>>();
  let closed = false;
  let stop_options: StopOptions | undefined;
  return {
    async source(executable) {
      if (closed) throw new ClaudeModelCatalogUnavailable('Claude model discovery is shut down.');
      if (backend_io_is_test())
        throw new ClaudeModelCatalogUnavailable(
          'Production catalog reads are unavailable in tests.',
        );
      const operation = probe_claude_model_catalog(executable, children, {
        signal: children.signal,
        stop_options: () => stop_options,
      });
      probes.add(operation);
      try {
        return await operation;
      } catch (error) {
        if (error instanceof ModelCatalogCleanupFailed) failed_cleanup.add(error);
        if (error instanceof ProviderUnavailable)
          throw new ClaudeModelCatalogUnavailable(error.message);
        throw error;
      } finally {
        probes.delete(operation);
      }
    },
    async dispose(options) {
      stop_options ??= options;
      closed = true;
      const results = await Promise.allSettled([...probes]);
      for (const result of results)
        if (result.status === 'rejected' && result.reason instanceof ModelCatalogCleanupFailed)
          failed_cleanup.add(result.reason);
      // Registry dispose runs every instance at once, so this retry never waits behind the
      // one-shot barrier that another instance's shutdown joins.
      await retry_model_catalog_cleanup(failed_cleanup, cleanup_history);
    },
  };
}
