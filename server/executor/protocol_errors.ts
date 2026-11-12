/** Executor validation diagnostics. Representation tags never become wire properties. */
import { JsonNumber, stringify_python_json } from '../core/http/numeric_json.ts';
import { python_string_repr } from '../core/python_repr.ts';
import { ordered_json_entries } from './protocol_json.ts';
export interface Issue {
  type: string;
  loc: readonly (string | number)[];
  message: string;
  input: unknown;
  url?: boolean;
}
export interface FieldRepresentation {
  name: string;
  enumeration?: { name: string; members: readonly (readonly [string, string])[] };
  path?: boolean;
}
const classes = new WeakMap<object, { name: string; fields: readonly FieldRepresentation[] }>();
const tuples = new WeakSet<object>();
export function class_name(value: unknown): string | undefined {
  return value !== null && typeof value === 'object' ? classes.get(value)?.name : undefined;
}
export function tag_class(
  value: object,
  name: string,
  fields: readonly FieldRepresentation[],
): void {
  classes.set(value, { name, fields });
}
export function tag_tuple(value: object): void {
  tuples.add(value);
}
export function tuple_value(value: unknown): boolean {
  return value !== null && typeof value === 'object' && tuples.has(value);
}
export function input_type(value: unknown): string {
  const known = class_name(value);
  if (known) return known;
  if (value instanceof Uint8Array) return 'bytes';
  if (value instanceof JsonNumber) return value.kind === 'integer' ? 'int' : 'float';
  if (Array.isArray(value)) return tuple_value(value) ? 'tuple' : 'list';
  if (value === null) return 'NoneType';
  return (
    (
      {
        string: 'str',
        boolean: 'bool',
        number: Number.isInteger(value) && !Object.is(value, -0) ? 'int' : 'float',
        object: 'dict',
      } as Record<string, string>
    )[typeof value] ?? typeof value
  );
}
export function python_repr(value: unknown): string {
  if (value === null || value === undefined) return 'None';
  if (typeof value === 'string') return python_string_repr(value);
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  if (value instanceof JsonNumber) return stringify_python_json(value);
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return Number.isNaN(value) ? 'nan' : value < 0 ? '-inf' : 'inf';
    return Object.is(value, -0) ? '-0.0' : String(value);
  }
  if (value instanceof Uint8Array) {
    const quote = value.includes(39) && !value.includes(34) ? '"' : "'";
    let result = 'b' + quote;
    for (const byte of value) {
      const char = String.fromCharCode(byte);
      result +=
        char === quote || byte === 92
          ? '\\' + char
          : byte === 10
            ? '\\n'
            : byte === 13
              ? '\\r'
              : byte === 9
                ? '\\t'
                : byte < 32 || byte >= 127
                  ? '\\x' + byte.toString(16).padStart(2, '0')
                  : char;
    }
    return result + quote;
  }
  if (Array.isArray(value)) {
    const items = value.map(python_repr).join(', ');
    return tuple_value(value)
      ? '(' + items + (value.length === 1 ? ',' : '') + ')'
      : '[' + items + ']';
  }
  if (typeof value === 'object') {
    const known = classes.get(value!);
    const record = value as Record<string, unknown>;
    if (known)
      return (
        known.name +
        '(' +
        known.fields
          .map((field) => {
            const item = record[field.name];
            const member = field.enumeration?.members.find(([, wire]) => wire === item);
            const rendered = member
              ? `<${field.enumeration!.name}.${member[0]}: ${python_repr(item)}>`
              : field.path && typeof item === 'string'
                ? `PosixPath(${python_repr(item)})`
                : python_repr(item);
            return field.name + '=' + rendered;
          })
          .join(', ') +
        ')'
      );
    return (
      '{' +
      ordered_json_entries(record)
        .map(([key, item]) => python_repr(key) + ': ' + python_repr(item))
        .join(', ') +
      '}'
    );
  }
  return String(value);
}
function abbreviated(value: unknown): string {
  const text = Buffer.from(python_repr(value));
  if (text.length <= 50) return text.toString();
  let first = 25,
    last = text.length - 24;
  while ((text[first]! & 0xc0) === 0x80) first--;
  while ((text[last]! & 0xc0) === 0x80) last++;
  return text.subarray(0, first).toString() + '...' + text.subarray(last).toString();
}
export class ValidationError extends Error {
  constructor(root: string, issues: readonly Issue[], version: string) {
    super(
      `${issues.length} validation error${issues.length === 1 ? '' : 's'} for ${root}\n` +
        issues
          .map(
            (issue) =>
              (issue.loc.length ? issue.loc.join('.') + '\n' : '') +
              `  ${issue.message} [type=${issue.type}, input_value=${abbreviated(issue.input)}, input_type=${input_type(issue.input)}]` +
              (issue.url === false
                ? ''
                : `\n    For further information visit https://errors.pydantic.dev/${version}/v/${issue.type}`),
          )
          .join('\n'),
    );
    this.name = 'ValidationError';
  }
}
export class KeyError extends Error {
  constructor(key: unknown) {
    super(python_repr(key));
    this.name = 'KeyError';
  }
}
export class PydanticSerializationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PydanticSerializationError';
  }
}

export class ValueError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ValueError';
  }
}
