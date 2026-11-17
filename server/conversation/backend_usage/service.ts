import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import type { BackendUsageAdapter, BackendUsageResult } from './contracts.ts';
import { FifoLock } from '../runtime/state.ts';
export class BackendUsageService {
  private readonly locks: ReadonlyMap<BackendInstanceId, FifoLock>;
  private readonly adapters: ReadonlyMap<
    BackendInstanceId,
    { readonly label: string; readonly adapter?: BackendUsageAdapter }
  >;
  constructor(
    adapters: ReadonlyMap<
      BackendInstanceId,
      { readonly label: string; readonly adapter?: BackendUsageAdapter }
    >,
  ) {
    this.adapters = adapters;
    this.locks = new Map([...adapters.keys()].map((id) => [id, new FifoLock()]));
  }
  async refresh(
    backend_key: BackendInstanceId,
    options: { signal?: AbortSignal } = {},
  ): Promise<BackendUsageResult> {
    const entry = this.adapters.get(backend_key);
    const title = entry?.label ?? backend_key;
    if (entry === undefined)
      return {
        backend_key,
        outcome: 'unavailable',
        detail: `${title} does not expose usage to Panels.`,
        observed_at: null,
        windows: [],
      };
    return this.locks.get(backend_key)!.run(async () => {
      options.signal?.throwIfAborted();
      const adapter = entry.adapter;
      if (!adapter)
        return {
          backend_key,
          outcome: 'unavailable',
          detail: `${title} does not expose usage to Panels.`,
          observed_at: null,
          windows: [],
        };
      try {
        return await adapter.refresh(options);
      } catch {
        options.signal?.throwIfAborted();
        return {
          backend_key,
          outcome: 'failed',
          detail: `${title} usage could not be refreshed. Try again.`,
          observed_at: null,
          windows: [],
        };
      }
    });
  }
}
