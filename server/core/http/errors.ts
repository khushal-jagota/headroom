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
