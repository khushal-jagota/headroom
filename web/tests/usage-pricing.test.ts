import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import RecordedUsage from "../src/components/usage/RecordedUsage.svelte";
import { chartSegments, combineMetrics, costSegments, metricValue, speedCostSegments } from "../src/components/usage/usagePresentation";
import type { ProviderUsageMetrics, UsageMetrics, UsageSummary } from "../src/lib/conversation/recordedUsageWire";

function metrics(overrides:Partial<UsageMetrics>={}):UsageMetrics {
  return {
    input_tokens:3_000_000,uncached_input_tokens:1_000_000,cached_input_tokens:1_000_000,
    cache_creation_tokens:1_000_000,output_tokens:1_000_000,reasoning_tokens:500_000,
    total_tokens:4_000_000,reported_cost_usd:null,cost_usd:58.8,estimated_cost_usd:58.8,
    provider_reported_cost_usd:null,category_cost_usd:{input:8,cache_read:0.8,cache_write:10,output:40},
    speed_cost_usd:{standard:0,fast:58.8,ultrafast:0},cache_savings_usd:7.2,speed_premium_usd:29.4,
    unpriced_records:0,model_priced_records:1,provider_reported_records:0,
    record_count:1,session_count:1,unknown_records:0,unknown_cost_records:1,...overrides
  };
}
const codex:ProviderUsageMetrics={...metrics(),provider:"codex"};
function summary():UsageSummary {
  return {
    scope:"local_cli",days:7,timezone:"UTC",since:"2026-10-11T00:00:00Z",until:"2026-10-17T12:00:00Z",
    observed_at:"2026-10-17T11:00:00Z",pricing:{status:"cached",source:"https://example.test/prices",fetched_at:"2026-10-15T12:00:00Z",known_models:1},
    totals:codex,providers:[codex],models:[{...codex,model:"example-model"}],
    periods:[{...codex,start:"2026-10-16T00:00:00Z",end:"2026-10-17T00:00:00Z",label:"2026-10-16",providers:[codex]}],
    sources:[{provider:"codex",state:"partial",observed_at:"2026-10-17T11:00:00Z",files_scanned:3,record_count:1,issues:[],duplicates:0,
      progress:{files_total:10,files_completed:3,bytes_total:1000,bytes_read:300,more_work:true}}]
  };
}

describe("T3 cost presentation",()=>{
  it("uses priced known subtotals and retains separate reported provenance",()=>{
    const unknown=metrics({cost_usd:null,estimated_cost_usd:null,category_cost_usd:null,speed_cost_usd:null,
      cache_savings_usd:null,speed_premium_usd:null,unpriced_records:1,model_priced_records:0});
    const reported=metrics({cost_usd:5,estimated_cost_usd:null,provider_reported_cost_usd:5,reported_cost_usd:5,
      model_priced_records:0,provider_reported_records:1});
    const total=combineMetrics([codex,unknown,reported]);
    expect(total.cost_usd).toBe(63.8);
    expect(total.estimated_cost_usd).toBe(58.8);
    expect(total.provider_reported_cost_usd).toBe(5);
    expect(total.reported_cost_usd).toBeNull();
    expect(total.unpriced_records).toBe(1);
    expect(metricValue(total,"cost")).toBe(63.8);
    expect(metricValue(total,"tokens")).toBe(12_000_000);
  });
  it("keeps an explicit zero distinct from entirely unpriced data",()=>{
    expect(combineMetrics([]).cost_usd).toBeNull();
    expect(combineMetrics([metrics({cost_usd:0})]).cost_usd).toBe(0);
    expect(chartSegments(summary().periods,"codex","cost")[0][0].value).toBe(58.8);
  });
  it("uses the reference's cost categories and speed premium without reasoning duplication",()=>{
    const values=costSegments(codex);
    expect(values.map(row=>row.label)).toEqual(["Input","Cached input","Cache write","Output"]);
    expect(values.reduce((sum,row)=>sum+row.value,0)).toBe(58.8);
    expect(speedCostSegments(codex).map(row=>row.value)).toEqual([0,58.8,0]);
    expect(speedCostSegments(metrics({speed_cost_usd:{standard:5,fast:0,ultrafast:0}}))).toEqual([]);
  });
  it("renders actual cost chart, Totals, cache savings and both type bars",()=>{
    const body=render(RecordedUsage,{props:{summary:summary(),providers:["codex"],metric:"cost"}}).body;
    for(const text of ["$58.80","Totals","Cache savings","$7.20","Cost by type","Cost by speed","$29.40","speed premium","3/10 files","more to read","Model prices","Source-reported USD"])
      expect(body).toContain(text);
    expect(body).not.toContain("reported cost");
    expect(body).not.toContain("Estimated");
    expect(body).not.toContain("billed");
  });
  it("labels unpriced quality without losing the known total",()=>{
    const data=summary();
    data.totals={...data.totals,unpriced_records:2,record_count:3};
    const body=render(RecordedUsage,{props:{summary:data,providers:["codex"],metric:"cost"}}).body;
    expect(body).toContain("$58.80");
    expect(body).toContain("Unpriced");
    expect(body).toContain("2 records");
  });
});
