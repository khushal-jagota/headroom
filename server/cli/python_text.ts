/** Native Python str/f-string scalar text; JSON document rendering is separate. */
import { JsonNumber, stringify_python_json } from '../core/http/numeric_json.ts';
import { python_string_repr } from '../core/python_repr.ts';
import { MissingResponseValue } from './errors.ts';
const repr = (value: unknown): string =>
  typeof value === 'string' ? python_string_repr(value) : python_text(value);
export function python_text(value: unknown): string {
  if (value === null) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  const number = value instanceof JsonNumber && value.kind === 'float' ? value.value : value;
  if (typeof number === 'number') {
    if (Number.isNaN(number)) return 'nan';
    if (number === Infinity) return 'inf';
    if (number === -Infinity) return '-inf';
    if (Object.is(number, -0)) return '-0.0';
  }
  if (value instanceof JsonNumber || typeof value === 'number' || typeof value === 'bigint')
    return stringify_python_json(value);
  if (Array.isArray(value)) return '[' + value.map(repr).join(', ') + ']';
  if (value !== null && typeof value === 'object')
    return (
      '{' +
      Object.entries(value)
        .map(([key, item]) => python_string_repr(key) + ': ' + repr(item))
        .join(', ') +
      '}'
    );
  if (value === undefined) throw new MissingResponseValue();
  return String(value);
}
