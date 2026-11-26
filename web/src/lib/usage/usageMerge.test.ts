import {
  USAGE_CONTRACT_VERSION,
  type UsageBucket,
  type UsageDay,
  type UsageProviderKind,
  type UsageSummary,
} from "@t3tools/contracts";
import { describe, expect, it } from "vite-plus/test";

import { mergeUsageSummaries, type MachineUsage } from "./usageMerge.ts";
import { calculateUsageTotals } from "./usageTotals.ts";

function bucket(provider: UsageProviderKind, costUsd: number): UsageBucket {
  return {
    day: "2026-08-07" as UsageDay,
    provider,
    model: `${provider}-model`,
    totals: { uncachedInputTokens: 100, cachedInputTokens: 0, cacheCreationTokens: 0, outputTokens: 50, reasoningTokens: 0 },
    costUsd,
    cacheSavingsUsd: 0,
    costSource: "modelPriced",
    records: 1,
    unpricedRecords: 0,
    sessions: 1,
  };
}

function machine(machineId: string, hostId: string, costUsd: number, distinctSessions: number, isHome = false): MachineUsage {
  const summary: UsageSummary = {
    contractVersion: USAGE_CONTRACT_VERSION,
    readAt: `2026-08-07T00:0${costUsd % 10}:00.000Z`,
    timeZone: "UTC",
    sinceDay: "2026-08-01" as UsageDay,
    untilDay: "2026-08-31" as UsageDay,
    buckets: [bucket("claude", costUsd)],
    sources: [{
      fingerprint: { hostId, provider: "claude", resolvedHomePath: "/home/me/.claude", volumeId: `vol-${hostId}` },
      status: "ok", scannedFiles: 1, skippedFiles: 0, malformedRecords: 0, distinctSessions, message: null,
    }],
    pricing: { status: "fresh", source: machineId, fetchedAt: null, knownModels: 10 },
    scanDurationMs: costUsd,
  };
  return { machineId, name: `Machine ${machineId}`, isHome, summary };
}

describe("mergeUsageSummaries", () => {
  it("adds the totals of machines that read different transcript directories", () => {
    const merged = mergeUsageSummaries([machine("b", "mac", 3, 2), machine("a", "vps", 5, 4, true)]);
    const totals = calculateUsageTotals(merged!.summary);
    expect(totals.totalTokens).toBe(300);
    expect(totals.costUsd).toBe(8);
    expect(totals.sessions).toBe(6);
    expect(merged!.duplicateSources).toEqual([]);
    expect(merged!.sources.map(source => source.machineName)).toEqual(["Machine a", "Machine b"]);
    expect(merged!.summary.pricing.source).toBe("a");
    expect(merged!.summary.readAt).toBe("2026-08-07T00:03:00.000Z");
    expect(merged!.summary.scanDurationMs).toBe(5);
  });

  it("counts a directory two machines report once and lists the later machine as a duplicate", () => {
    const merged = mergeUsageSummaries([machine("b", "vps", 3, 2), machine("a", "vps", 5, 4, true)]);
    const totals = calculateUsageTotals(merged!.summary);
    expect(totals.costUsd).toBe(5);
    expect(totals.sessions).toBe(4);
    expect(merged!.duplicateSources).toEqual(["Machine b: /home/me/.claude"]);
    expect(merged!.sources.map(source => source.machineId)).toEqual(["a"]);
  });

  it("gives the home summary's own totals when no other machine answered", () => {
    const home = machine("a", "vps", 5, 4, true);
    const merged = mergeUsageSummaries([home]);
    expect(calculateUsageTotals(merged!.summary)).toEqual(calculateUsageTotals(home.summary));
    expect(merged!.summary).toEqual(home.summary);
  });
});
