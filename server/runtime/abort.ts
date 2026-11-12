/** Stop a caller's wait without cancelling an exchange owned by the conversation core. */
export class ConversationStartAborted extends Error {
  constructor() {
    super('conversation start aborted');
    this.name = 'AbortError';
  }
}
/** Invocation-owned raw exchanges survive cancellation of their caller's observer. */
export interface ConversationOperationScope {
  settled(): Promise<void>;
}
const raw_operations = new WeakMap<AbortSignal, Set<Promise<void>>>();
function pending_for(signal: AbortSignal): Set<Promise<void>> {
  let pending = raw_operations.get(signal);
  if (pending === undefined) {
    pending = new Set();
    raw_operations.set(signal, pending);
  }
  return pending;
}
export function conversation_operation_scope(signal: AbortSignal): ConversationOperationScope {
  const pending = pending_for(signal);
  return {
    async settled() {
      while (pending.size) await Promise.allSettled([...pending]);
    },
  };
}
export function observe_conversation_operation<T>(
  operation: () => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  if (signal?.aborted) return Promise.reject(new ConversationStartAborted());
  const pending = signal === undefined ? undefined : pending_for(signal);
  let release!: () => void;
  const raw = new Promise<void>((resolve) => {
    release = resolve;
  });
  // Reserve before the operation can synchronously request shutdown.
  pending?.add(raw);
  return new Promise<T>((resolve, reject) => {
    const aborted = () => reject(new ConversationStartAborted());
    signal?.addEventListener('abort', aborted, { once: true });
    Promise.resolve()
      .then(() => {
        if (signal?.aborted) throw new ConversationStartAborted();
        return operation();
      })
      .then(resolve, reject)
      .finally(() => {
        signal?.removeEventListener('abort', aborted);
        pending?.delete(raw);
        release();
      });
  });
}
