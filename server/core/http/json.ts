/** Native JSON diagnostics with the position used by the HTTP error envelope. */
export interface JsonFailure {
  position: number;
  message: string;
}
export function jsonFailure(source: string): JsonFailure {
  try {
    JSON.parse(source);
  } catch (error) {
    const message = (error as SyntaxError).message;
    const match = /position (\d+)/.exec(message);
    const offset = match ? Number(match[1]) : /end|unterminated/i.test(message) ? source.length : 0;
    return { position: Array.from(source.slice(0, offset)).length, message };
  }
  return { position: 0, message: 'Invalid JSON' };
}
