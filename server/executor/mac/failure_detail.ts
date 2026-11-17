import {
  capped_failure_text,
  redacted_failure_text,
} from '../../conversation/logic/backend_events.ts';
/** The longest failure detail one diagnostic line keeps. */
const FAILURE_DETAIL_MAXIMUM_CHARACTERS = 4096;
/** The deepest cause chain one diagnostic line follows. */
const FAILURE_DETAIL_MAXIMUM_CAUSES = 5;
function one_failure(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  const code = (error as NodeJS.ErrnoException).code;
  const status = (error as { status?: unknown }).status;
  return (
    error.name +
    (typeof status === 'number' ? ` status=${status}` : '') +
    (typeof code === 'string' ? ` code=${code}` : '') +
    (error.message ? `: ${error.message}` : '')
  );
}
/**
 * Name, status, code, message and cause chain of one failure, redacted with the
 * conversation backend scrubber and capped. Use `failure_name` for dedupe keys.
 */
export function failure_detail(error: unknown): string {
  const parts = [one_failure(error)];
  let cause = error instanceof Error ? error.cause : undefined;
  for (let depth = 0; cause !== undefined && depth < FAILURE_DETAIL_MAXIMUM_CAUSES; depth++) {
    parts.push(`caused by ${one_failure(cause)}`);
    cause = cause instanceof Error ? cause.cause : undefined;
  }
  return capped_failure_text(
    redacted_failure_text(parts.join('; ')),
    FAILURE_DETAIL_MAXIMUM_CHARACTERS,
  );
}
