import { readFile } from 'node:fs/promises';
import { TextDecoder } from 'node:util';
import { JsonNumber, parse_numeric_json } from '../core/http/numeric_json.ts';
import type { Invocation } from './contracts.ts';
import { fail_validation } from './http.ts';
import { json_diagnostic } from './json_diagnostics.ts';

export async function read_source(
  inv: Invocation,
  spec: string,
  artifact = false,
): Promise<string> {
  try {
    const raw = spec === '-' ? await inv.stdin() : await readFile(spec);
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
    return artifact ? text : text.replace(/\r\n?/g, '\n');
  } catch {
    fail_validation(
      inv,
      artifact ? `cannot read UTF-8 body file: ${spec}` : `cannot read body file: ${spec}`,
    );
  }
}
export async function worker_stdin_body(inv: Invocation, label = 'body'): Promise<string> {
  const text = (await inv.stdin()).toString('utf8').replace(/\r\n?/g, '\n');
  if (!text.trim()) fail_validation(inv, `empty ${label}`);
  return text;
}
function contains_nonfinite_float(value: unknown): boolean {
  if (value instanceof JsonNumber) return value.kind === 'float' && !Number.isFinite(value.value);
  if (typeof value === 'number') return !Number.isFinite(value);
  if (Array.isArray(value)) return value.some(contains_nonfinite_float);
  return value !== null && typeof value === 'object'
    ? Object.values(value).some(contains_nonfinite_float)
    : false;
}
export async function read_json_object(
  inv: Invocation,
  spec: string,
  label: string,
): Promise<Record<string, any>> {
  const text = await read_source(inv, spec);
  const diagnostic = json_diagnostic(text);
  if (diagnostic) fail_validation(inv, `invalid ${label} JSON: ${diagnostic.msg}`);
  if (!/^[ \t\r\n]*\{/.test(text)) fail_validation(inv, `${label} must be one JSON object`);
  let value: unknown;
  try {
    value = parse_numeric_json(text);
  } catch {
    fail_validation(inv, 'non-finite JSON values are not allowed');
  }
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    value instanceof JsonNumber
  )
    fail_validation(inv, `${label} must be one JSON object`);
  if (contains_nonfinite_float(value))
    fail_validation(inv, 'non-finite JSON values are not allowed');
  return value as Record<string, any>;
}
export async function stdin_object(inv: Invocation): Promise<Record<string, any>> {
  const raw = await inv.stdin();
  if (raw.length > 2 * 1024 * 1024) fail_validation(inv, 'JSON stdin exceeds two MiB.');
  let value: unknown;
  try {
    value = parse_numeric_json(new TextDecoder('utf8', { fatal: true }).decode(raw));
  } catch {
    fail_validation(inv, 'Provide a JSON object on stdin.');
  }
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    value instanceof JsonNumber
  )
    fail_validation(inv, 'Provide a JSON object on stdin.');
  if (contains_nonfinite_float(value)) fail_validation(inv, 'Provide a JSON object on stdin.');
  return value as Record<string, any>;
}
