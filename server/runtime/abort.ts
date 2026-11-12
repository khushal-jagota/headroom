/** Stop a caller's wait without cancelling an exchange owned by the conversation core. */
export class ConversationStartAborted extends Error {
  constructor() {
    super('conversation start aborted');
    this.name = 'AbortError';
  }
}
export function observe_conversation_operation<T>(
  operation: () => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  if (signal?.aborted) return Promise.reject(new ConversationStartAborted());
  return new Promise<T>((resolve, reject) => {
    const aborted = () => reject(new ConversationStartAborted());
    signal?.addEventListener('abort', aborted, { once: true });
    Promise.resolve()
      .then(() => {
        if (signal?.aborted) throw new ConversationStartAborted();
        return operation();
      })
      .then(resolve, reject)
      .finally(() => signal?.removeEventListener('abort', aborted));
  });
}
