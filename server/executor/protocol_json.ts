/** Executor JSON lexical boundary: jiter-compatible input, before wire normalization. */
import { JsonNumber } from '../core/http/numeric_json.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
import type { JsonValue } from './wire.generated.ts';
import { EXECUTOR_JSON_MAX_DEPTH, EXECUTOR_JSON_MAX_INTEGER_LENGTH } from './configuration.ts';

export type ExecutorJSONValue = NumericJSON<JsonValue>;
const object_keys = new WeakMap<object, readonly string[]>();

/** Source order survives integer-looking keys. Repeated keys keep their original position. */
export function ordered_json_entries(
  value: Record<string, unknown>,
): readonly (readonly [string, unknown])[] {
  const keys = object_keys.get(value) ?? Object.keys(value);
  return keys.map((key) => [key, value[key]] as const);
}

export class ExecutorJSONSyntaxError extends SyntaxError {
  readonly reason: string;
  /** Bytes inspected, including the offending byte; EOF uses the complete input length. */
  readonly byte_position: number;
  readonly line: number;
  readonly column: number;
  constructor(reason: string, body: Uint8Array, byte_position: number) {
    let line = 1;
    let column = 0;
    for (let index = 0; index < byte_position; index++) {
      if (body[index] === 10) {
        line++;
        column = 0;
      } else column++;
    }
    super(`${reason} at line ${line} column ${column}`);
    this.reason = reason;
    this.byte_position = byte_position;
    this.line = line;
    this.column = column;
  }
}

