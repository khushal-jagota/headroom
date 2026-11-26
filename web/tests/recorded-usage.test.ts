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
import { backendUsageOutcomeLabel, refreshBackendSnapshots } from "../src/lib/conversation/backendRefresh";
import type { BackendUsageRefreshOutcome } from "../src/lib/conversation/wire";
import { readUsageSummary, readUsageLimits } from "../src/lib/conversation/recordedUsageWire";
import { buildChartDays } from "../src/lib/usage/usageChartData";
import { usageSourceColor, usageSourceLabel, usageSourceOrder } from "../src/lib/usage/usageProviders";
import { DRIVERS } from "./support/drivers";
import type { ServerProviderUsageLimits } from "../src/lib/usage/contracts";
import { refreshMachineLimits, type MachineUsageLimits } from "../src/lib/usage/machineLimits";
import type { Machine } from "../src/lib/machines/contracts";

/** One machine's saved reading for one backend, as the limits read returns it. */
function reading(backend_key: string, limits: ServerProviderUsageLimits, machine_name = "VPS 1", account_label: string | null = null): MachineUsageLimits {
  return { machine_id: machine_name, machine_name, backend_key, account_label, account_detail: null, limits };
}

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
    const body = render(RecordedUsage, { props: { summary: summary(), input, providers: ["codex"], metric: "tokens", drivers: DRIVERS } }).body;
    expect(body).toContain("360");
    expect(body).toContain("Across 1 sessions");
    expect(body.match(/data-usage-period=/g)).toHaveLength(24);
    expect(body.match(/data-usage-value="0"/g)).toHaveLength(23);
    expect(body).toContain("example-model");
    expect(body).toContain("1 files");
  });
  it("renders the T3 API-equivalent cost label and unpriced share", () => {
    const body = render(RecordedUsage, { props: { summary: summary(), input, providers: ["codex"], metric: "cost", drivers: DRIVERS } }).body;
    for (const text of ["Raw token cost", "$7.00*", "* if billed at full API rate", "Cache savings", "$2.00", "50.0%", "of records, excluded from cost"])
      expect(body).toContain(text);
  });
  it("filters source and bucket data together before the copied merge", () => {
    const body = render(RecordedUsage, { props: { summary: summary(), input, providers: [], metric: "tokens", drivers: DRIVERS } }).body;
    expect(body).toContain("No activity in this window.");
    expect(body).not.toContain("example-model");
    expect(body).toContain("Across 0 sessions");
  });
  it("renders actual zero allowance, observation and reset without a credit-balance conversion", () => {
    const body = render(UsageLimits, { props: { providers: ["codex", "claude"], drivers: DRIVERS, now: Date.parse("2026-10-29T12:00:00Z"), readings: [
      reading("codex", { checkedAt: "2026-10-29T11:59:00Z", windows: [{ id: "secondary", kind: "weekly", label: "Weekly", usedPercent: 100, windowDurationMins: 10080, resetsAt: "2026-11-04T06:35:00Z" }] }),
      reading("claude", { checkedAt: "2026-10-29T11:59:00Z", windows: [], unavailable: { reason: "probeFailed", message: "No valid local allowance observation." } }),
    ] } }).body;
    expect(body).toContain("0%");
    expect(body).toContain("Weekly");
    expect(body).toContain('datetime="2026-10-29T11:59:00Z"');
    expect(body).toContain("Oct 29, 2026, 11:59 AM UTC");
    expect(body).toContain("2026-11-04T06:35:00Z");
    expect(body).toContain("No valid local allowance observation.");
    expect(body).not.toContain("reset credits banked");
    expect(body.indexOf('datetime="2026-10-29T11:59:00Z"')).toBeLessThan(body.indexOf('data-allowance-window='));
  });
  // Distinct risk: limits belong to the provider account. Before this, the page showed one machine
  // at a time, so the same account looked like two quotas, or one hid behind a machine picker.
  it("merges one account signed in on two machines and pools different accounts", () => {
    const weekly = (checkedAt: string, usedPercent: number): ServerProviderUsageLimits =>
      ({ checkedAt, windows: [{ id: "seven_day", kind: "weekly", label: "7 days", usedPercent, windowDurationMins: 10080 }] });
    const now = Date.parse("2026-11-26T12:00:00Z");
    const shared = render(UsageLimits, { props: { providers: ["claude"], drivers: DRIVERS, now, readings: [
      reading("claude", weekly("2026-11-26T10:00:00Z", 50), "VPS 1", "owner@example.com"),
      reading("claude", weekly("2026-11-26T11:00:00Z", 79), "Mac 1", "Owner@Example.com "),
    ] } }).body;
    expect(shared.match(/class="segment[ "]/g)).toHaveLength(1);
    expect(shared).toContain("21%");
    expect(shared).not.toContain("50%");
    expect(shared).toContain("Signed in: VPS 1, Mac 1");
    const distinct = render(UsageLimits, { props: { providers: ["claude"], drivers: DRIVERS, now, readings: [
      reading("claude", weekly("2026-11-26T10:00:00Z", 50), "VPS 1", "owner@example.com"),
      reading("claude", weekly("2026-11-26T11:00:00Z", 79), "Mac 1", "other@example.com"),
    ] } }).body;
    expect(distinct.match(/class="segment[ "]/g)).toHaveLength(2);
    for (const text of ["owner@example.com", "other@example.com", "Signed in: VPS 1", "Signed in: Mac 1"]) expect(distinct).toContain(text);
  });
  it("offers no Config tab, which moved to Settings, and disables the period only on Limits", () => {
    const body = render(UsageHeader, { props: { tab: "limits", href: prefixHref("#/backends"), days: 1,
      providerKeys: ["codex"], selectedProviders: ["codex"], busy: false, drivers: DRIVERS, onDays: () => {}, onProviders: () => {}, onRefresh: () => {} } }).body;
    expect(body).toContain('href="#/backends/tokens"');
    expect(body).not.toContain('href="#/backends/config"');
    expect(body).toContain("24h");
    expect(body).toContain("disabled");
  });
});

