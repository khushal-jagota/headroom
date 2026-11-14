/** Expected failures at the executor's JSON and schema boundary. */
import type { ErrorObject } from 'ajv';

export class ExecutorValidationError extends Error {
  readonly issues: readonly ErrorObject[];
  constructor(message: string, issues: readonly ErrorObject[] = []) {
    super(message);
    this.name = 'ExecutorValidationError';
    this.issues = issues;
  }
}
