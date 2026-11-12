import { assigned_after_unicode_15 } from './python_repr_unicode.ts';

/** Python repr(str): printable Unicode stays literal, other code points are escaped. */
export function python_string_repr(value: string): string {
  const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
  let result = quote;
  for (const character of value) {
    if (character === quote || character === '\\') result += '\\' + character;
    else if (character === '\n') result += '\\n';
    else if (character === '\r') result += '\\r';
    else if (character === '\t') result += '\\t';
    else if (
      character !== ' ' &&
      (/[\p{C}\p{Z}]/u.test(character) || assigned_after_unicode_15(character.codePointAt(0)!))
    ) {
      const code = character.codePointAt(0)!;
      const width = code < 256 ? 2 : code < 65536 ? 4 : 8;
      result +=
        '\\' +
        (width === 2 ? 'x' : width === 4 ? 'u' : 'U') +
        code.toString(16).padStart(width, '0');
    } else result += character;
  }
  return result + quote;
}
