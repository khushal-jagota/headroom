/** CPython 3.12 JSON syntax diagnostics, independent of V8 error wording.
 * Grammar/error locations follow Modules/_json.c and Lib/json/decoder.py:
 * https://github.com/python/cpython/blob/3.12/Modules/_json.c
 * https://github.com/python/cpython/blob/3.12/Lib/json/decoder.py
 * Values remain the responsibility of the shared numeric JSON codec.
 */
export interface JsonDiagnostic {
  msg: string;
  pos: number;
  lineno: number;
  colno: number;
}
class ScanFailure {
  readonly diagnostic: JsonDiagnostic;
  constructor(diagnostic: JsonDiagnostic) {
    this.diagnostic = diagnostic;
  }
}
export function json_diagnostic(source: string): JsonDiagnostic | null {
  let offset = 0;
  const fail = (msg: string, at = offset): never => {
    const prefix = source.slice(0, at);
    const pos = Array.from(prefix).length;
    const newline = prefix.lastIndexOf('\n');
    throw new ScanFailure({
      msg,
      pos,
      lineno: prefix.split('\n').length,
      colno: Array.from(prefix.slice(newline + 1)).length + 1,
    });
  };
  const whitespace = () => {
    while (offset < source.length && /[ \t\r\n]/.test(source[offset])) offset++;
  };
  const string = () => {
    const begin = offset++;
    while (offset < source.length) {
      const character = source[offset];
      if (character === '"') {
        offset++;
        return;
      }
      if (character.charCodeAt(0) <= 0x1f) fail('Invalid control character at');
      if (character !== '\\') {
        offset++;
        continue;
      }
      const escape = offset++;
      if (offset === source.length) fail('Unterminated string starting at', begin);
      if (source[offset] === 'u') {
        const unicode = offset;
        if (
          unicode + 5 >= source.length ||
          !/^[0-9a-fA-F]{4}$/.test(source.slice(unicode + 1, unicode + 5))
        )
          fail('Invalid \\uXXXX escape', unicode);
        offset += 5;
      } else {
        if (!'"\\/bfnrt'.includes(source[offset])) fail('Invalid \\escape', escape);
        offset++;
      }
    }
    fail('Unterminated string starting at', begin);
  };
  const value = (): void => {
    const character = source[offset];
    if (character === '"') {
      string();
      return;
    }
    if (character === '{') {
      offset++;
      whitespace();
      if (source[offset] === '}') {
        offset++;
        return;
      }
      for (;;) {
        if (source[offset] !== '"') fail('Expecting property name enclosed in double quotes');
        string();
        whitespace();
        if (source[offset] !== ':') fail("Expecting ':' delimiter");
        offset++;
        whitespace();
        value();
        whitespace();
        if (source[offset] === '}') {
          offset++;
          return;
        }
        if (source[offset] !== ',') fail("Expecting ',' delimiter");
        offset++;
        whitespace();
      }
    }
    if (character === '[') {
      offset++;
      whitespace();
      if (source[offset] === ']') {
        offset++;
        return;
      }
      for (;;) {
        value();
        whitespace();
        if (source[offset] === ']') {
          offset++;
          return;
        }
        if (source[offset] !== ',') fail("Expecting ',' delimiter");
        offset++;
        whitespace();
      }
    }
    for (const literal of ['null', 'true', 'false', 'NaN', 'Infinity', '-Infinity']) {
      if (source.startsWith(literal, offset)) {
        offset += literal.length;
        return;
      }
    }
    const number = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(
      source.slice(offset),
    );
    if (number) {
      offset += number[0].length;
      return;
    }
    fail('Expecting value');
  };
  try {
    if (source.startsWith('\uFEFF')) fail('Unexpected UTF-8 BOM (decode using utf-8-sig)', 0);
    whitespace();
    value();
    whitespace();
    if (offset !== source.length) fail('Extra data');
    return null;
  } catch (error) {
    if (error instanceof ScanFailure) return error.diagnostic;
    throw error;
  }
}
