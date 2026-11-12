import { ConversationBackendKey } from '../contracts.ts';
import type { BackendUsageAdapter, BackendUsageResult } from './contracts.ts';
import { FifoLock } from '../runtime/state.ts';
export class BackendUsageService {
  private readonly locks = new Map(
    Object.values(ConversationBackendKey).map((key) => [key, new FifoLock()]),
  );
  private readonly adapters: Readonly<Partial<Record<ConversationBackendKey, BackendUsageAdapter>>>;
  constructor(adapters: Readonly<Partial<Record<ConversationBackendKey, BackendUsageAdapter>>>) {
    this.adapters = adapters;
  }
  async refresh(
    backend_key: ConversationBackendKey,
    options: { signal?: AbortSignal } = {},
  ): Promise<BackendUsageResult> {
    return this.locks.get(backend_key)!.run(async () => {
      options.signal?.throwIfAborted();
      const title = backend_key[0].toUpperCase() + backend_key.slice(1);
      const adapter = this.adapters[backend_key];
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
