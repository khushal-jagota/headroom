const unicode_space = (code: number): boolean =>
  (code >= 9 && code <= 13) ||
  code === 32 ||
  code === 0x85 ||
  code === 0xa0 ||
  code === 0x1680 ||
  (code >= 0x2000 && code <= 0x200a) ||
  code === 0x2028 ||
  code === 0x2029 ||
  code === 0x202f ||
  code === 0x205f ||
  code === 0x3000;
const python_space = (code: number): boolean =>
  unicode_space(code) || (code >= 0x1c && code <= 0x1f);
function edges(value: string, space: (code: number) => boolean): string {
  let start = 0,
    end = value.length;
  while (start < end && space(value.charCodeAt(start))) start++;
  while (end > start && space(value.charCodeAt(end - 1))) end--;
  return value.slice(start, end);
}
export const strip = (value: string): string => edges(value, python_space);
/** Numeric parsing uses Unicode White_Space, excluding U+001C through U+001F. */
export const strip_unicode_space = (value: string): string => edges(value, unicode_space);
