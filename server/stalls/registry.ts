import { finding_key } from './contracts.ts';
import type { TrackedWait, TrackedWaitHandle, TrackedWaitKind, WaitRegistry } from './contracts.ts';

/** Scoped process-local waits. Call close in finally, including failed save attempts. */
export class ProcessWaitRegistry implements WaitRegistry {
  private readonly entries = new Map<number, TrackedWait>();
  private next_id = 1;
  private readonly clock: () => number;
  constructor(clock: () => number = Date.now) {
    this.clock = clock;
  }
  track(
    kind: TrackedWaitKind,
    wait_key: string,
    options: { detail: string; conversation_id?: string | null },
  ): TrackedWaitHandle {
    const id = this.next_id++;
    this.entries.set(id, {
      kind,
      wait_key,
      conversation_id: options.conversation_id ?? null,
      detail: options.detail,
      started_at_ms: this.clock(),
    });
    return {
      set_detail: (detail) => {
        const entry = this.entries.get(id);
        if (entry) this.entries.set(id, { ...entry, detail });
      },
      close: () => {
        this.entries.delete(id);
      },
    };
  }
  snapshot(): readonly TrackedWait[] {
    const earliest = new Map<string, TrackedWait>();
    for (const wait of this.entries.values()) {
      const key = finding_key(wait);
      const kept = earliest.get(key);
      if (!kept || wait.started_at_ms < kept.started_at_ms) earliest.set(key, wait);
    }
    return [...earliest.values()].map((wait) => ({ ...wait }));
  }
}
export const WAITS = new ProcessWaitRegistry();
