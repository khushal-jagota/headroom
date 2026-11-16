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
export function result_error_summary(message: Record<string, unknown>): string {
  const status = typeof message.api_error_status === 'number' ? message.api_error_status : null;
  if (status === 401) return normalized_failure_summary(null, status);
  if (Array.isArray(message.errors) && message.errors.length)
    return normalized_failure_summary(message.errors.map(display_text).join('; '), status);
  return normalized_failure_summary(message.result || message.subtype, status);
}
