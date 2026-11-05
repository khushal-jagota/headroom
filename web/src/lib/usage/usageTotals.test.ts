import {
  USAGE_CONTRACT_VERSION,
  type UsageBucket,
  type UsageDay,
  type UsageProviderKind,
  type UsageSummary,
} from "@t3tools/contracts";
import { describe, expect, it } from "vite-plus/test";

import { calculateUsageTotals } from "./usageTotals.ts";

function bucket(overrides: Partial<UsageBucket> = {}): UsageBucket {
  return {
    day: "2026-08-07" as UsageDay,
    provider: "claude",
    model: "claude-fable-5",
    totals: {
      uncachedInputTokens: 100,
      cachedInputTokens: 1000,
      cacheCreationTokens: 10,
      outputTokens: 50,
      reasoningTokens: 0,
    },
    costUsd: 10,
    cacheSavingsUsd: 2,
    costSource: "modelPriced",
    records: 5,
    unpricedRecords: 0,
    sessions: 1,
    ...overrides,
  };
}

function summary(
  buckets: readonly UsageBucket[],
  sources: readonly {
    provider: UsageProviderKind;
    hostId: string;
    homePath: string;
    volumeId?: string;
    distinctSessions?: number;
  }[],
  contractVersion: number = USAGE_CONTRACT_VERSION,
): UsageSummary {
  return {
    contractVersion,
    readAt: "2026-08-07T00:00:00.000Z",
    timeZone: "UTC",
    sinceDay: "2026-08-01" as UsageDay,
    untilDay: "2026-08-31" as UsageDay,
    buckets,
    sources: sources.map((source) => ({
      fingerprint: {
        hostId: source.hostId,
        provider: source.provider,
        resolvedHomePath: source.homePath,
        volumeId: source.volumeId ?? `vol-${source.hostId}`,
      },
      status: "ok" as const,
      scannedFiles: 1,
      skippedFiles: 0,
      malformedRecords: 0,
      distinctSessions: source.distinctSessions ?? 1,
      message: null,
    })),
    pricing: { status: "fresh", source: "litellm", fetchedAt: null, knownModels: 10 },
    scanDurationMs: 1,
  };
}

describe("calculateUsageTotals", () => {
  it("derives token totals, provider shares, models, and cost quality", () => {
    const totals = calculateUsageTotals(summary([
      bucket({ costUsd: 75 }),
      bucket({ provider: "codex", model: "gpt-5.6-sol", costUsd: 25, unpricedRecords: 5,
        totals: { uncachedInputTokens: 100, cachedInputTokens: 1000, cacheCreationTokens: 10,
          outputTokens: 50, reasoningTokens: 20 } }),
    ], [
      { provider: "claude", hostId: "vps", homePath: "/a/.claude" },
      { provider: "codex", hostId: "vps", homePath: "/a/.codex" },
    ]));

    expect(totals.costUsd).toBe(100);
    expect(totals.totalTokens).toBe(2320);
    expect(totals.reasoningTokens).toBe(20);
    expect(totals.records).toBe(10);
    expect(totals.providers[0]?.provider).toBe("claude");
    expect(totals.providers[0]?.costShare).toBeCloseTo(0.75, 5);
    expect(totals.providers[0]?.tokenShare).toBeCloseTo(0.5, 5);
    expect(totals.models.map(model => model.model)).toEqual(["claude-fable-5", "gpt-5.6-sol"]);
    expect(totals.costQuality.unpricedShare).toBeCloseTo(0.5, 5);
    expect(totals.costQuality.cacheSavingsUsd).toBe(4);
  });

  it("totals sessions from per-directory distinct counts, not per-bucket sums", () => {
    const totals = calculateUsageTotals(summary(
      [bucket({ day: "2026-08-06" as UsageDay }), bucket({ day: "2026-08-07" as UsageDay })],
      [{ provider: "claude", hostId: "vps", homePath: "/a/.claude", distinctSessions: 1 }],
    ));
    expect(totals.sessions).toBe(1);
    expect(totals.providers[0]?.sessions).toBe(1);
  });

  it("returns empty totals with no usage", () => {
    const totals = calculateUsageTotals(summary([], []));
    expect(totals.costUsd).toBe(0);
    expect(totals.daily).toHaveLength(0);
    expect(totals.hourly).toHaveLength(0);
    expect(totals.providers).toEqual([]);
    expect(totals.costQuality).toEqual({ providerReportedShare: 0, modelPricedShare: 0,
      unpricedShare: 0, cacheSavingsUsd: 0 });
  });

  it("omits providers with no sessions or usage", () => {
    const totals = calculateUsageTotals(summary([], [
      { provider: "claude", hostId: "vps", homePath: "/a/.claude", distinctSessions: 0 },
    ]));
    expect(totals.providers).toEqual([]);
  });

  it("derives hourly totals without losing the daily rollup", () => {
    const totals = calculateUsageTotals(summary([
      bucket({ hourStart: "2026-08-07T09:37:00.000Z", costUsd: 3 }),
      bucket({ hourStart: "2026-08-07T10:37:00.000Z", costUsd: 7 }),
    ], [{ provider: "claude", hostId: "vps", homePath: "/a/.claude" }]));
    expect(totals.hourly.map(hour => [hour.hourStart, hour.costUsd])).toEqual([
      ["2026-08-07T09:37:00.000Z", 3], ["2026-08-07T10:37:00.000Z", 7],
    ]);
    expect(totals.daily).toHaveLength(1);
    expect(totals.daily[0]?.costUsd).toBe(10);
    expect(totals.daily[0]?.byProvider.get("claude")?.costUsd).toBe(10);
  });
});
