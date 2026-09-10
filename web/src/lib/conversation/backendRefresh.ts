import {
  ConversationWireError,
  readBackends,
  refreshBackends,
  type BackendSnapshot,
  type BackendsRefreshResult
} from "./wire";

export type BackendRefreshAttempt = Readonly<{
  snapshots: BackendSnapshot[] | null;
  error: string | null;
}>;

export function backendRefreshControl(refreshing: boolean): Readonly<{
  label: string;
  busy: boolean;
}> {
  return { label: refreshing ? "Reading…" : "Refresh", busy: refreshing };
}

/** Run the one explicit backend refresh and keep transport and provider failures calm. */
type BackendRefreshRequest = () => Promise<BackendsRefreshResult>;

export async function refreshBackendSnapshots(
  request: BackendRefreshRequest = refreshBackends
): Promise<BackendRefreshAttempt> {
  try {
    const result = await request();
    const failures = result.usage_outcomes
      .filter((outcome) => outcome.outcome === "failed")
      .map((outcome) => outcome.detail)
      .filter((detail): detail is string => detail !== null);
    return {
      snapshots: result.backends,
      error: failures.length === 0 ? null : failures.join(" ")
    };
  } catch (problem) {
    return {
      snapshots: null,
      error: problem instanceof ConversationWireError
        ? problem.message
        : problem instanceof Error
          ? problem.message
          : "The request did not succeed."
    };
  }
}

type BackendSnapshotRequest = () => Promise<BackendSnapshot[]>;

/** Paint the first catalogue, then enrich it with update advice in the background. */
export async function loadBackendPageSnapshots(
  onOrdinarySnapshots: (snapshots: BackendSnapshot[]) => void | Promise<void>,
  ordinaryRequest: BackendSnapshotRequest = () => readBackends(),
  advisoryRequest: () => Promise<BackendSnapshot[]> = () => readBackends(true)
): Promise<BackendRefreshAttempt> {
  let ordinarySnapshots: BackendSnapshot[];
  try {
    ordinarySnapshots = await ordinaryRequest();
  } catch (problem) {
    return {
      snapshots: null,
      error: problem instanceof ConversationWireError
        ? problem.message
        : problem instanceof Error
          ? problem.message
          : "The request did not succeed."
    };
  }
  await onOrdinarySnapshots(ordinarySnapshots);
  try {
    return {
      snapshots: await advisoryRequest(),
      error: null
    };
  } catch (problem) {
    const advisoryError = problem instanceof ConversationWireError
      ? problem.message
      : problem instanceof Error
        ? problem.message
        : "The request did not succeed.";
    return {
      snapshots: ordinarySnapshots,
      error: advisoryError
    };
  }
}
