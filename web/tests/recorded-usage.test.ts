import { describe, expect, it, vi } from "vitest";
import { render } from "svelte/server";
import type { PeriodUsageMetrics, ProviderUsageMetrics, UsageMetrics, UsageSummary } from "../src/lib/conversation/recordedUsageWire";
import { refreshRecordedUsage } from "../src/lib/conversation/recordedUsageWire";
import { chartPointerIndex, chartSegments, combineMetrics, formatCost, formatTokens, selectedTotals, tokenSegments } from "../src/components/usage/usagePresentation";
import { remainingPercent } from "../src/lib/conversation/usagePresentation";
import RecordedUsage from "../src/components/usage/RecordedUsage.svelte";
import AllowanceWindows from "../src/components/usage/AllowanceWindows.svelte";
import UsageHeader from "../src/components/usage/UsageHeader.svelte";

function metrics(overrides:Partial<UsageMetrics>={}):UsageMetrics {
  return { input_tokens:100,uncached_input_tokens:40,cached_input_tokens:50,cache_creation_tokens:10,
    output_tokens:20,reasoning_tokens:5,total_tokens:120,reported_cost_usd:null,
    record_count:1,session_count:1,unknown_records:0,unknown_cost_records:1,...overrides };
}
const codex:ProviderUsageMetrics={...metrics(),provider:"codex"};
const claude:ProviderUsageMetrics={...metrics({input_tokens:null,total_tokens:null,uncached_input_tokens:12,cached_input_tokens:null,unknown_records:1}),provider:"claude"};
function period(hour:number,providers:ProviderUsageMetrics[]=[codex]):PeriodUsageMetrics {
  return {...combineMetrics(providers),start:`2026-10-16T${String(hour).padStart(2,"0")}:00:00Z`,end:`2026-10-16T${String(hour+1).padStart(2,"0")}:00:00Z`,label:`${hour}:00`,providers};
}
function summary(overrides:Partial<UsageSummary>={}):UsageSummary {
  return {scope:"local_cli",days:1,timezone:"UTC",since:"2026-10-15T22:00:00Z",until:"2026-10-16T22:00:00Z",observed_at:"2026-10-16T12:00:00Z",
    totals:combineMetrics([codex,claude]),providers:[codex,claude],models:[{...codex,model:"model-a"},{...claude,model:null}],
    periods:[period(10),period(12,[claude])],sources:[{provider:"codex",state:"partial",observed_at:"2026-10-16T12:00:00Z",files_scanned:2,record_count:1,issues:["Read limit reached"],duplicates:1}],...overrides};
}

describe("recorded usage unknown values and filters",()=>{
  it("keeps an unknown total without losing independently known categories",()=>{
    const total=combineMetrics([codex,claude]);
    expect(total.total_tokens).toBeNull();
    expect(total.input_tokens).toBeNull();
    expect(total.uncached_input_tokens).toBe(52);
    expect(total.output_tokens).toBe(40);
    expect(total.unknown_records).toBe(1);
    expect(total.reported_cost_usd).toBeNull();
  });
  it("keeps empty records unknown and a known zero distinct",()=>{
    expect(combineMetrics([]).total_tokens).toBeNull();
    expect(combineMetrics([]).reported_cost_usd).toBeNull();
    expect(formatTokens(null)).toBe("—");
    expect(formatTokens(0)).toBe("0");
    expect(formatCost(null)).toBe("—");
    expect(formatCost(0)).not.toBe("—");
  });
  it("uses authoritative totals for all providers and only selected provider values otherwise",()=>{
    const s=summary({totals:metrics({session_count:1})});
    expect(selectedTotals(s,["codex","claude"])).toBe(s.totals);
    expect(selectedTotals(s,["codex"]).total_tokens).toBe(120);
    expect(selectedTotals(s,[]).total_tokens).toBeNull();
  });
  it("does not add cache subsets or reasoning twice",()=>{
    const parts=tokenSegments(metrics());
    expect(parts.map(part=>part.label)).toEqual(["Input","Cached input","Cache write","Output"]);
    expect(parts.reduce((sum,part)=>sum+part.value,0)).toBe(120);
    expect(parts.some(part=>part.label==="Reasoning")).toBe(false);
    expect(combineMetrics([metrics(),metrics()]).total_tokens).toBe(240);
  });
  it("does not fabricate category shares for partial or inconsistent counters",()=>{
    expect(tokenSegments(metrics({cached_input_tokens:null}))).toEqual([]);
    expect(tokenSegments(metrics({total_tokens:130}))).toEqual([]);
  });
});

