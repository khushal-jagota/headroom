/** Python's process-wide, payload-free rate limit for connection failures. */
import { CONNECTION_FAILURE_LOG_INTERVAL_SECONDS } from '../configuration.ts';
import { ExecutorHttpError, HttpTimeout, failure_name } from './errors.ts';
export type ConnectionPhase = 'lease' | 'poll' | 'items' | 'outcomes';
export type ConnectionFailureCategory =
  'configuration' | 'tls' | 'authentication' | 'timeout' | 'network' | 'connection_or_protocol';
export function classify_connection_failure(error: unknown): ConnectionFailureCategory {
  const code = (error as NodeJS.ErrnoException | null)?.code ?? '';
  if (code === 'ERR_INVALID_URL') return 'configuration';
  if (/^(ERR_TLS_|ERR_SSL_|CERT_|DEPTH_|SELF_SIGNED_|UNABLE_TO_)/.test(code)) return 'tls';
  if (error instanceof ExecutorHttpError && [401, 403].includes(error.status))
    return 'authentication';
  if (error instanceof HttpTimeout) return 'timeout';
  if (/^(E[A-Z]+)$/.test(code)) return 'network';
  return 'connection_or_protocol';
}
export class ConnectionDiagnostics {
  private readonly last = new Map<string, { at: number; repeats: number }>();
  private readonly emit: (line: string) => void;
  private readonly monotonic: () => number;
  constructor(emit: (line: string) => void, monotonic = () => performance.now() / 1000) {
    this.emit = emit;
    this.monotonic = monotonic;
  }
  report(error: unknown, phase: ConnectionPhase): void {
    const category = classify_connection_failure(error),
      name = failure_name(error),
      key = JSON.stringify([category, phase, name]);
    const now = this.monotonic(),
      last = this.last.get(key);
    if (last && now - last.at < CONNECTION_FAILURE_LOG_INTERVAL_SECONDS) {
      last.repeats++;
      return;
    }
    this.last.set(key, { at: now, repeats: 0 });
    this.emit(
      `executor connection failure: ${category} phase=${phase} exception=${name}${last ? ` unlogged_repeats=${last.repeats}` : ''}`,
    );
  }
}
