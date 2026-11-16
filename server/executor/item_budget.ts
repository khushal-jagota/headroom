/** An item budget limits observation, never an admitted save or native effect. */
import { PlannerError } from '../core/contracts.ts';
import { observe_promise } from '../core/promise_observer.ts';

export async function wait_for_item<T>(promise: Promise<T>, deadline?: number): Promise<T> {
  if (deadline === undefined) return promise;
  const answer = await observe_promise(promise, Math.max(deadline * 1000 - performance.now(), 0));
  if (answer.completed) return answer.value;
  throw new PlannerError(
    'retry_later',
    'The conversation is still busy with earlier work; send this item again',
  );
}