describe("observed-only usage chart",()=>{
  it("selects a later recorded period from the first touch coordinate without a move",()=>{
    const periods=[period(10),period(11),period(14)];
    const bounds={left:100,width:300};
    expect(chartPointerIndex(periods,399,bounds)).toBe(2);
    expect(chartPointerIndex(periods,175,bounds)).toBe(1);
    expect(chartPointerIndex(periods,80,bounds)).toBe(0);
    expect(chartPointerIndex(periods,450,bounds)).toBe(2);
    expect(chartPointerIndex([],399,bounds)).toBeNull();
    expect(chartPointerIndex(periods,399,{left:100,width:0})).toBeNull();
  });
  it("joins adjacent observed periods",()=>{
    expect(chartSegments([period(10),period(11)],"codex","tokens").map(segment=>segment.map(point=>point.value))).toEqual([[120,120]]);
  });
  it("breaks the line across an omitted interval",()=>{
    expect(chartSegments([period(10),period(12)],"codex","tokens").map(segment=>segment.length)).toEqual([1,1]);
  });
  it("breaks the line at missing provider and null metric values instead of inserting zero",()=>{
    const unknown={...codex,total_tokens:null};
    expect(chartSegments([period(10),period(11,[]),period(12),period(13,[unknown]),period(14)],"codex","tokens").map(segment=>segment.map(point=>point.index))).toEqual([[0],[2],[4]]);
  });
  it("keeps a reported zero and excludes unknown costs",()=>{
    const known={...codex,reported_cost_usd:0};
    expect(chartSegments([period(10,[known]),period(11)],"codex","cost").map(segment=>segment.map(point=>point.value))).toEqual([[0]]);
  });
});

describe("usage surfaces",()=>{
  it("shows recorded local scope, source coverage and nullable costs without an estimate",()=>{
    const body=render(RecordedUsage,{props:{summary:summary(),providers:["codex","claude"],metric:"cost"}}).body;
    expect(body).toContain("includes activity outside Panels");
    expect(body).toContain("partial source coverage");
    expect(body).toContain("Read limit reached");
    expect(body).toContain("No recorded cost values");
    expect(body).not.toContain("$0.00");
    expect(body).not.toContain("API estimate");
  });
  it("keeps token detail values known independently of the unknown combined total",()=>{
    const body=render(RecordedUsage,{props:{summary:summary(),providers:["codex","claude"],metric:"tokens"}}).body;
    expect(body).toContain("Unknown model");
    expect(body).toContain("52");
    expect(body).toContain("data-usage-total");
    expect(body).toContain("Unrecorded intervals remain gaps");
  });
  it("labels expired allowance snapshots instead of current remaining allowance",()=>{
    const body=render(AllowanceWindows,{props:{windows:[{kind:"seven_day",model_id:null,used_percent:32,resets_at:"2026-10-11T13:32:16Z"}],observedAt:"2026-10-04T21:31:35Z",now:Date.parse("2026-10-16T18:00:00Z")}}).body;
    expect(body).toContain("68%");
    expect(body).toContain("cached");
    expect(body).toContain("Reset passed");
    expect(body).toContain("Current allowance unavailable");
    expect(body).toContain("Left at observation");
  });
  it("shares the same remaining clamp with conversation rings",()=>{
    expect(remainingPercent(-4)).toBe(100);
    expect(remainingPercent(130)).toBe(0);
    expect(remainingPercent(32)).toBe(68);
  });
  it("keeps Config as canonical navigation and includes every approved period",()=>{
    const body=render(UsageHeader,{props:{metric:"limits",days:1,providerKeys:["codex"],selectedProviders:["codex"],busy:false,onMetric:()=>{},onDays:()=>{},onProviders:()=>{},onRefresh:()=>{}}}).body;
    expect(body).toContain('href="#/settings/config"');
    for(const label of ["24h","7d","30d","90d"])expect(body).toContain(label);
    expect(body).toContain('aria-pressed="true"');
  });
});

describe("explicit native usage refresh",()=>{
  it("sends only the explicit POST without browser-supplied source paths",async()=>{
    const request=vi.fn().mockResolvedValue(new Response(JSON.stringify({sources:[]})));
    vi.stubGlobal("fetch",request);
    await expect(refreshRecordedUsage()).resolves.toEqual({sources:[]});
    expect(request).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledWith("/api/conversation/usage/refresh",expect.objectContaining({method:"POST"}));
    expect(request.mock.calls[0][1].body).toBeUndefined();
  });
  it("propagates a refresh failure without inventing an empty successful reading",async()=>{
    vi.stubGlobal("fetch",vi.fn().mockRejectedValue(new Error("offline")));
    await expect(refreshRecordedUsage()).rejects.toThrow("network error");
  });
});
