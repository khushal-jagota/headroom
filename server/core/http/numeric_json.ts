/** Native JSON intake that retains Python's integer/float distinction for document storage. */
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
    return rawJSON(number_token(this));
  }
}
function python_float(value: number): string {
  if (Number.isNaN(value)) return 'NaN';
  if (!Number.isFinite(value)) return value < 0 ? '-Infinity' : 'Infinity';
  if (Object.is(value, -0)) return '-0.0';
  const magnitude = Math.abs(value);
  let raw =
    magnitude !== 0 && (magnitude < 1e-4 || magnitude >= 1e16)
      ? value.toExponential()
      : String(value);
  if (raw.includes('e'))
    raw = raw.replace(
      /e([+-]?)(\d+)$/,
      (_, sign: string, digits: string) => 'e' + (sign || '+') + digits.padStart(2, '0'),
    );
  else if (!raw.includes('.')) raw += '.0';
  return raw;
}
function number_token(value: JsonNumber): string {
  return value.kind === 'integer' ? String(value.integer) : python_float(value.value);
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
export function stringify_python_json(value: unknown): string {
  if (value instanceof JsonNumber) return number_token(value);
  if (typeof value === 'bigint') return String(value);
  if (typeof value === 'number')
    return Number.isInteger(value) ? String(BigInt(value)) : python_float(value);
  if (Array.isArray(value)) return '[' + value.map(stringify_python_json).join(', ') + ']';
  if (value !== null && typeof value === 'object')
    return (
      '{' +
      Object.entries(value)
        .map(([key, item]) => JSON.stringify(key) + ': ' + stringify_python_json(item))
        .join(', ') +
      '}'
    );
  const result = JSON.stringify(value);
  if (result === undefined) throw new TypeError('Value is not JSON serializable');
  return result;
}