describe("usage names and colours come from the drivers", () => {
  function twoSources(): UsageSummary {
    const base = summary();
    const bucket = (provider: string, model: string) => ({ ...base.buckets[0], provider, model });
    const source = (provider: string) => ({ ...base.sources[0], fingerprint: { ...base.sources[0].fingerprint, provider, resolvedHomePath: `/test/${provider}` } });
    return { ...base, buckets: [bucket("claude", "claude-model"), bucket("grok", "grok-model"), bucket("antigravity", "other-model")],
      sources: [source("claude"), source("grok"), source("antigravity")] };
  }
  it("labels and colours each source from the drivers, Grok as itself, and an unknown key as itself", () => {
    const body = render(RecordedUsage, { props: { summary: twoSources(), input, providers: ["claude", "grok", "antigravity"], metric: "tokens", drivers: DRIVERS } }).body;
    expect(body).toContain("Claude");
    expect(body).not.toContain("Claude Code");
    expect(body).toContain("background: #d97757");
    expect(body).toContain("Grok Build");
    expect(body).toMatch(/<i[^>]*style="background: #a1a1aa;"[^>]*><\/i>Grok Build<\/span>/);
    expect(body).toMatch(/<i[^>]*style="background: #a1a1aa;"[^>]*><\/i>Antigravity<\/span>/);
    expect(body).toMatch(/<i[^>]*style="background: #d97757;"[^>]*><\/i>Claude<\/span>/);
  });
  it("colours an allowance pool with its driver's accent", () => {
    const body = render(UsageLimits, { props: { providers: ["claude"], drivers: DRIVERS, now: Date.parse("2026-10-29T12:00:00Z"), readings: [
      reading("claude", { checkedAt: "2026-10-29T11:59:00Z", windows: [{ id: "session", kind: "session", label: "Session", usedPercent: 10, windowDurationMins: 300 }] }),
    ] } }).body;
    expect(body).toContain("<h2 class=");
    expect(body).toMatch(/<h2[^>]*>Claude<\/h2>/);
    expect(body).toContain("--provider-color: #d97757");
  });
  it("names a source in the provider filter by its driver label", () => {
    const body = render(UsageHeader, { props: { tab: "tokens", href: prefixHref("#/backends"), days: 1,
      providerKeys: ["codex", "claude", "grok"], selectedProviders: ["codex"], busy: false, drivers: DRIVERS, onDays: () => {}, onProviders: () => {}, onRefresh: () => {} } }).body;
    for (const label of ["Codex", "Claude", "Grok Build", "Tokens"]) expect(body).toContain(label);
    expect(body).not.toContain("Claude Code");
  });
  it("stacks codex, claude, then grok, with a saved Hermes source on top, named and neutral", () => {
    const byProvider = new Map([["grok", { costUsd: 1, totalTokens: 1 }], ["claude", { costUsd: 1, totalTokens: 1 }],
      ["codex", { costUsd: 1, totalTokens: 1 }], ["hermes", { costUsd: 1, totalTokens: 1 }]]);
    const [day] = buildChartDays(["2026-10-28"], [{ day: "2026-10-28", costUsd: 4, totalTokens: 4, byProvider }], "tokens", usageSourceOrder(DRIVERS));
    expect(day.values.map(entry => entry.provider).filter(provider => byProvider.has(provider)))
      .toEqual(["codex", "claude", "grok", "hermes"]);
    expect([usageSourceLabel("hermes", DRIVERS), usageSourceColor("hermes", DRIVERS)]).toEqual(["Hermes", "#a1a1aa"]);
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

describe("explicit provider refresh", () => {
  it("retains every provider outcome with null detail and keeps saved allowance cards", async () => {
    const outcomes: BackendUsageRefreshOutcome[] = [
      { backend_key: "codex", outcome: "failed", detail: null },
      { backend_key: "claude", outcome: "unauthenticated", detail: null },
      { backend_key: "hermes", outcome: "unavailable", detail: null },
    ];
    const result = await refreshBackendSnapshots(async () => ({ backends: [], usage_outcomes: outcomes }));
    expect(result.outcomes).toEqual(outcomes);
    expect(result.error).toBeNull();
    const body = render(UsageLimits, { props: { providers: ["codex"], drivers: DRIVERS, now: Date.parse("2026-10-30T12:00:00Z"),
      outcomes: result.outcomes.map(outcome => ({ ...outcome, machine_name: "Mac 1" })), readings: [
        reading("codex", { checkedAt: "2026-10-27T11:59:00Z", windows: [{ id: "session", kind: "session", label: "Session", usedPercent: 35, windowDurationMins: 300 }] }),
      ] } }).body;
    for (const label of ["Mac 1 · Codex: Failed", "Mac 1 · Claude: Unauthenticated", "Mac 1 · Hermes: Unavailable", "65%", "Oct 27, 2026, 11:59 AM UTC"])
      expect(body).toContain(label);
    expect(backendUsageOutcomeLabel("succeeded")).toBeNull();
  });
  it("keeps transport errors separate from provider outcomes", async () => {
    const result = await refreshBackendSnapshots(async () => { throw new Error("Transport failed"); });
    expect(result).toEqual({ snapshots: null, outcomes: [], error: "Transport failed" });
  });
  // Distinct risk: Refresh now covers every machine. An unavailable machine must not be asked,
  // so its saved reading stays, and one machine's outcomes must name that machine.
  it("refreshes every available machine and names an unavailable one", async () => {
    const machine = (machine_id: string, name: string, available: boolean) =>
      ({ machine_id, name, available, icon: "server", color: "jade", is_home: false, running_workers: null, waiting_tickets: 0 }) as Machine;
    const asked: string[] = [];
    const result = await refreshMachineLimits([machine("vps", "VPS 1", true), machine("mac", "Mac 1", false)], async machineId => {
      asked.push(machineId);
      return { snapshots: [], error: null, outcomes: [{ backend_key: "claude", outcome: "succeeded", detail: null }] };
    });
    expect(asked).toEqual(["vps"]);
    expect(result.outcomes).toEqual([{ backend_key: "claude", outcome: "succeeded", detail: null, machine_name: "VPS 1" }]);
    expect(result.notices).toEqual(["Mac 1 is unavailable, so its last observation stays."]);
  });
});
