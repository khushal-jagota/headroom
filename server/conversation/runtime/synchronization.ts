/** Receipt barriers own turn replacement until the accepted record settles. */
import type { ConversationState } from './state.ts';

export class LockLease {
  private release_lock: (() => void) | null;
  readonly state: ConversationState;
  constructor(state: ConversationState, release: () => void) {
    this.state = state;
    this.release_lock = release;
  }
  release(): void {
    const release = this.release_lock;
    if (!release) throw new Error('conversation lock is not held');
    this.release_lock = null;
    release();
  }
  async reacquire(): Promise<void> {
    if (this.release_lock) throw new Error('conversation lock is already held');
    this.release_lock = await this.state.lock.acquire();
  }
  async unlocked<T>(operation: () => Promise<T>): Promise<T> {
    this.release();
    try {
      return await operation();
    } finally {
      await this.reacquire();
    }
  }
}
export async function acquire_after_steers(state: ConversationState): Promise<LockLease> {
  while (true) {
    for (const barrier of [...state.steer_settlements]) await barrier.promise;
    const release = await state.lock.acquire();
    if (state.steer_settlements.size === 0) return new LockLease(state, release);
    release();
  }
}
export async function acquire_settled(state: ConversationState): Promise<LockLease> {
  while (true) {
    const lease = await acquire_after_steers(state);
    if (state.phase === 'idle' || state.phase === 'running') return lease;
    lease.release();
    await state.phase_settled.promise;
  }
}
