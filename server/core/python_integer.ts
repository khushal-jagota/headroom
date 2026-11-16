/** Return the ASCII spelling of a Unicode decimal digit. */
export function decimal_digit(character: string): string {
  const point = character.codePointAt(0)!;
  let start = point;
  while (start > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(start - 1))) start--;
  return String((point - start) % 10);
}

/** Parse decimal Unicode digits with underscores between digits. */
export function integer_text(text: string): bigint | null {
  if (text.includes('\uFEFF')) return null;
  const trimmed = text.replace(/^[\s\u001c-\u001f\u0085]+|[\s\u001c-\u001f\u0085]+$/gu, '');
  if (
    trimmed.includes('\uFEFF') ||
    !/^[+-]?\p{Decimal_Number}(?:_?\p{Decimal_Number})*$/u.test(trimmed)
  )
    return null;
  let ascii = '';
  for (const character of trimmed) {
    if (character === '_') continue;
    if (character === '+' || character === '-') {
      ascii += character;
      continue;
    }
    ascii += decimal_digit(character);
  }
  return BigInt(ascii);
}
