/** Project only persisted provider observations into the existing limits cards. */
import type { CachedBackendUsage } from '../backend_usage/contracts.ts';
import { datetime_json } from '../snapshot/presentation.ts';
import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import type { ServerProviderUsageLimits, ServerProviderUsageWindow } from './contracts.ts';

/**
 * One machine's saved observation for one backend, with the account that machine is signed in
 * to. Limits belong to the account, so the browser merges readings that name the same one.
 */
export interface MachineUsageLimits {
  readonly machine_id: string;
  readonly machine_name: string;
  readonly backend_key: BackendInstanceId;
  /** The account the machine's saved card names, usually an email. Null when it names none. */
  readonly account_label: string | null;
  /** The plan line of that card, such as `max plan via claude.ai`. */
  readonly account_detail: string | null;
  readonly limits: ServerProviderUsageLimits;
}
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
