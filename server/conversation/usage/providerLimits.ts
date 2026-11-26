/** Project only persisted provider observations into the existing limits cards. */
import type { CachedBackendUsage } from '../backend_usage/contracts.ts';
import { datetime_json } from '../snapshot/presentation.ts';
import type { ServerProviderUsageLimits, ServerProviderUsageWindow } from './contracts.ts';
export function presentProviderLimits(observation: CachedBackendUsage): ServerProviderUsageLimits {
  const windows = observation.windows.map(
    (window) =>
      ({
        id: window.id,
        kind: window.kind === 'five_hour' ? 'session' : 'weekly',
        label: window.label,
        usedPercent: window.used_percent,
        ...(window.resets_at === null ? {} : { resetsAt: datetime_json(window.resets_at) }),
        ...(window.window_duration_mins === null
          ? {}
          : { windowDurationMins: window.window_duration_mins }),
      }) as ServerProviderUsageWindow,
  );
  return {
    checkedAt: datetime_json(observation.observed_at),
    windows,
  } as ServerProviderUsageLimits;
}
