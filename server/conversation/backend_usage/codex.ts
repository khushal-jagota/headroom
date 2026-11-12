/** Rate-limit policy over an injected, bounded Codex app-server read. */
import type { BackendUsageAdapter, BackendUsageResult, CodexUsageSource } from './contracts.ts';
import {
  CodexUsageTimeout,
  CodexUsageRequestRejected,
  CodexUsageLoginIsDuplicated,
} from './contracts.ts';
import { parse_codex_windows } from './logic/windows.ts';
export const CODEX_USAGE_REFRESH_TIMEOUT_SECONDS = 30;
export class CodexUsageAdapter implements BackendUsageAdapter {
  private readonly options: {
    executable_path(name: string): string | null;
    read_codex_rate_limits: CodexUsageSource;
    now(): Date;
  };
  constructor(options: {
    executable_path(name: string): string | null;
    read_codex_rate_limits: CodexUsageSource;
    now(): Date;
  }) {
    this.options = options;
  }
  async refresh(options: { signal?: AbortSignal } = {}): Promise<BackendUsageResult> {
    options.signal?.throwIfAborted();
    const result = (
      outcome: BackendUsageResult['outcome'],
      detail: string,
    ): BackendUsageResult => ({
      backend_key: 'codex',
      outcome,
      detail,
      observed_at: null,
      windows: [],
    });
    const executable = this.options.executable_path('codex');
    if (executable === null) return result('unavailable', 'Codex is not installed or not on PATH.');
    let response: unknown;
    try {
      response = await this.options.read_codex_rate_limits(executable, {
        timeout_seconds: CODEX_USAGE_REFRESH_TIMEOUT_SECONDS,
        ...options,
      });
    } catch (error) {
      options.signal?.throwIfAborted();
      if (error instanceof CodexUsageTimeout)
        return result('failed', 'Codex usage did not answer in time. Try again.');
      if (error instanceof CodexUsageRequestRejected) {
        const unauthenticated =
          [401, 403].includes(error.code) ||
          ['not logged in', 'login required', 'authentication required', 'unauthorized'].some(
            (phrase) => error.message.toLowerCase().includes(phrase),
          );
        return result(
          unauthenticated ? 'unauthenticated' : 'failed',
          unauthenticated
            ? 'Codex is not logged in. Run `codex login` and try again.'
            : 'Codex could not refresh its usage. Try again.',
        );
      }
      if (error instanceof CodexUsageLoginIsDuplicated) return result('failed', error.message);
      return result('failed', 'Codex could not refresh its usage. Try again.');
    }
    const windows = parse_codex_windows(response);
    if (windows === null) return result('failed', 'Codex could not refresh its usage. Try again.');
    if (!windows.length)
      return result('failed', 'Codex returned usage in a format Panels does not understand.');
    return {
      backend_key: 'codex',
      outcome: 'succeeded',
      detail: null,
      observed_at: this.options.now(),
      windows,
    };
  }
}
