/** The one way every command reads its text or JSON input. */
import { readFile } from 'node:fs/promises';
import { TextDecoder } from 'node:util';
import { JsonNumber, parse_numeric_json } from '../core/http/numeric_json.ts';
import type { Invocation } from './contracts.ts';
import { error_reason, fail_validation } from './http.ts';

const JSON_INPUT_LIMIT_BYTES = 2 * 1024 * 1024;

async function read_bytes(inv: Invocation, spec: string): Promise<Buffer> {
  if (spec === '-') return inv.stdin();
  try {
    return await readFile(spec);
  } catch (error) {
    return fail_validation(inv, `cannot read ${spec} (${error_reason(error)})`, { cause: error });
  }
}
function decode(inv: Invocation, raw: Buffer, source: string): string {
  try {
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
  } catch (error) {
    return fail_validation(inv, `${source} is not UTF-8 text`, { cause: error });
  }
}
const source_name = (spec: string) => (spec === '-' ? 'stdin' : spec);

export interface TextInput {
  /** The inline flag: `--message`, or `--value` on the field-setting commands. */
  inline: 'message' | 'value';
  /** What the text is, for error messages. */
  label: string;
  /** Keep CR and CRLF line endings exactly, for file contents. */
  exact?: boolean;
  /** `--clear` is a fourth source and returns null. */
  clearable?: boolean;
  /** No source given and stdin is a terminal or empty: return null instead of failing. */
  optional?: boolean;
}
/**
 * Text comes from the inline flag, from `--body-file FILE` (`-` is stdin), or from stdin
 * when no flag is given. Stdin is not read when a flag is given, so a caller whose stdin
 * stays open never waits.
 */
export async function read_text(inv: Invocation, input: TextInput): Promise<string | null> {
  const v = inv.values;
  const inline = `--${input.inline}`;
  const given = [
    v[input.inline] != null && inline,
    v.body_file != null && '--body-file',
    input.clearable && v.clear === true && '--clear',
  ].filter((flag): flag is string => typeof flag === 'string');
  const ways = `${inline} TEXT, --body-file FILE, or stdin`;
  if (given.length > 1)
    fail_validation(
      inv,
      `pass the ${input.label} one way only: ${ways}${input.clearable ? ', or --clear' : ''}`,
    );
  if (given[0] === '--clear') return null;
  let text: string;
  if (given[0] === inline) text = v[input.inline];
  else {
    const spec = v.body_file ?? '-';
    if (spec === '-' && inv.stdin_is_terminal) {
      if (input.optional) return null;
      fail_validation(inv, `supply the ${input.label} with ${ways}`);
    }
    text = decode(inv, await read_bytes(inv, spec), source_name(spec));
    if (input.optional && given.length === 0 && text === '') return null;
  }
  if (!input.exact) text = text.replace(/\r\n?/g, '\n');
  if (!text.trim()) fail_validation(inv, `the ${input.label} is empty`);
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
/** One JSON object from `--input-json FILE` (`-` is stdin), or from stdin when the flag is absent. */
export async function read_json_input(
  inv: Invocation,
  label: string,
): Promise<Record<string, any>> {
  const spec: string = inv.values.input_json ?? '-';
  if (spec === '-' && inv.stdin_is_terminal)
    fail_validation(inv, `supply the ${label} with --input-json FILE, --input-json -, or stdin`);
  const raw = await read_bytes(inv, spec);
  if (raw.length > JSON_INPUT_LIMIT_BYTES) fail_validation(inv, `the ${label} exceeds two MiB`);
  const text = decode(inv, raw, source_name(spec));
  let value: unknown;
  try {
    value = parse_numeric_json(text);
  } catch (error) {
    fail_validation(inv, `the ${label} is not valid JSON (${error_reason(error)})`, {
      cause: error,
    });
  }
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    value instanceof JsonNumber
  )
    fail_validation(inv, `the ${label} must be one JSON object`);
  if (contains_nonfinite_float(value))
    fail_validation(inv, 'non-finite JSON values are not allowed');
  return value as Record<string, any>;
}
/** A file the caller names on a flag other than the input flags, such as `--edit-file`. */
export async function read_named_file(inv: Invocation, spec: string): Promise<string> {
  return decode(inv, await read_bytes(inv, spec), source_name(spec)).replace(/\r\n?/g, '\n');
}
