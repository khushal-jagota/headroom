/**
 * Each machine's saved allowance readings, shaped as T3 Code's connected environments, so the
 * copied `collectLimitAccounts` merges one account signed in on two machines into one entry.
 */
import {
  EnvironmentId,
  ProviderDriverKind,
  ProviderInstanceId,
  type ServerProvider,
  type ServerProviderUsageLimits,
} from "./contracts";
import type { BackendUsageRefreshOutcome } from "../conversation/wire";
import type { BackendUsageRefreshAttempt } from "../conversation/backendRefresh";
import { formatDuration } from "./usageLimits";
import type { Machine } from "../machines/contracts";

/** `GET /api/conversation/usage/limits`: one entry per machine and backend with a saved observation. */
export interface MachineUsageLimits {
  readonly machine_id: string;
  readonly machine_name: string;
  readonly backend_key: string;
  /** The account the machine's card named when the observation was read, usually an email. */
  readonly account_label: string | null;
  /** That card's plan line, such as `max plan via claude.ai`, for a confirmed sign-in only. */
  readonly account_detail: string | null;
  readonly limits: ServerProviderUsageLimits;
}
/** The latest refresh of one backend on one machine that did not succeed. */
export interface MachineUsageFailure {
  readonly machine_id: string;
  readonly machine_name: string;
  readonly backend_key: string;
  readonly outcome: "unavailable" | "unauthenticated" | "failed";
  readonly detail: string | null;
  readonly failed_at: string;
}
export interface MachineUsageLimitsResponse {
  readonly readings: readonly MachineUsageLimits[];
  readonly failures: readonly MachineUsageFailure[];
}

const FAILURE_WORDS = {
  unauthenticated: "not signed in",
  failed: "could not reach the provider",
  unavailable: "limits are not available",
} as const;
/**
 * One plain line per failure, such as "Antigravity on Mac 1: not signed in". The last good figures
 * stay on the page, so the line names their age, or says that there are none.
 */
export function failureLines(
  failures: readonly MachineUsageFailure[],
  readings: readonly MachineUsageLimits[],
  now: number,
  providerName: (backendKey: string) => string,
): { readonly key: string; readonly outcome: MachineUsageFailure["outcome"]; readonly text: string }[] {
  return failures.map(failure => {
    const good = readings.find(reading => reading.machine_id === failure.machine_id && reading.backend_key === failure.backend_key);
    const checked = good ? Date.parse(good.limits.checkedAt) : Number.NaN;
    const figures = !good ? "No figures yet."
      : Number.isNaN(checked) ? "Showing its last figures."
      : `Showing figures from ${formatDuration(Math.max(0, now - checked))} ago.`;
    return {
      key: `${failure.machine_id}:${failure.backend_key}`,
      outcome: failure.outcome,
      text: `${providerName(failure.backend_key)} on ${failure.machine_name}: ${FAILURE_WORDS[failure.outcome]}. ${figures}`,
    };
  });
}
/** One backend's refresh outcome on one machine. */
export type MachineUsageRefreshOutcome = BackendUsageRefreshOutcome & { readonly machine_id: string; readonly machine_name: string };

type LimitPresentations = Map<EnvironmentId, {
  readonly entry: { readonly target: { readonly label: string } };
  readonly serverConfig: { readonly providers: readonly ServerProvider[] };
}>;

export function limitPresentations(readings: readonly MachineUsageLimits[]): LimitPresentations {
  const presentations: LimitPresentations = new Map();
  for (const reading of readings) {
    const environmentId = EnvironmentId.make(reading.machine_id);
    const presentation = presentations.get(environmentId)
      ?? { entry: { target: { label: reading.machine_name } }, serverConfig: { providers: [] } };
    // Only an email names one account wherever it signs in. Another label, such as "Logged in
    // using an API key", would merge different accounts, so that reading stays its machine's own.
    const email = reading.account_label?.includes("@") ? reading.account_label.trim() : undefined;
    const provider: ServerProvider = {
      instanceId: ProviderInstanceId.make(reading.backend_key),
      driver: ProviderDriverKind.make(reading.backend_key),
      enabled: true, installed: true, version: null, status: "ready",
      auth: { status: "authenticated", ...(email ? { email } : {}), ...(reading.account_detail ? { label: reading.account_detail } : {}) },
      checkedAt: reading.limits.checkedAt, models: [], slashCommands: [], skills: [],
      usageLimits: reading.limits,
    };
    presentations.set(environmentId, { ...presentation, serverConfig: { providers: [...presentation.serverConfig.providers, provider] } });
  }
  return presentations;
}

/**
 * Refreshes every available machine at once. An unavailable machine is not asked, and the server
 * keeps its last observation, so it is only named in a notice.
 */
export async function refreshMachineLimits(
  machines: readonly Machine[],
  refresh: (machineId: string) => Promise<BackendUsageRefreshAttempt>,
): Promise<{ outcomes: MachineUsageRefreshOutcome[]; notices: string[] }> {
  const results = await Promise.all(machines.filter(machine => machine.available)
    .map(async machine => ({ machine, result: await refresh(machine.machine_id) })));
  return {
    outcomes: results.flatMap(({ machine, result }) =>
      result.outcomes.map(outcome => ({ ...outcome, machine_id: machine.machine_id, machine_name: machine.name }))),
    notices: [
      ...machines.filter(machine => !machine.available).map(machine => `${machine.name} is unavailable, so its last observation stays.`),
      ...results.flatMap(({ machine, result }) => result.error ? [`${machine.name}: ${result.error}`] : []),
    ],
  };
}
