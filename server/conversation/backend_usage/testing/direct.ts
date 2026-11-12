/** Export-only proof companion. All credentials and HTTP answers come from the driver. */
import { ClaudeUsageAdapter } from '../claude.ts';
import { CodexUsageAdapter } from '../codex.ts';
import { CodexUsageRequestRejected } from '../contracts.ts';
import type { BackendUsageResult, BackendUsageWindow } from '../contracts.ts';
import { parse_claude_windows, parse_codex_windows } from '../logic/windows.ts';

interface UsageCase {
  readonly name: string;
  readonly credential_path: string;
  readonly status_code: number;
  readonly body: unknown;
  readonly transport_error?: boolean;
}
interface UsageProof {
  readonly now: string;
  readonly claude_payloads: readonly unknown[];
  readonly codex_json: readonly string[];
  readonly refreshes: readonly UsageCase[];
}
function windows_json(windows: readonly BackendUsageWindow[] | null): unknown {
  return (
    windows?.map((window) => ({
      kind: window.kind,
      used_percent: window.used_percent,
      resets_at: window.resets_at.toISOString(),
      model_scope: window.model_scope,
    })) ?? null
  );
}
function result_json(result: BackendUsageResult): unknown {
  return {
    backend_key: result.backend_key,
    outcome: result.outcome,
    detail: result.detail,
    observed_at: result.observed_at?.toISOString() ?? null,
    windows: windows_json(result.windows),
  };
}
export async function compare_provider_usage(spec: UsageProof): Promise<unknown> {
  const refreshes = [];
  for (const test of spec.refreshes) {
    const calls: unknown[] = [];
    const adapter = new ClaudeUsageAdapter({
      credential_path: test.credential_path,
      now: () => new Date(spec.now),
      http_get: async (url, headers, timeout_seconds) => {
        calls.push({ url, headers, timeout_seconds });
        if (test.transport_error) throw new Error('Synthetic transport failure');
        return { status_code: test.status_code, body: test.body };
      },
    });
    refreshes.push({ name: test.name, result: result_json(await adapter.refresh()), calls });
  }
  const codex_refreshes = [];
  for (const rejected of [false, true]) {
    const calls: unknown[] = [];
    const adapter = new CodexUsageAdapter({
      executable_path: () => '/synthetic/bin/codex',
      now: () => new Date(spec.now),
      read_codex_rate_limits: async (executable) => {
        calls.push({ executable });
        if (rejected) throw new CodexUsageRequestRejected(401, 'Synthetic login required');
        return spec.codex_json[0]!;
      },
    });
    codex_refreshes.push({ result: result_json(await adapter.refresh()), calls });
  }
  return {
    claude_windows: spec.claude_payloads.map((body) => windows_json(parse_claude_windows(body))),
    codex_windows: spec.codex_json.map((raw) => windows_json(parse_codex_windows(raw))),
    refreshes,
    codex_refreshes,
  };
}
