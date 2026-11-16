/** SQL counters must fit the native executor number contract. */
import { ExecutorValidationError } from '../protocol_errors.ts';
export function counter(value: unknown): number {
  const result = Number(value);
  if (!Number.isSafeInteger(result))
    throw new ExecutorValidationError('Executor counter exceeds the safe integer range');
  return result;
}
