/** Never include provider or request payloads in transport diagnostics. */
export class ExecutorHttpError extends Error {
  readonly status: number;
  readonly code: string;
  readonly message: string;
  constructor(status: number, code: string, message = '') {
    super(`executor route answered ${status} ${code}`);
    this.name = 'ExecutorHttpError';
    this.status = status;
    this.code = code;
    this.message = message;
  }
  toString(): string {
    return `ExecutorHttpError: executor route answered ${this.status} ${this.code}`;
  }
}
export class HttpTimeout extends Error {
  constructor() {
    super('executor request timed out');
    this.name = 'TimeoutError';
  }
}
export function failure_name(error: unknown): string {
  if (!(error instanceof Error)) return 'Error';
  const name =
    error instanceof ExecutorHttpError ? `HTTP ${error.status} ${error.code}` : error.name;
  return name + (error.cause instanceof Error ? ` from ${error.cause.name}` : '');
}
export function http_error(status: number, body: Uint8Array): ExecutorHttpError {
  let code = 'unknown',
    message = '';
  try {
    const payload: unknown = JSON.parse(Buffer.from(body).toString('utf8'));
    if (payload && typeof payload === 'object' && 'error' in payload) {
      const error = payload.error;
      if (error && typeof error === 'object') {
        if (
          'code' in error &&
          typeof error.code === 'string' &&
          /^[a-z0-9_.-]{1,64}$/.test(error.code)
        )
          code = error.code;
        if ('message' in error && typeof error.message === 'string')
          message = [...error.message].slice(0, 500).join('');
      }
    }
  } catch {
    /* An invalid body has the same unknown diagnostic as Python. */
  }
  return new ExecutorHttpError(status, code, message);
}
