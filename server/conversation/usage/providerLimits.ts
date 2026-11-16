/** Project only persisted provider observations into the existing limits cards. */
import type { CachedBackendUsage } from '../backend_usage/contracts.ts';
import { datetime_json } from '../snapshot/presentation.ts';
import type { ServerProviderUsageLimits, ServerProviderUsageWindow } from './contracts.ts';
export function presentProviderLimits(observation: CachedBackendUsage): ServerProviderUsageLimits {
  const windows = observation.windows.map((window): ServerProviderUsageWindow => {
    const session = window.kind === 'five_hour';
    const duration = session ? '5 hours' : '7 days';
    return {
      id: `${window.kind}:${window.model_scope || ''}`,
      kind: session ? 'session' : 'weekly',
      label: window.model_scope === null ? duration : `${duration} · ${window.model_scope}`,
      usedPercent: window.used_percent,
      resetsAt: datetime_json(window.resets_at),
      windowDurationMins: session ? 300 : 10080,
    } as ServerProviderUsageWindow;
  });
  return {
    checkedAt: datetime_json(observation.observed_at),
    windows,
  } as ServerProviderUsageLimits;
}
