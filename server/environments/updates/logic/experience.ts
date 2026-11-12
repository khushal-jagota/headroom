/** Update state follows the persisted attempt, offered release, and one instant. */
import { parseIsoDatetime } from '../../../core/clock.ts';
import type { ParsedIsoDatetime } from '../../../core/clock.ts';
import type {
  DeploymentStatus,
  PublishedUpdate,
  UpdateAttempt,
  UpdateExperience,
  UpdateExperienceOptions,
} from '../contracts.ts';

export const UNFINISHED_DETAIL =
  'The update did not finish in time, so Panels stayed on the previous version.';
export const MAC_FAILED_DETAIL =
  'Panels updated, but the Mac did not. It went back to its previous version.';
export const MAC_UNREACHABLE_DETAIL =
  'The Mac did not check in during the update. It can be asleep or offline.';

export function instant(value: string): ParsedIsoDatetime {
  const parsed = parseIsoDatetime(value.replaceAll('Z', '+00:00'));
  if (!parsed.aware) throw new Error('update timestamp requires a time zone');
  return parsed;
}
function floor_div(value: bigint, by: bigint): bigint {
  const result = value / by;
  return value % by < 0 ? result - 1n : result;
}
function rounded_microseconds(seconds: number): bigint {
  const value = seconds * 1_000_000;
  const lower = Math.floor(value);
  const fraction = value - lower;
  return BigInt(fraction === 0.5 ? (lower % 2 === 0 ? lower : lower + 1) : Math.round(value));
}
export function add_seconds(value: ParsedIsoDatetime, seconds: number): ParsedIsoDatetime {
  const epochMicroseconds = value.epochMicroseconds + rounded_microseconds(seconds);
  const zone = value.iso.slice(-6);
  const minutes =
    (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(4))) * (zone[0] === '-' ? -1 : 1);
  const local = epochMicroseconds + BigInt(minutes) * 60_000_000n;
  const date = new Date(Number(floor_div(local, 1000n)));
  const microsecond = ((local % 1_000_000n) + 1_000_000n) % 1_000_000n;
  const fraction = microsecond === 0n ? '' : '.' + microsecond.toString().padStart(6, '0');
  return {
    date: new Date(Number(floor_div(epochMicroseconds, 1000n))),
    iso: date.toISOString().slice(0, 19) + fraction + zone,
    aware: true,
    epochMicroseconds,
  };
}
export function settle(
  attempt: UpdateAttempt,
  app_sha: string | null,
  now: string,
): UpdateAttempt | null {
  if (
    attempt.state !== 'installing' ||
    attempt.deadline_at === null ||
    instant(now).epochMicroseconds <= instant(attempt.deadline_at).epochMicroseconds
  )
    return null;
  return attempt.to_sha === app_sha
    ? { ...attempt, state: 'done', detail: null }
    : { ...attempt, state: 'failed', detail: UNFINISHED_DETAIL };
}
export function resolve(
  attempt: UpdateAttempt | null,
  available: PublishedUpdate | null,
  app_sha: string | null,
  options: UpdateExperienceOptions,
): UpdateExperience {
  const idle: UpdateExperience =
    available !== null ? ['available', ['update'], null, null] : ['up_to_date', [], null, null];
  if (attempt === null) return idle;
  if (attempt.state === 'waiting')
    return attempt.mode === 'idle'
      ? ['waiting', ['cancel', 'update_now'], null, null]
      : ['updating', [], null, attempt.deadline_at];
  if (attempt.state === 'preparing' || attempt.state === 'installing')
    return ['updating', [], null, attempt.deadline_at];
  if (attempt.state === 'failed') {
    if (available === null) return ['failed', [], attempt.detail, null];
    if (available.manifest.app_sha !== attempt.to_sha) return idle;
    return ['failed', ['retry'], attempt.detail, null];
  }
  if (attempt.to_sha !== app_sha || !options.mac_applies || options.mac_phase === 'current')
    return idle;
  if (options.mac_phase === 'failed' || options.mac_phase === 'reverted')
    return ['mac_failed', ['retry_mac'], MAC_FAILED_DETAIL, null];
  let heard = instant(attempt.updated_at);
  if (options.mac_reported_at !== null) {
    const reported = instant(options.mac_reported_at);
    if (reported.epochMicroseconds > heard.epochMicroseconds) heard = reported;
  }
  const silent_after = add_seconds(heard, options.mac_report_seconds);
  return instant(options.now).epochMicroseconds > silent_after.epochMicroseconds
    ? ['mac_unreachable', ['retry_mac'], MAC_UNREACHABLE_DETAIL, null]
    : ['updating', [], null, silent_after.iso];
}
export function deployment_status(
  attempt: UpdateAttempt | null,
  app_sha: string | null,
  options: { success_seconds: number; now: string },
): DeploymentStatus {
  const body: DeploymentStatus = {
    state: 'idle',
    deployed_sha: app_sha,
    target_sha: null,
    outcome: null,
    detail: null,
    valid_until: null,
    success_display_until: null,
  };
  if (attempt === null || attempt.state === 'waiting') return body;
  body.target_sha = attempt.to_sha;
  if (attempt.state === 'preparing') body.state = 'preparing';
  else if (attempt.state === 'installing') {
    body.state = 'restarting';
    body.valid_until = attempt.deadline_at;
  } else if (attempt.state === 'failed') {
    if (
      instant(options.now).epochMicroseconds <
      add_seconds(instant(attempt.updated_at), options.success_seconds).epochMicroseconds
    ) {
      body.state = 'problem';
      body.outcome = 'failed';
      body.detail = attempt.detail;
    }
  } else if (attempt.to_sha === app_sha) {
    const until = add_seconds(instant(attempt.updated_at), options.success_seconds);
    body.outcome = 'succeeded';
    if (instant(options.now).epochMicroseconds < until.epochMicroseconds) {
      body.state = 'back_up';
      body.valid_until = until.iso;
      body.success_display_until = until.iso;
    }
  }
  return body;
}
