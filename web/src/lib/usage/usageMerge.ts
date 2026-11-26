/** Adapted from T3 packages/shared/src/usageMerge.ts:84–158 (fingerprintKey, claimSources,
 * ownedContribution). See T3-LICENSE.
 *
 * T3 merges per-environment summaries straight into page totals. Panels merges each
 * machine's summary into one combined `UsageSummary` instead, so `calculateUsageTotals`
 * sums it unchanged. The source-ownership rule is T3's: the first machine in machine-id
 * order claims each transcript directory, and a later machine's duplicate drops that
 * provider's buckets from that machine.
 */
import type {
  UsageBucket,
  UsageProviderKind,
  UsageSource,
  UsageSourceFingerprint,
  UsageSummary,
} from "@t3tools/contracts";

export interface MachineUsage {
  readonly machineId: string;
  readonly name: string;
  readonly isHome: boolean;
  readonly summary: UsageSummary;
}

/** A kept source and the machine that read it. */
export interface MachineUsageSource {
  readonly machineId: string;
  readonly machineName: string;
  readonly source: UsageSource;
}

export interface MergedUsageSummary {
  readonly summary: UsageSummary;
  /** The combined summary's sources, in the same order, each with its machine. */
  readonly sources: readonly MachineUsageSource[];
  /** Sources dropped as a duplicate of another machine's, as "machine: path". */
  readonly duplicateSources: readonly string[];
}

/**
 * Two sources are the same physical transcript directory only when host,
 * provider, path and filesystem identity all agree.
 *
 * `volumeId` is what stops two machines that happen to share a hostname and a
 * home path, which is every Mac in a fleet, from collapsing into one source and
 * having one of them silently dropped.
 */
function fingerprintKey(fingerprint: UsageSourceFingerprint): string {
  return [
    fingerprint.hostId,
    fingerprint.provider,
    fingerprint.resolvedHomePath,
    fingerprint.volumeId,
  ].join(" ");
}

/**
 * Decides which machine owns each physical transcript directory.
 *
 * The first machine in a stable order claims a fingerprint; the rest have that
 * provider's buckets dropped. Machines are sorted by id so the winner does not
 * change between renders.
 */
function claimSources(machines: readonly MachineUsage[]): {
  readonly ownerByFingerprint: ReadonlyMap<string, string>;
  readonly duplicates: readonly string[];
} {
  const ownerByFingerprint = new Map<string, string>();
  const duplicates: string[] = [];
  for (const machine of machines) {
    for (const source of machine.summary.sources) {
      if (source.status === "missing") continue;
      const key = fingerprintKey(source.fingerprint);
      if (ownerByFingerprint.has(key)) {
        duplicates.push(`${machine.name}: ${source.fingerprint.resolvedHomePath}`);
        continue;
      }
      ownerByFingerprint.set(key, machine.machineId);
    }
  }
  return { ownerByFingerprint, duplicates };
}

/** Sources this machine owns after fingerprint claims, plus their buckets. */
function ownedContribution(
  machine: MachineUsage,
  ownerByFingerprint: ReadonlyMap<string, string>,
): { readonly buckets: readonly UsageBucket[]; readonly sources: readonly UsageSource[] } {
  const ownedProviders = new Set<UsageProviderKind>();
  // A missing source counts nothing; it stays listed so its status still shows.
  const sources = machine.summary.sources.filter((source) => {
    if (source.status === "missing") return true;
    if (ownerByFingerprint.get(fingerprintKey(source.fingerprint)) !== machine.machineId) return false;
    ownedProviders.add(source.fingerprint.provider);
    return true;
  });
  return {
    buckets: machine.summary.buckets.filter((bucket) => ownedProviders.has(bucket.provider)),
    sources,
  };
}

/**
 * Merges every answered machine's summary for one window into one summary.
 *
 * The window fields and model prices are the home machine's (the first machine when
 * the home did not answer). `readAt` is the oldest read, since the totals are no
 * fresher than it, and `scanDurationMs` is the longest scan, since the reads run in
 * parallel.
 */
export function mergeUsageSummaries(machines: readonly MachineUsage[]): MergedUsageSummary | null {
  const ordered = [...machines].sort((a, b) => a.machineId.localeCompare(b.machineId));
  const home = ordered.find((machine) => machine.isHome) ?? ordered[0];
  if (home === undefined) return null;
  const { ownerByFingerprint, duplicates } = claimSources(ordered);
  const buckets: UsageBucket[] = [];
  const sources: MachineUsageSource[] = [];
  for (const machine of ordered) {
    const owned = ownedContribution(machine, ownerByFingerprint);
    buckets.push(...owned.buckets);
    for (const source of owned.sources)
      sources.push({ machineId: machine.machineId, machineName: machine.name, source });
  }
  return {
    summary: {
      ...home.summary,
      readAt: ordered.map((machine) => machine.summary.readAt).reduce((oldest, readAt) =>
        Date.parse(readAt) < Date.parse(oldest) ? readAt : oldest),
      buckets,
      sources: sources.map((entry) => entry.source),
      scanDurationMs: Math.max(...ordered.map((machine) => machine.summary.scanDurationMs)),
    },
    sources,
    duplicateSources: duplicates,
  };
}
