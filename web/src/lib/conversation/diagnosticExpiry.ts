/** Only the canonical error code identifies omitted diagnostic history. */
export const DIAGNOSTIC_EXPIRED_MESSAGE = 'This diagnostic detail expired.';
export class DiagnosticExpiredError extends Error {
  constructor() { super(DIAGNOSTIC_EXPIRED_MESSAGE); this.name = 'DiagnosticExpiredError'; }
}
export function isDiagnosticExpiry(raw: string): boolean {
  try { return JSON.parse(raw)?.error?.code === 'diagnostic_expired'; }
  catch { return false; }
}
