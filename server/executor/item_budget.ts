/** An item budget limits observation, never an admitted save or native effect. */
import { PlannerError } from '../core/contracts.ts';

export async function answer_by<T>(
  promise: Promise<T>,
  deadline: number,
): Promise<{ done: true; value: T } | { done: false }> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise.then((value) => ({ done: true as const, value })),
      new Promise<{ done: false }>((resolve) => {
        timer = setTimeout(
          () => resolve({ done: false }),
          Math.max(deadline * 1000 - performance.now(), 0),
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export async function wait_for_item<T>(promise: Promise<T>, deadline?: number): Promise<T> {
  if (deadline === undefined) return promise;
  const answer = await answer_by(promise, deadline);
  if (answer.done) return answer.value;
  throw new PlannerError(
    'retry_later',
    'The conversation is still busy with earlier work; send this item again',
  );
}
