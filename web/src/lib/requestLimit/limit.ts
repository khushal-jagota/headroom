/** The request passed its time limit. The server gave no answer, which is not a refusal. */
export class RequestLimitError extends Error {
  constructor() {
    super("The server did not answer in time.");
    this.name = "RequestLimitError";
  }
}

/**
 * One signal for the whole exchange: the caller's own abort, or the time limit.
 *
 * The timer stays armed after the response headers arrive, so the limit also covers a
 * body that stops part-way. An abort after the body was read does nothing.
 */
export function signalWithLimit(limitMs: number, callerSignal?: AbortSignal | null): AbortSignal {
  const controller = new AbortController();
  const forward = (): void => { clearTimeout(timer); controller.abort(callerSignal?.reason); };
  const timer = setTimeout(() => {
    callerSignal?.removeEventListener("abort", forward);
    controller.abort(new RequestLimitError());
  }, limitMs);
  if (callerSignal?.aborted) forward();
  else callerSignal?.addEventListener("abort", forward, { once: true });
  return controller.signal;
}
