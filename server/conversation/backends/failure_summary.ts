/** Failure text shared by the backend runtimes. Secrets are redacted and the length is capped. */
import { capped_failure_text, redacted_failure_text } from '../logic/backend_events.ts';
const SECRET_ASSIGNMENT =
  /(["']?\b(?:authorization|password|api[ -]key|(?:[a-z0-9]+[_-])*(?:api[_-]?key|authorization|password|secret|token)(?:[_-][a-z0-9]+)*)\b["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:bearer\s+)?[^,;}]+)/gi;
const BEARER_VALUE = /\bbearer\s+(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\S+)/gi;

export function display_text(value: unknown): string {
  return value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value);
}
export function error_text(error: unknown): string {
  return error instanceof Error ? error.message : display_text(error);
}
export function normalized_failure_summary(
  detail: unknown,
  http_status: number | null = null,
): string {
  if (http_status === 401) return 'authentication was rejected (HTTP 401)';
  let text =
    detail === null || detail === undefined
      ? ''
      : display_text(detail).trim().split(/\s+/u).join(' ');
  text = text.replace(SECRET_ASSIGNMENT, '$1[redacted]').replace(BEARER_VALUE, 'Bearer [redacted]');
  if (!text)
    return http_status === null
      ? 'the turn failed for an unknown reason'
      : `the provider request failed (HTTP ${http_status})`;
  if (http_status !== null && !text.includes(`HTTP ${http_status}`))
    text += ` (HTTP ${http_status})`;
  const points = Array.from(text);
  return points.length > 240 ? points.slice(0, 239).join('').trimEnd() + '…' : text;
}
/** The longest backend failure text one log line keeps. */
const LOGGED_FAILURE_TEXT_MAXIMUM_CHARACTERS = 4096;
function class_name(value: unknown): string {
  return value instanceof Error ? value.constructor.name : typeof value;
}
/** One log line for one backend failure, with the backend's own text redacted. */
export function log_backend_failure(
  conversation_id: string,
  backend_key: string,
  error: unknown,
  text: string = error_text(error),
): void {
  const cause =
    error instanceof Error && error.cause !== undefined
      ? ` cause_class=${class_name(error.cause)}`
      : '';
  const logged = capped_failure_text(
    redacted_failure_text(text),
    LOGGED_FAILURE_TEXT_MAXIMUM_CHARACTERS,
  );
  console.error(
    `conversation backend failed conversation_id=${conversation_id} backend=${backend_key} ` +
      `error_class=${class_name(error)}${cause} error=${JSON.stringify(logged)}`,
  );
}
