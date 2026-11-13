/** Storage diagnostics name failures without payload-bearing message text. */
export function failure_name(error: unknown): string {
  if (!(error instanceof Error)) return 'Error';
  return error.name + (error.cause instanceof Error ? ` from ${error.cause.name}` : '');
}
