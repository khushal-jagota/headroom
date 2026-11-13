/** Python truth for JSON values, including exact numeric markers. */
import { numeric_value } from './http/numeric_json.ts';

export function python_truth(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === '') return false;
  const number = numeric_value(value);
  if (number !== undefined) return number !== 0;
  if (Array.isArray(value)) return value.length !== 0;
  if (typeof value === 'object') return Object.keys(value).length !== 0;
  return Boolean(value);
}
