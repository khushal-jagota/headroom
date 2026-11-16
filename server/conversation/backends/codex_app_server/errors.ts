/** Failures of the Codex app-server wire. */
export class CodexAppServerError extends Error {}
export class CodexChildWouldNotStart extends CodexAppServerError {}
export class CodexLoginIsDuplicated extends CodexChildWouldNotStart {}
export class CodexWireFailed extends CodexAppServerError {
  readonly request_may_have_been_written: boolean;
  constructor(message: string, options: { request_may_have_been_written?: boolean } = {}) {
    super(message);
    this.request_may_have_been_written = options.request_may_have_been_written ?? false;
  }
}
export class CodexRequestRejected extends CodexAppServerError {
  readonly method: string;
  readonly code: number | null;
  readonly rejection_message: string;
  readonly data: unknown;
  readonly request_id: string | null;
  constructor(
    method: string,
    code: number | null,
    message: string,
    data: unknown = null,
    options: { request_id?: string | null } = {},
  ) {
    super(`${method} was rejected: ${message}`);
    this.method = method;
    this.code = code;
    this.rejection_message = message;
    this.data = data;
    this.request_id = options.request_id ?? null;
  }
}
