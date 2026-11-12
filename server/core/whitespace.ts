/** Python's Unicode whitespace policy, shared by configuration and text intake. */
export const PYTHON_SPACE =
  '[\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]';
const EDGES = new RegExp(`^${PYTHON_SPACE}+|${PYTHON_SPACE}+$`, 'gu');
export const strip = (value: string): string => value.replace(EDGES, '');
