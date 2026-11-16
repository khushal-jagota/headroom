/** Runtime predictions and their rollback belong to one synchronous store attempt. */
import type { RuntimeMutation, SaveAttempt } from './contracts.ts';

/** Cancel runtime predictions inside a refused participant, before commit steps run. */
export class RemoteEventPrediction implements RuntimeMutation {
  active = false;
  private undo: (() => void)[] = [];
  private readonly mutations: readonly RuntimeMutation[];
  constructor(mutations: readonly RuntimeMutation[]) {
    this.mutations = mutations;
  }
  apply(): () => void {
    this.active = true;
    this.undo = [];
    try {
      for (const mutation of this.mutations) this.undo.push(mutation.apply());
    } catch (error) {
      this.rollback();
      throw error;
    }
    return () => this.rollback();
  }
  rollback(): void {
    this.active = false;
    for (const restore of this.undo.splice(0).reverse()) restore();
  }
}

export function field_mutation<T extends object, K extends keyof T>(
  target: T,
  key: K,
  value: T[K],
): RuntimeMutation {
  return {
    apply() {
      const previous = target[key];
      target[key] = value;
      return () => {
        target[key] = previous;
      };
    },
  };
}
export const save_attempt: SaveAttempt = (_conversation_id, work, mutations) => {
  const undo: (() => void)[] = [];
  try {
    for (const mutation of mutations) undo.push(mutation.apply());
    return work();
  } catch (error) {
    for (const restore of undo.reverse()) restore();
    throw error;
  }
};
