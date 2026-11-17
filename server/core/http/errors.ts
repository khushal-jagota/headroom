/** HTTP validation errors retain FastAPI's detail envelope. */
export class HttpError extends Error {
  readonly statusCode: number;
  readonly detail: unknown;
  constructor(statusCode: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : 'request validation failed');
    this.statusCode = statusCode;
    this.detail = detail;
  }
}
/** A response-depth refusal leaves an already committed write intact. */
export class ResponseDepthError extends Error {}
/** Fastify runs with `logger: false`, so every 5xx answer logs its real error and cause here. */
export function log_server_error(
  request: { method: string; url: string },
  status: number,
  error: unknown,
): void {
  console.error(`HTTP ${status}`, request.method, request.url, error);
}
