/** Payload-free, best-effort notification. SQLite remains the source of truth. */
export class ChangeSignal {
  #subscribers = new Set<() => void>();

  subscribe(subscriber: () => void): () => void {
    this.#subscribers.add(subscriber);
    return () => { this.#subscribers.delete(subscriber); };
  }

  emit(): void {
    for (const subscriber of [...this.#subscribers]) {
      try {
        subscriber();
      } catch (error) {
        console.error('change signal subscriber failed', error);
      }
    }
  }
}