/** Finite numeric leaves retain integer precision and token kind; nonfinite floats are native NaN/Infinity. */
export function parse_executor_json(body: Uint8Array): ExecutorJSONValue {
  const bytes = Buffer.from(body.buffer, body.byteOffset, body.byteLength);
  let position = 0;
  const fail = (reason: string, at = Math.min(position + 1, bytes.length)): never => {
    throw new ExecutorJSONSyntaxError(reason, bytes, at);
  };
  const whitespace = (): void => {
    while ([32, 9, 10, 13].includes(bytes[position]!)) position++;
  };
  const digit = (byte: number | undefined): boolean =>
    byte !== undefined && byte >= 48 && byte <= 57;
  const literal = (text: string, value: ExecutorJSONValue): ExecutorJSONValue => {
    for (const expected of Buffer.from(text)) {
      if (position === bytes.length) fail('EOF while parsing a value');
      if (bytes[position] !== expected) fail('expected ident');
      position++;
    }
    return value;
  };
  const number = (): ExecutorJSONValue => {
    const start = position;
    if (bytes[position] === 45) {
      position++;
      if (bytes[position] === 73) return literal('Infinity', -Infinity);
    }
    if (position === bytes.length) fail('EOF while parsing a value');
    if (!digit(bytes[position])) fail('invalid number');
    if (bytes[position] === 48) {
      position++;
      if (digit(bytes[position])) fail('invalid number');
    } else {
      while (digit(bytes[position])) {
        position++;
        if (position - start > EXECUTOR_JSON_MAX_INTEGER_LENGTH) fail('number out of range');
      }
    }
    for (const marker of [46, 101]) {
      const byte = bytes[position];
      if (marker === 46 ? byte !== 46 : byte !== 101 && byte !== 69) continue;
      position++;
      if (marker === 101 && (bytes[position] === 43 || bytes[position] === 45)) position++;
      if (position === bytes.length) fail('EOF while parsing a value');
      if (!digit(bytes[position])) fail('invalid number');
      while (digit(bytes[position])) position++;
    }
    const result = new JsonNumber(bytes.toString('ascii', start, position));
    return result.kind === 'integer' || Number.isFinite(result.value) ? result : result.value;
  };
  const string = (): string => {
    position++;
    const pieces: string[] = [];
    let invalid_utf8: number | undefined;
    const raw = (start: number, end: number): string => {
      let result = '';
      for (let index = start; index < end;) {
        const lead = bytes[index]!;
        if (lead < 128) {
          result += String.fromCharCode(lead);
          index++;
          continue;
        }
        const length =
          lead >= 0xc2 && lead <= 0xdf
            ? 2
            : lead >= 0xe0 && lead <= 0xef
              ? 3
              : lead >= 0xf0 && lead <= 0xf4
                ? 4
                : 0;
        let code = lead & (length === 2 ? 31 : length === 3 ? 15 : 7);
        let valid = length !== 0 && index + length <= end;
        for (let offset = 1; valid && offset < length; offset++) {
          const next = bytes[index + offset]!;
          valid = next >= 0x80 && next <= 0xbf;
          code = (code << 6) | (next & 63);
        }
        valid &&=
          code >= (length === 2 ? 0x80 : length === 3 ? 0x800 : 0x10000) &&
          code <= 0x10ffff &&
          !(code >= 0xd800 && code <= 0xdfff);
        if (!valid) {
          invalid_utf8 ??= index + 2;
          return result;
        }
        result += String.fromCodePoint(code);
        index += length;
      }
      return result;
    };
    const hex = (): number => {
      if (bytes.length - position < 4) fail('EOF while parsing a string', bytes.length);
      let value = 0;
      for (let index = 0; index < 4; index++) {
        const byte = bytes[position]!;
        const digit =
          byte >= 48 && byte <= 57
            ? byte - 48
            : byte >= 65 && byte <= 70
              ? byte - 55
              : byte >= 97 && byte <= 102
                ? byte - 87
                : -1;
        if (digit < 0) fail('invalid escape');
        value = (value << 4) | digit;
        position++;
      }
      return value;
    };
    while (true) {
      const start = position;
      while (
        position < bytes.length &&
        bytes[position]! >= 32 &&
        bytes[position] !== 34 &&
        bytes[position] !== 92
      )
        position++;
      if (position === bytes.length) fail('EOF while parsing a string');
      const byte = bytes[position]!;
      if (byte < 32) fail('control character (\\u0000-\\u001F) found while parsing a string');
      pieces.push(raw(start, position));
      position++;
      if (byte === 34) {
        if (invalid_utf8 !== undefined) fail('invalid unicode code point', invalid_utf8);
        return pieces.join('');
      }
      if (position === bytes.length) fail('EOF while parsing a string');
      const escaped = bytes[position]!;
      const simple: Record<number, string> = {
        34: '"',
        92: '\\',
        47: '/',
        98: '\b',
        102: '\f',
        110: '\n',
        114: '\r',
        116: '\t',
      };
      if (Object.hasOwn(simple, escaped)) {
        pieces.push(simple[escaped]!);
        position++;
      } else if (escaped === 117) {
        position++;
        let point = hex();
        if (point >= 0xdc00 && point <= 0xdfff)
          fail('lone leading surrogate in hex escape', position);
        if (point >= 0xd800 && point <= 0xdbff) {
          for (const expected of [92, 117]) {
            if (position === bytes.length) fail('EOF while parsing a string');
            if (bytes[position] !== expected) fail('unexpected end of hex escape');
            position++;
          }
          const low = hex();
          if (low < 0xdc00 || low > 0xdfff) fail('lone leading surrogate in hex escape', position);
          point = 0x10000 + ((point - 0xd800) << 10) + low - 0xdc00;
        }
        pieces.push(String.fromCodePoint(point));
      } else fail('invalid escape');
    }
  };
  const value = (depth: number): ExecutorJSONValue => {
    whitespace();
    if (position === bytes.length) fail('EOF while parsing a value');
    switch (bytes[position]) {
      case 110:
        return literal('null', null);
      case 116:
        return literal('true', true);
      case 102:
        return literal('false', false);
      case 78:
        return literal('NaN', NaN);
      case 73:
        return literal('Infinity', Infinity);
      case 34:
        return string();
      case 91: {
        position++;
        whitespace();
        const result: ExecutorJSONValue[] = [];
        if (bytes[position] === 93) {
          position++;
          return result;
        }
        if (position === bytes.length) fail('EOF while parsing a list');
        while (true) {
          if (depth >= EXECUTOR_JSON_MAX_DEPTH) fail('recursion limit exceeded');
          result.push(value(depth + 1));
          whitespace();
          if (position === bytes.length) fail('EOF while parsing a list');
          if (bytes[position] === 93) {
            position++;
            return result;
          }
          if (bytes[position] !== 44) fail('expected `,` or `]`');
          position++;
          whitespace();
          if (bytes[position] === 93) fail('trailing comma');
        }
      }
      case 123: {
        position++;
        whitespace();
        const result: Record<string, ExecutorJSONValue> = Object.create(null);
        const keys: string[] = [];
        object_keys.set(result, keys);
        if (bytes[position] === 125) {
          position++;
          return result;
        }
        if (position === bytes.length) fail('EOF while parsing an object');
        while (true) {
          if (bytes[position] !== 34) fail('key must be a string');
          const key = string();
          whitespace();
          if (position === bytes.length) fail('EOF while parsing an object');
          if (bytes[position] !== 58) fail('expected `:`');
          position++;
          whitespace();
          if (depth >= EXECUTOR_JSON_MAX_DEPTH) fail('recursion limit exceeded');
          const item = value(depth + 1);
          if (!Object.hasOwn(result, key)) keys.push(key);
          result[key] = item;
          whitespace();
          if (position === bytes.length) fail('EOF while parsing an object');
          if (bytes[position] === 125) {
            position++;
            return result;
          }
          if (bytes[position] !== 44) fail('expected `,` or `}`');
          position++;
          whitespace();
          if (position === bytes.length) fail('EOF while parsing a value');
          if (bytes[position] === 125) fail('trailing comma');
        }
      }
      default:
        if (bytes[position] === 45 || digit(bytes[position])) return number();
        return fail('expected value');
    }
  };
  const result = value(0);
  whitespace();
  if (position !== bytes.length) fail('trailing characters');
  return result;
}
