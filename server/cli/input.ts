import { readFile } from 'node:fs/promises';
import { TextDecoder } from 'node:util';
import { JsonNumber, parse_numeric_json } from '../core/http/numeric_json.ts';
import type { Invocation } from './contracts.ts';
import { fail_validation } from './http.ts';

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
export async function read_json_object(
  inv: Invocation,
  spec: string,
  label: string,
): Promise<Record<string, any>> {
  const text = await read_source(inv, spec);
  let value: unknown;
  try {
    value = parse_numeric_json(text);
  } catch {
    fail_validation(inv, `invalid ${label} JSON: ${json_error(text)}`);
  }
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    value instanceof JsonNumber
  )
    fail_validation(inv, `${label} must be one JSON object`);
  return value as Record<string, any>;
}
/** JSON decoder failures exposed by Python's input helper. */
function json_error(text: string): string {
  if (text.startsWith('\uFEFF')) return 'Unexpected UTF-8 BOM (decode using utf-8-sig)';
  if (!text.trim()) return 'Expecting value';
  try {
    JSON.parse(text);
  } catch (error) {
    const message = String((error as Error).message);
    if (/property name|property name or/.test(message))
      return 'Expecting property name enclosed in double quotes';
    if (/after JSON|after JSON data/.test(message)) return 'Extra data';
    if (/Expected.*:|colon/.test(message)) return "Expecting ':' delimiter";
    if (/Expected.*comma|Expected.*\}|Expected.*\]|comma or/.test(message))
      return "Expecting ',' delimiter";
    if (/Unterminated string/.test(message)) return 'Unterminated string starting at';
    if (/Bad control/.test(message)) return 'Invalid control character at';
    if (/Bad escaped/.test(message)) return 'Invalid \\escape';
  }
  return 'Expecting value';
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
  return value as Record<string, any>;
}
