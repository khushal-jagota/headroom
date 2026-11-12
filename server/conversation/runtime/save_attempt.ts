/** Runtime predictions and their rollback belong to one synchronous store attempt. */
import type { RuntimeMutation, SaveAttempt } from './contracts.ts';

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
