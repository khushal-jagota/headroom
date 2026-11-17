import { failure_detail } from '../failure_detail.ts';
/** Redacted retry-log detail: name, status, code, message and cause chain. */
export function mailbox_failure(error: unknown): string {
  return failure_detail(error);
}
