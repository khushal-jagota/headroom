/** Framework-boundary checks for the mechanical T3 Svelte translation. */
import { describe, expect, it, vi } from "vitest";
import { render } from "svelte/server";
import { UsageDay, USAGE_CONTRACT_VERSION, type UsageSummary, type UsageSummaryInput } from "../src/lib/usage/contracts";
import RecordedUsage from "../src/components/usage/RecordedUsage.svelte";
import UsageLimits from "../src/components/usage/UsageLimits.svelte";
import UsageHeader from "../src/components/usage/UsageHeader.svelte";
import { prefixHref } from "../src/lib/utilityAddress";
import * as wire from "../src/lib/conversation/recordedUsageWire";
import { refreshUsage } from "../src/lib/usage/usage";
import { readUsageSummary, readUsageLimits } from "../src/lib/conversation/recordedUsageWire";

const input: UsageSummaryInput = {
  sinceDay: UsageDay.make("2026-10-28"), untilDay: UsageDay.make("2026-10-29"), timeZone: "UTC", resolution: "hour",
  sinceTime: "2026-10-28T12:37:00.000Z", untilTime: "2026-10-29T12:37:00.000Z",
};
function summary(): UsageSummary {
  return {
    contractVersion: USAGE_CONTRACT_VERSION, readAt: "2026-10-29T12:37:00.000Z", timeZone: "UTC", sinceDay: input.sinceDay, untilDay: input.untilDay,
    buckets: [{ day: input.sinceDay, hourStart: input.sinceTime, provider: "codex", model: "example-model",
      totals: { uncachedInputTokens: 100, cachedInputTokens: 200, cacheCreationTokens: 10, outputTokens: 50, reasoningTokens: 20 },
      costUsd: 7, cacheSavingsUsd: 2, costSource: "modelPriced", records: 2, unpricedRecords: 1, sessions: 1 }],
    sources: [{ fingerprint: { hostId: "test", provider: "codex", resolvedHomePath: "/test/codex", volumeId: "1:2" },
      status: "ok", scannedFiles: 1, skippedFiles: 0, malformedRecords: 0, distinctSessions: 1, message: null }],
    pricing: { status: "cached", source: "https://example.test/prices", fetchedAt: null, knownModels: 1 }, scanDurationMs: 12,
  };
}

describe("T3 Svelte presentation", () => {
  it("renders copied merge totals and zero-filled 24-hour columns through Svelte", () => {
    const body = render(RecordedUsage, { props: { summary: summary(), input, providers: ["codex"], metric: "tokens" } }).body;
    expect(body).toContain("360");
    expect(body).toContain("Across 1 sessions");
    expect(body.match(/data-usage-period=/g)).toHaveLength(24);
    expect(body.match(/data-usage-value="0"/g)).toHaveLength(23);
    expect(body).toContain("example-model");
    expect(body).toContain("1 files");
  });
  it("renders the T3 API-equivalent cost label and unpriced share", () => {
    const body = render(RecordedUsage, { props: { summary: summary(), input, providers: ["codex"], metric: "cost" } }).body;
    for (const text of ["Raw token cost", "$7.00*", "* if billed at full API rate", "Cache savings", "$2.00", "50.0%", "of records, excluded from cost"])
      expect(body).toContain(text);
  });
  it("filters source and bucket data together before the copied merge", () => {
    const body = render(RecordedUsage, { props: { summary: summary(), input, providers: [], metric: "tokens" } }).body;
    expect(body).toContain("No activity in this window.");
    expect(body).not.toContain("example-model");
    expect(body).toContain("Across 0 sessions");
  });
  it("renders actual zero allowance, observation and reset without a credit-balance conversion", () => {
    const body = render(UsageLimits, { props: { providers: ["codex", "claude"], now: Date.parse("2026-10-29T12:00:00Z"), limits: {
      codex: { checkedAt: "2026-10-29T11:59:00Z", windows: [{ id: "secondary", kind: "weekly", label: "Weekly", usedPercent: 100, windowDurationMins: 10080, resetsAt: "2026-11-04T06:35:00Z" }] },
      claude: { checkedAt: "2026-10-29T11:59:00Z", windows: [], unavailable: { reason: "probeFailed", message: "No valid local allowance observation." } },
    } } }).body;
    expect(body).toContain("0%");
    expect(body).toContain("Weekly");
    expect(body).toContain("2026-10-29T11:59:00Z");
    expect(body).toContain("2026-11-04T06:35:00Z");
    expect(body).toContain("No valid local allowance observation.");
    expect(body).not.toContain("reset credits banked");
  });
  it("keeps the ordinary Config address and disables the period only on Limits", () => {
    const body = render(UsageHeader, { props: { tab: "limits", href: prefixHref("#/backends"), days: 1,
      providerKeys: ["codex"], selectedProviders: ["codex"], busy: false, onDays: () => {}, onProviders: () => {}, onRefresh: () => {} } }).body;
    expect(body).toContain('href="#/backends/config"');
    expect(body).toContain("24h");
    expect(body).toContain("disabled");
  });
});

describe("local usage HTTP boundary", () => {
  it("uses exact T3 window query fields and GET-only local limits", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await readUsageSummary(input);
    await readUsageLimits();
    const url = new URL(fetch.mock.calls[0][0], "http://test.local");
    expect(url.pathname).toBe("/api/conversation/usage/summary");
    expect(Object.fromEntries(url.searchParams)).toEqual(input);
    expect(fetch.mock.calls[1][0]).toBe("/api/conversation/usage/limits");
    for (const call of fetch.mock.calls) expect(call[1]?.method ?? "GET").toBe("GET");
  });
  it("still invalidates and rescans after a pricing refresh failure, in T3 order", async () => {
    const calls: string[] = [];
    vi.spyOn(wire, "refreshUsageRates").mockImplementation(async () => {
      calls.push("rates");
      throw new Error("pricing unavailable");
    });
    await refreshUsage(async () => { calls.push("summary"); });
    expect(calls).toEqual(["rates", "summary"]);
  });

});
