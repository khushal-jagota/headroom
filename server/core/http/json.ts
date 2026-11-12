/** Python json.JSONDecodeError position and message at the shared JSON boundary. */
export interface JsonFailure {
  position: number;
  message: string;
}
export function jsonFailure(source: string): JsonFailure {
  let i = 0;
  const fail = (message: string, position = i): never => {
    throw { position: Array.from(source.slice(0, position)).length, message };
  };
  const whitespace = (): void => {
    while (/[ \t\r\n]/.test(source[i] ?? '\0')) i++;
  };
  const string = (): void => {
    const start = i++;
    while (i < source.length) {
      const c = source[i++]!;
      if (c === '"') return;
      if (c.charCodeAt(0) < 32) fail('Invalid control character at', i - 1);
      if (c === '\\') {
        if (i >= source.length) fail('Unterminated string starting at', start);
        const escape = source[i++]!;
        if (escape === 'u') {
          if (!/^[0-9a-fA-F]{4}$/.test(source.slice(i, i + 4)))
            fail('Invalid \\uXXXX escape', i - 1);
          i += 4;
        } else if (!'"\\/bfnrt'.includes(escape)) fail('Invalid \\escape', i - 2);
      }
    }
    fail('Unterminated string starting at', start);
  };
  const value = (): void => {
    whitespace();
    const c = source[i];
    if (c === '"') {
      string();
      return;
    }
    if (c === '{') {
      i++;
      whitespace();
      if (source[i] === '}') {
        i++;
        return;
      }
      while (true) {
        if (source[i] !== '"') fail('Expecting property name enclosed in double quotes');
        string();
        whitespace();
        if (source[i] !== ':') fail("Expecting ':' delimiter");
        i++;
        value();
        whitespace();
        if (source[i] === '}') {
          i++;
          return;
        }
        if (source[i] !== ',') fail("Expecting ',' delimiter");
        i++;
        whitespace();
      }
    }
    if (c === '[') {
      i++;
      whitespace();
      if (source[i] === ']') {
        i++;
        return;
      }
      while (true) {
        value();
        whitespace();
        if (source[i] === ']') {
          i++;
          return;
        }
        if (source[i] !== ',') fail("Expecting ',' delimiter");
        i++;
      }
    }
    for (const literal of ['true', 'false', 'null'])
      if (source.startsWith(literal, i)) {
        i += literal.length;
        return;
      }
    const number = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(source.slice(i));
    if (number) {
      i += number[0].length;
      return;
    }
    fail('Expecting value');
  };
  try {
    value();
    whitespace();
    if (i !== source.length) fail('Extra data');
  } catch (error) {
    return error as JsonFailure;
  }
  return { position: 0, message: 'Expecting value' };
}
