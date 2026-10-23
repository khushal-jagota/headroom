<script lang="ts">
  import type { ModelUsageMetrics, UsageProvider, UsageSummary } from "../../lib/conversation/recordedUsageWire";
  import UsageChart from "./UsageChart.svelte";
  import { costSegments, formatCost, formatTokens, metricValue, providerLabel, selectedTotals, speedCostSegments, timestampLabel, tokenSegments } from "./usagePresentation";
  let { summary, providers, metric }: { summary: UsageSummary; providers: readonly UsageProvider[]; metric: "tokens" | "cost" } = $props();
  let breakdown = $state<"model" | "time">("model");
  let selectedModel = $state<string | null>(null);
  let totals = $derived(selectedTotals(summary,providers));
  let providerRows = $derived(summary.providers.filter(row=>providers.includes(row.provider)));
  let models = $derived([...summary.models].filter(row=>providers.includes(row.provider)).sort((a,b)=>(metricValue(b,metric)??-1)-(metricValue(a,metric)??-1)));
  let segments = $derived(tokenSegments(totals));
  let costTypes = $derived(costSegments(totals));
  let speedTypes = $derived(speedCostSegments(totals));
  let shareGroups = $derived(metric === "tokens"
    ? [{ label: "Tokens by type", rows: segments }]
    : [{ label: "Cost by type", rows: costTypes }, ...(speedTypes.length ? [{ label: "Cost by speed", rows: speedTypes }] : [])]);
  let detail = $derived(models.find(row=>`${row.provider}:${row.model}`===selectedModel));
  let sources = $derived(summary.sources.filter(source=>providers.includes(source.provider)));
  let incompleteSources = $derived(sources.filter(source=>source.state!=="ready"));
  const format=(value:number|null):string=>metric==="tokens"?formatTokens(value):formatCost(value);
  function share(value:number|null):number|null {
    const total=metricValue(totals,metric);
    return value!==null && total!==null && total>0 ? value/total*100 : null;
  }
  function toggleModel(row:ModelUsageMetrics):void {
    const key=`${row.provider}:${row.model}`;
    selectedModel=selectedModel===key?null:key;
  }
  function exact(value:number|null):string { return value===null?"Unavailable":value.toLocaleString(); }
