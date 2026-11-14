/** JSON intake retains exact integers and numeric kinds for validation and storage. */
import { is_record } from '../records.ts';

/** Exact-number markers represent JSON numbers, not dictionary values. */
export function is_json_record(value: unknown): value is Record<string, unknown> {
  return is_record(value) && !(value instanceof JsonNumber);
}

export type JsonNumberKind = 'integer' | 'float';
/** The server's document form retains numeric kinds until an explicit validation projection. */
export type NumericJSON<T> = T extends number
  ? T | JsonNumber
  : T extends readonly (infer Item)[]
    ? NumericJSON<Item>[]
    : T extends object
      ? { [Key in keyof T]: NumericJSON<T[Key]> }
      : T;
interface PrimitiveContext {
  source?: string;
}
type NativeParse = (
  text: string,
  reviver: (key: string, value: unknown, context: PrimitiveContext) => unknown,
) => unknown;
const rawJSON = (JSON as unknown as { rawJSON(text: string): unknown }).rawJSON;

/** Explicit numeric metadata survives document validation and emits a JSON number on the wire. */
export class JsonNumber {
  readonly kind: JsonNumberKind;
  readonly integer: bigint | null;
  readonly value: number;
  constructor(source: string) {
    this.kind = /[.eE]/.test(source) ? 'float' : 'integer';
    this.integer = this.kind === 'integer' ? BigInt(source) : null;
    this.value = Number(source);
    Object.freeze(this);
  }
  toJSON(): unknown {
    if (this.integer !== null) return rawJSON(String(this.integer));
    if (!Number.isFinite(this.value)) throw new RangeError('JSON numbers must be finite');
    return this.value;
  }
}
/** Native JSON preserves exact markers without claiming unsafe floats as exact integers. */
export function stringify_numeric_json(payload: unknown): string {
  return JSON.stringify(payload, (_key, value: unknown) =>
    typeof value === 'number' && Number.isInteger(value) && !Number.isSafeInteger(value)
      ? rawJSON(value.toExponential())
      : value,
  );
}
function python_float(value: number, pydantic = false): string {
  if (pydantic && !Number.isFinite(value))
    throw Object.assign(new Error('Out of range float values are not JSON compliant'), {
      name: 'ValueError',
    });
  if (Number.isNaN(value)) return 'NaN';
  if (!Number.isFinite(value)) return value < 0 ? '-Infinity' : 'Infinity';
  if (Object.is(value, -0)) return '-0.0';
  const magnitude = Math.abs(value);
  let raw =
    magnitude !== 0 && (magnitude < (pydantic ? 1e-5 : 1e-4) || magnitude >= 1e16)
      ? value.toExponential()
      : String(value);
  if (raw.includes('e')) {
    if (!pydantic)
      raw = raw.replace(
        /e([+-]?)(\d+)$/,
        (_, sign: string, digits: string) => 'e' + (sign || '+') + digits.padStart(2, '0'),
      );
  } else if (!raw.includes('.')) raw += '.0';
  return raw;
}
function number_token(value: JsonNumber, pydantic = false): string {
  return value.kind === 'integer' ? String(value.integer) : python_float(value.value, pydantic);
}
export function parse_numeric_json(text: string): unknown {
  return (JSON.parse as NativeParse)(text, (_key, value, context) => {
    if (typeof value !== 'number') return value;
    if (context.source === undefined)
      throw new Error('Native JSON primitive source support is required');
    return new JsonNumber(context.source);
  });
}
/** Validation uses this projection, never replaces the original numeric marker. */
export function numeric_value(value: unknown): number | undefined {
  return value instanceof JsonNumber ? value.value : typeof value === 'number' ? value : undefined;
}
export function numeric_kind(value: unknown): JsonNumberKind | undefined {
  return value instanceof JsonNumber
    ? value.kind
    : typeof value === 'number'
      ? Number.isInteger(value)
        ? 'integer'
        : 'float'
      : undefined;
}
export function clone_numeric_json(value: unknown): unknown {
  if (value instanceof JsonNumber || value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(clone_numeric_json);
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, clone_numeric_json(item)]),
  );
}
/** Python json.dumps(value, ensure_ascii=False), with its ordinary spaced separators. */
type PythonJSONOptions = {
  sort_keys?: boolean;
  compact?: boolean;
  ensure_ascii?: boolean;
  indent?: 2;
};
export function stringify_python_json(value: unknown, options: PythonJSONOptions = {}): string {
  return stringify_json(value, options, 0);
}
/** Compact Pydantic JSON for finite numeric documents, without wire-shape validation. */
export function stringify_pydantic_json(value: unknown): string {
  return stringify_json(value, { compact: true }, 0, true);
}
function stringify_json(
  value: unknown,
  options: PythonJSONOptions,
  depth: number,
  pydantic = false,
): string {
  const separator = options.compact ? ',' : ', ';
  const text = (item: string): string => {
    if (pydantic)
      for (let index = 0; index < item.length; index++) {
        const code = item.charCodeAt(index);
        if (code >= 0xd800 && code <= 0xdbff) {
          const next = item.charCodeAt(++index);
          if (next >= 0xdc00 && next <= 0xdfff) continue;
        } else if (code < 0xdc00 || code > 0xdfff) continue;
        throw Object.assign(new Error('invalid Unicode surrogate in JSON string'), {
          name: 'PydanticSerializationError',
        });
      }
    const encoded = JSON.stringify(item);
    return options.ensure_ascii
      ? encoded.replace(
          /[\u007f-\uffff]/g,
          (character) => '\\u' + character.charCodeAt(0).toString(16).padStart(4, '0'),
        )
      : encoded;
  };
  if (value instanceof JsonNumber) return number_token(value, pydantic);
  if (typeof value === 'bigint') return String(value);
  if (typeof value === 'number')
    return Number.isInteger(value) && !(pydantic && Object.is(value, -0))
      ? String(BigInt(value))
      : python_float(value, pydantic);
  const container = (open: string, close: string, parts: string[]): string => {
    if (options.indent === undefined || parts.length === 0)
      return open + parts.join(separator) + close;
    const child = ' '.repeat(options.indent * (depth + 1));
    return (
      open +
      '\n' +
      child +
      parts.join(',\n' + child) +
      '\n' +
      ' '.repeat(options.indent * depth) +
      close
    );
  };
  if (Array.isArray(value))
    return container(
      '[',
      ']',
      value.map((item) => stringify_json(item, options, depth + 1, pydantic)),
    );
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value);
    if (options.sort_keys)
      entries.sort(([a], [b]) => {
        const left = Array.from(a, (character) => character.codePointAt(0)!);
        const right = Array.from(b, (character) => character.codePointAt(0)!);
        for (let index = 0; index < Math.min(left.length, right.length); index++) {
          const difference = left[index]! - right[index]!;
          if (difference) return difference;
        }
        return left.length - right.length;
      });
    return container(
      '{',
      '}',
      entries.map(
        ([key, item]) =>
          text(key) +
          (options.compact ? ':' : ': ') +
          stringify_json(item, options, depth + 1, pydantic),
      ),
    );
  }
  const result = typeof value === 'string' ? text(value) : JSON.stringify(value);
  if (result === undefined) throw new TypeError('Value is not JSON serializable');
  return result;
}
