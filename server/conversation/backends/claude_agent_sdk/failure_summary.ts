import { python_string_repr } from '../../../core/python_repr.ts';
import { JsonNumber, python_float_repr } from '../../../core/http/numeric_json.ts';
import { ordered_json_entries } from '../../../executor/protocol_json.ts';
const SECRET_ASSIGNMENT =
  /(["']?\b(?:authorization|password|api[ -]key|(?:[a-z0-9]+[_-])*(?:api[_-]?key|authorization|password|secret|token)(?:[_-][a-z0-9]+)*)\b["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:bearer\s+)?[^,;}]+)/gi;
const BEARER_VALUE = /\bbearer\s+(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\S+)/gi;

export function python_repr(value: unknown): string {
  if (value instanceof JsonNumber)
    return value.kind === 'integer' ? String(value.integer) : python_float_repr(value.value);
  if (typeof value === 'number')
    return Number.isSafeInteger(value) && !Object.is(value, -0)
      ? String(value)
      : python_float_repr(value);
  if (value === null || value === undefined) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (typeof value === 'string') return python_string_repr(value);
  if (Array.isArray(value)) return '[' + value.map(python_repr).join(', ') + ']';
  if (typeof value === 'object') {
    return (
      '{' +
      ordered_json_entries(value as Record<string, unknown>)
        .map(([key, item]) => python_repr(key) + ': ' + python_repr(item))
        .join(', ') +
      '}'
    );
  }
  return String(value);
}
export function python_str(value: unknown): string {
  return typeof value === 'string' ? value : python_repr(value);
}
export function error_text(error: unknown): string {
  return error instanceof Error ? error.message : python_str(error);
}
export function normalized_failure_summary(
  detail: unknown,
  http_status: number | null = null,
): string {
  if (http_status === 401) return 'authentication was rejected (HTTP 401)';
  let text =
    detail === null || detail === undefined
      ? ''
      : python_str(detail).trim().split(/\s+/u).join(' ');
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
    return normalized_failure_summary(message.errors.map(python_str).join('; '), status);
  return normalized_failure_summary(message.result || message.subtype, status);
}