</script>
<section class="recorded-usage" data-recorded-usage data-usage-metric={metric}>
  <div class="source-line"><span>{timestampLabel(summary.since,summary.timezone)} – {timestampLabel(summary.until,summary.timezone)}</span></div>
  <section class="usage-top">
    <div class="totals">
      <div><div class="total-figure" title={exact(metricValue(totals,metric))} data-usage-total>{format(metricValue(totals,metric))}</div><p class="total-caption">{metric==="tokens"?"processed tokens":"cost"} · {totals.session_count??"—"} sessions</p>{#if metric==="cost"&&totals.unpriced_records}<p class="unpriced" data-unpriced-records>Unpriced · {totals.unpriced_records.toLocaleString()} {totals.unpriced_records===1?"record":"records"}</p>{/if}</div>
      {#each providerRows as row (row.provider)}
        {@const value=metricValue(row,metric)}{@const percent=share(value)}
        <div class="provider-summary">
          <div class="provider-total"><span><span class="mark" aria-hidden="true">{row.provider==="codex"?"‹›":"✳"}</span>{providerLabel(row.provider)}</span><strong title={exact(value)}>{format(value)}</strong></div>
          <p>{percent===null?"Share unavailable":`${percent.toFixed(1)}%`} · {row.session_count??"—"} {row.session_count===1?"session":"sessions"}</p>
          {#if percent!==null}<div class="share-bar" class:claude={row.provider==="claude"}><span style:width={`${percent}%`}></span></div>{/if}
        </div>
      {/each}
    </div>
    <UsageChart {summary} {providers} {metric}/>
  </section>
  <details class="coverage" data-usage-coverage>
    <summary>{totals.record_count.toLocaleString()} {totals.record_count===1?"record":"records"}{incompleteSources.length?" · partial history":""}{totals.unknown_records?` · ${totals.unknown_records} with unknown tokens`:""} <span aria-hidden="true">⌄</span></summary>
    <div class="coverage-body">
      <p>Local CLI history includes activity outside Panels. Account coverage is unknown.</p>
      {#each sources as source (source.provider)}
        <section><h3>{providerLabel(source.provider)} · {source.state}</h3><p>{source.record_count.toLocaleString()} records · {source.progress?`${source.progress.files_completed.toLocaleString()}/${source.progress.files_total.toLocaleString()} files${source.progress.more_work?" · more to read":""}`:`${source.files_scanned.toLocaleString()} files`} · observed {timestampLabel(source.observed_at,summary.timezone)}</p>{#if source.issues.length}<ul>{#each source.issues as issue}<li>{issue}</li>{/each}</ul>{/if}</section>
      {/each}
      {#if !sources.length}<p>No source data for this selection.</p>{/if}
      {#if metric==="cost"}<section><h3>Model prices · {summary.pricing.status}</h3><p>{summary.pricing.known_models.toLocaleString()} rates · {timestampLabel(summary.pricing.fetched_at,summary.timezone)}</p><p>Source-reported USD {formatCost(totals.provider_reported_cost_usd)} · {totals.provider_reported_records.toLocaleString()} records</p></section>{/if}
    </div>
  </details>
  <section class="usage-totals"><h2>Totals</h2><dl>
    <div><dt>Processed tokens</dt><dd>{formatTokens(totals.total_tokens)}</dd></div>
    <div><dt>Cached input</dt><dd>{formatTokens(totals.cached_input_tokens)}</dd></div>
    <div><dt>Uncached input</dt><dd>{formatTokens(totals.uncached_input_tokens)}</dd></div>
    <div><dt>Output</dt><dd>{formatTokens(totals.output_tokens)}</dd></div>
    <div><dt>Cache savings</dt><dd>{formatCost(totals.cache_savings_usd)}</dd></div>
  </dl></section>
  <div class="share-groups">
    {#each shareGroups as group (group.label)}
      {@const total=group.rows.reduce((sum,row)=>sum+row.value,0)}
      <section class="token-types">
        <h2>{group.label}{#if group.label==="Cost by speed"&&totals.speed_premium_usd!==null&&totals.speed_premium_usd!==0}<span class="speed-premium">+{formatCost(totals.speed_premium_usd)} speed premium</span>{/if}</h2>
        {#if total>0}<div class="type-share" aria-label={group.label}>{#each group.rows as segment}<span style:width={`${segment.value/total*100}%`} style:background={segment.color} title={`${segment.label}: ${format(segment.value)}`}></span>{/each}</div>{/if}
        <div class="type-legend">
          {#if group.rows.length}{#each group.rows as segment}<span><i style:background={segment.color}></i>{segment.label} <b title={exact(segment.value)}>{format(segment.value)}</b></span>{/each}
          {:else if metric==="tokens"}{#each [["Input",totals.uncached_input_tokens],["Cached input",totals.cached_input_tokens],["Cache write",totals.cache_creation_tokens],["Output",totals.output_tokens]] as [label,value]}<span>{label} <b>{formatTokens(value as number|null)}</b></span>{/each}
          {:else}<span>Unavailable</span>{/if}
        </div>
      </section>
    {/each}
  </div>
  <section class="breakdown">
    <header><h2>Breakdown</h2><div class="segmented" role="group" aria-label="Usage breakdown"><button aria-pressed={breakdown==="model"} onclick={()=>breakdown="model"}>Model</button><button aria-pressed={breakdown==="time"} onclick={()=>breakdown="time"}>{summary.days===1?"Hour":"Day"}</button></div></header>
    {#if breakdown==="model"}
      <table><thead><tr><th>#</th><th>Model</th><th>Cost</th><th class="share-column">Share</th><th>Tokens</th></tr></thead><tbody>
        {#each models as row,index (`${row.provider}:${row.model}`)}
          {@const percent=share(metricValue(row,metric))}
          <tr><td>{index+1}</td><td><button class="model-button" aria-expanded={selectedModel===`${row.provider}:${row.model}`} aria-controls="recorded-model-details" onclick={()=>toggleModel(row)}><span class="mark" aria-hidden="true">{row.provider==="codex"?"‹›":"✳"}</span>{row.model??"Unknown model"}</button>{#if percent!==null}<div class="model-bar" aria-hidden="true"><span style:width={`${percent}%`}></span></div>{/if}</td><td title={exact(row.cost_usd)}>{formatCost(row.cost_usd)}</td><td class="share-column">{percent===null?"—":`${percent.toFixed(1)}%`}</td><td title={exact(row.total_tokens)}>{formatTokens(row.total_tokens)}</td></tr>
        {/each}
        {#if !models.length}<tr><td colspan="5" class="empty">No recorded usage for this selection.</td></tr>{/if}
      </tbody></table>
    {:else}
      <table class="time-table"><thead><tr><th>{summary.days===1?"Hour":"Day"}</th>{#each providers as provider}<th>{providerLabel(provider)}</th>{/each}</tr></thead><tbody>
        {#each summary.periods as period (period.start)}<tr><td>{period.label}</td>{#each providers as provider}{@const entry=period.providers.find(row=>row.provider===provider)}<td>{entry?format(metricValue(entry,metric)):"—"}</td>{/each}</tr>{/each}
        {#if !summary.periods.length}<tr><td colspan={providers.length+1} class="empty">No observed periods.</td></tr>{/if}
      </tbody></table>
    {/if}
    {#if detail && breakdown==="model"}
      <section class="model-details" id="recorded-model-details" aria-label="Model token details"><h3>{detail.model??"Unknown model"} · {providerLabel(detail.provider)}</h3><dl>
        {#each [
          ["Input",detail.uncached_input_tokens],["Cached input",detail.cached_input_tokens],
          ["Cache write",detail.cache_creation_tokens],["Output",detail.output_tokens],
          ["Reasoning¹",detail.reasoning_tokens],["Total",detail.total_tokens]
        ] as [label,value]}<div><dt>{label}</dt><dd>{exact(value as number|null)}</dd></div>{/each}
      </dl><p>¹ Reasoning is part of output.</p></section>
    {/if}
  </section>
</section>
<style>
  .unpriced { margin:var(--space-2) 0 0; color:var(--text-faint); font-size:var(--type-xs); }
  .usage-totals { margin-top:var(--space-5); }.usage-totals dl { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:var(--space-4) var(--space-5); margin:var(--space-3) 0 0; }.usage-totals dt { color:var(--text-faintest); font-size:var(--type-xs); }.usage-totals dd { color:var(--text-default); font-size:var(--type-sm); font-variant-numeric:tabular-nums; margin:var(--space-1) 0 0; }
  .share-groups { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-5); }.share-groups:has(>section:only-child) { grid-template-columns:1fr; }.speed-premium { float:right; font-weight:400; font-size:var(--type-xs); }
  .source-line { display:flex; justify-content:space-between; gap:var(--space-4); margin-bottom:var(--space-5); font-size:var(--type-xs); color:var(--text-faintest); }.source-line span:last-child { text-align:right; }
  .usage-top { display:grid; grid-template-columns:18rem minmax(0,1fr); gap:var(--space-5); }.totals { display:flex; flex-direction:column; gap:var(--space-5); }.total-figure { font-size:var(--type-hero); line-height:1.2; font-weight:600; color:var(--text-strong); letter-spacing:var(--tracking-title); font-variant-numeric:tabular-nums; }.total-caption { margin:var(--space-1) 0 0; color:var(--text-faintest); font-size:var(--type-xs); }
  .provider-total { display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-3); color:var(--text-default); font-size:var(--type-sm); }.provider-total>span { display:flex; align-items:center; gap:var(--space-2); }.provider-total strong { font-weight:500; font-variant-numeric:tabular-nums; color:var(--text-strong); }.mark { font-size:var(--type-md); line-height:1; }.provider-summary p { margin:var(--space-1) 0 0; font-size:var(--type-xs); color:var(--text-faintest); }.share-bar,.model-bar { height:var(--space-1); background:var(--surface-recessed); border-radius:var(--radius-pill); overflow:hidden; margin-top:var(--space-2); }.share-bar span,.model-bar span { display:block; height:100%; background:var(--text-default); border-radius:var(--radius-pill); }.share-bar.claude span,.model-bar span { background:var(--text-faintest); }
  .coverage { margin-top:var(--space-4); font-size:var(--type-xs); color:var(--text-faintest); }.coverage summary { cursor:pointer; list-style:none; }.coverage summary::-webkit-details-marker { display:none; }.coverage summary span { margin-left:var(--space-2); }.coverage-body { margin-top:var(--space-3); padding:var(--space-4); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-md); }.coverage-body p { margin:0; }.coverage-body section { margin-top:var(--space-3); }.coverage-body h3 { margin:0 0 var(--space-1); font-size:var(--type-xs); font-weight:500; color:var(--text-muted); }.coverage-body ul { margin:var(--space-2) 0 0; padding-left:var(--space-4); overflow-wrap:anywhere; }
  .token-types { margin-top:var(--space-5); padding-top:var(--space-5); border-top:var(--border-hairline) solid var(--border-color); }h2 { margin:0; font-size:var(--type-sm); font-weight:500; color:var(--text-default); }.token-types h2 { font-size:var(--type-xs); color:var(--text-faint); font-weight:400; margin-bottom:var(--space-3); }.type-share { display:flex; gap:var(--border-strong); height:var(--space-2); border-radius:var(--radius-pill); overflow:hidden; background:var(--surface-recessed); }.type-share span { min-width:var(--border-hairline); }.type-legend { display:flex; flex-wrap:wrap; gap:var(--space-3) var(--space-5); margin-top:var(--space-3); color:var(--text-faint); font-size:var(--type-xs); }.type-legend>span { display:flex; align-items:center; gap:var(--space-2); }.type-legend i { width:var(--space-2); height:var(--space-2); border-radius:var(--radius-sm); display:inline-block; flex:none; }.type-legend b { font-weight:400; font-variant-numeric:tabular-nums; }
  .breakdown { margin-top:var(--space-6); }.breakdown>header { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); margin-bottom:var(--space-3); }.segmented { display:flex; gap:var(--border-strong); padding:var(--border-strong); border-radius:var(--radius-md); background:var(--surface-recessed); }.segmented button { border:0; background:none; border-radius:var(--radius-sm); color:var(--text-faint); font:inherit; font-size:var(--type-xs); padding:var(--space-1) var(--space-3); cursor:pointer; }.segmented [aria-pressed="true"] { background:var(--surface-2); color:var(--text-strong); }
  table { width:100%; border-collapse:collapse; font-size:var(--type-sm); }th { text-align:right; font-size:var(--type-xs); font-weight:400; color:var(--text-faintest); padding:var(--space-2) 0 var(--space-2) var(--space-5); border-bottom:var(--border-hairline) solid var(--border-color); white-space:nowrap; }td { text-align:right; padding:var(--space-4) 0 var(--space-4) var(--space-5); border-bottom:var(--border-hairline) solid var(--border-color); font-variant-numeric:tabular-nums; color:var(--text-faint); white-space:nowrap; vertical-align:top; }th:first-child,td:first-child { padding-left:0; text-align:left; color:var(--text-faintest); font-size:var(--type-xs); }th:nth-child(2),td:nth-child(2) { text-align:left; padding-left:var(--space-3); width:100%; white-space:normal; }.time-table :is(th,td):not(:first-child) { text-align:right; width:auto; }.model-button { display:flex; align-items:center; gap:var(--space-2); max-width:100%; padding:0; border:0; background:none; color:var(--text-default); font:inherit; text-align:left; overflow-wrap:anywhere; cursor:pointer; }.model-button .mark { flex:none; }.model-button:hover { color:var(--text-strong); }.empty { padding:var(--space-5) 0; text-align:left; white-space:normal; }
  .model-details { margin-top:var(--space-3); padding:var(--space-4); background:var(--surface-recessed); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-md); font-size:var(--type-xs); }.model-details h3 { font-size:var(--type-sm); font-weight:500; margin:0 0 var(--space-3); overflow-wrap:anywhere; }.model-details dl { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-2) var(--space-5); margin:0; }.model-details dl>div { display:flex; justify-content:space-between; gap:var(--space-2); }.model-details dt { color:var(--text-faint); }.model-details dd { margin:0; font-variant-numeric:tabular-nums; }.model-details p { margin:var(--space-3) 0 0; color:var(--text-faintest); }
  :is(button,summary):focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:var(--space-1); }
  @media(max-width:1000px) { .usage-top { grid-template-columns:14rem minmax(0,1fr); } }
  @media(max-width:760px) { .source-line { flex-direction:column; gap:var(--space-1); }.source-line span:last-child { text-align:left; }.usage-top { grid-template-columns:1fr; gap:var(--space-6); }.totals { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-4); }.totals>div:first-child { grid-column:1/-1; }.type-legend { gap:var(--space-2) var(--space-4); } }
  @media(max-width:580px) { .share-column { display:none; }table { font-size:var(--type-xs); }th,td { padding-left:var(--space-3); }th:nth-child(2),td:nth-child(2) { padding-left:var(--space-2); }.model-button { gap:var(--space-1); }.model-button .mark { font-size:var(--type-sm); }.type-legend>span { width:calc(50% - var(--space-2)); }.model-details dl { grid-template-columns:1fr; } }
  @media(max-width:760px) { .usage-totals dl { grid-template-columns:repeat(2,minmax(0,1fr)); }.share-groups { grid-template-columns:1fr; } }
</style>
