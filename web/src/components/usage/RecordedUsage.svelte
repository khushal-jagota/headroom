<script lang="ts">
  // Mechanical translation of T3 mobile UsageRouteScreen.tsx:392–620.
  import { type UsageSummary, type UsageSummaryInput, type UsageProviderKind } from "../../lib/usage/contracts";
  import { calculateUsageTotals } from "../../lib/usage/usageTotals";
  import { enumerateDays, enumerateHourStarts, formatUsd, formatTokens, formatPercent, formatCount, formatDayShort, formatHourShort, formatDateTimeShort } from "../../lib/usage/usageFormat";
  import { PROVIDER_LABEL, PROVIDER_COLOR } from "../../lib/usage/usageProviders";
  import UsageChart from "./UsageChart.svelte";
  let { summary, input, providers, metric }: {
    summary: UsageSummary; input: UsageSummaryInput; providers: readonly UsageProviderKind[]; metric: "tokens" | "cost";
  } = $props();
  const selected = $derived({ ...summary,
    buckets: summary.buckets.filter(bucket => providers.includes(bucket.provider)),
    sources: summary.sources.filter(source => providers.includes(source.fingerprint.provider)),
  });
  const totals = $derived(calculateUsageTotals(selected));
  const isPast24Hours = $derived(input.resolution === "hour");
  const days = $derived(isPast24Hours && input.sinceTime && input.untilTime
    ? enumerateHourStarts(input.sinceTime, input.untilTime) : enumerateDays(input.sinceDay, input.untilDay));
  const daily = $derived(isPast24Hours ? totals.hourly.map(hour => ({
    day: hour.hourStart, costUsd: hour.costUsd, totalTokens: hour.totalTokens, byProvider: hour.byProvider,
  })) : totals.daily);
  const hasActivity = $derived(daily.some(period => period.totalTokens > 0));
  const ordered = $derived([...totals.providers].sort((a, b) => metric === "cost" ? b.costUsd - a.costUsd : b.totalTokens - a.totalTokens));
  const activePeriods = $derived((isPast24Hours ? totals.hourly : totals.daily).filter(period => period.totalTokens > 0).length);
  const periodAverage = $derived(activePeriods === 0 ? 0 : totals.totalTokens / activePeriods);
  const observedInput = $derived(totals.uncachedInputTokens + totals.cachedInputTokens);
  const cachedShare = $derived(observedInput === 0 ? 0 : totals.cachedInputTokens / observedInput);
</script>
<div class="recorded" data-recorded-usage>
  <section class="card chart-card">
    <div class="headline">
      <span>{metric === "cost" ? "Raw token cost" : "Processed tokens"}</span>
      <strong data-usage-total>{metric === "cost" ? `${formatUsd(totals.costUsd)}*` : formatTokens(totals.totalTokens)}</strong>
      <span>{metric === "cost" ? "* if billed at full API rate" : `Across ${formatCount(totals.sessions)} sessions`}</span>
    </div>
    {#if hasActivity}<UsageChart {days} {daily} {metric} timeZone={input.timeZone} />
    {:else}<p class="empty-chart">No activity in this window.</p>{/if}
    <div class="legend-line">
      <span>{isPast24Hours ? formatHourShort(days[0] ?? "", input.timeZone) : formatDayShort(input.sinceDay)}</span>
      <div class="legend">{#each totals.providers as provider (provider.provider)}<span><i style:background={PROVIDER_COLOR[provider.provider]}></i>{PROVIDER_LABEL[provider.provider]}</span>{/each}</div>
      <span>{isPast24Hours ? formatHourShort(days[days.length - 1] ?? "", input.timeZone) : formatDayShort(input.untilDay)}</span>
    </div>
  </section>
  {#if ordered.length}
    <section class="card"><h2>Providers</h2>
      {#each ordered as provider (provider.provider)}
        {@const share = metric === "cost" ? provider.costShare : provider.tokenShare}
        <article class="provider-row">
          <div class="row"><span><i style:background={PROVIDER_COLOR[provider.provider]}></i>{PROVIDER_LABEL[provider.provider]}</span><strong>{metric === "cost" ? formatUsd(provider.costUsd) : formatTokens(provider.totalTokens)}</strong></div>
          <div class="bar"><div style:width={`${share * 100}%`} style:background={PROVIDER_COLOR[provider.provider]}></div></div>
          <p>{metric === "cost" ? `${formatPercent(share)} of cost · ${formatTokens(provider.totalTokens)} tokens` : `${formatPercent(share)} of tokens · ${formatUsd(provider.costUsd)}`}</p>
        </article>
      {/each}
    </section>
  {/if}
  <section class="card"><h2>Totals</h2><div class="totals">
    <div><span>Processed tokens</span><strong>{formatTokens(totals.totalTokens)}</strong><small>{formatTokens(periodAverage)} per active {isPast24Hours ? "hour" : "day"}</small></div>
    <div><span>Cache savings</span><strong>{formatUsd(totals.costQuality.cacheSavingsUsd)}</strong><small>{totals.costUsd > 0 ? `${(totals.costQuality.cacheSavingsUsd / totals.costUsd).toFixed(1)}x the raw cost` : "vs full input rates"}</small></div>
    <div><span>Cached input</span><strong>{formatTokens(totals.cachedInputTokens)}</strong><small>{formatPercent(cachedShare)} of observed input</small></div>
    <div><span>Uncached input</span><strong>{formatTokens(totals.uncachedInputTokens)}</strong><small>{formatTokens(totals.cacheCreationTokens)} cache writes</small></div>
    <div><span>Output</span><strong>{formatTokens(totals.outputTokens)}</strong><small>incl. {formatTokens(totals.reasoningTokens)} reasoning</small></div>
    <div><span>Unpriced</span><strong>{formatPercent(totals.costQuality.unpricedShare)}</strong><small>of records, excluded from cost</small></div>
  </div></section>
  {#if totals.models.length}<section class="card"><h2>By model</h2>
    {#each totals.models as model (`${model.provider}:${model.model}`)}
      <article class="model-row"><i style:background={PROVIDER_COLOR[model.provider]}></i><div><span>{model.model}</span><small>{formatPercent(model.costShare)} of cost · {formatTokens(model.totalTokens)} tokens</small></div><strong>{formatUsd(model.costUsd)}</strong></article>
    {/each}
  </section>{/if}
  <details class="sources"><summary>Sources · {summary.scanDurationMs} ms</summary>
    <p>Observed {formatDateTimeShort(summary.readAt, input.timeZone)}</p>
    {#each summary.sources as source (`${source.fingerprint.provider}:${source.fingerprint.resolvedHomePath}`)}
      <p>{PROVIDER_LABEL[source.fingerprint.provider]} · {source.status} · {source.scannedFiles} files · {source.distinctSessions} sessions{source.message ? ` · ${source.message}` : ""}</p>
    {/each}
    <p>Model prices: {summary.pricing.status} · {summary.pricing.knownModels} models{summary.pricing.fetchedAt ? ` · ${formatDateTimeShort(summary.pricing.fetchedAt, input.timeZone)}` : ""}</p>
  </details>
</div>
<style>
  .recorded { display:grid; gap:var(--page-block-gap); color:var(--tone-primary); }
  .card { border-radius:var(--page-card-radius); background:var(--page-card-fill); overflow:hidden; }
  .chart-card { padding:var(--page-card-pad); display:grid; gap:var(--space-4); }
  .headline { display:grid; gap:var(--space-1); }.headline span { color:var(--text-faint); font-size:var(--type-body); }.headline strong { color:var(--text-strong); font-size:var(--page-reading); font-weight:var(--weight-page-title); line-height:1.2; font-variant-numeric:tabular-nums; }
  h2 { font-size:var(--type-page-section); color:var(--tone-primary); margin:0; padding:var(--page-card-pad) var(--page-card-pad) 0; font-weight:var(--weight-page-section); }
  .empty-chart { height:180px; display:grid; place-items:center; color:var(--text-faint); }
  .legend-line,.legend,.legend span,.row,.row>span { display:flex; align-items:center; gap:var(--space-2); }.legend-line,.row { justify-content:space-between; }.legend-line { flex-wrap:wrap; color:var(--text-faint); font-size:var(--type-small); }.legend { flex-wrap:wrap; justify-content:center; gap:var(--space-4); }
  i { display:inline-block; width:.6rem; height:.6rem; border-radius:50%; flex-shrink:0; }
  .provider-row { padding:var(--page-card-pad); display:grid; gap:var(--space-2); }.provider-row+.provider-row,.model-row+.model-row { border-top:var(--border-hairline) solid var(--page-card-edge); }.provider-row p { color:var(--text-faint); font-size:var(--type-body); margin:0; }.bar { height:4px; background:var(--surface-recessed); border-radius:var(--radius-sm); overflow:hidden; }.bar>div { height:100%; }
  strong { font-variant-numeric:tabular-nums; color:var(--text-strong); }.totals { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); }.totals>div { display:grid; gap:var(--space-1); padding:var(--page-card-pad); }.totals span,small { color:var(--text-faint); font-size:var(--type-small); }.totals strong { font-size:var(--page-reading); }.model-row { padding:var(--page-card-pad); display:flex; align-items:center; gap:var(--space-3); }.model-row>div { display:grid; gap:var(--space-1); min-width:0; flex:1; }.model-row>div>span { overflow-wrap:anywhere; }.sources { overflow-wrap:anywhere; color:var(--text-faint); font-size:var(--type-small); }
  @media(max-width:600px) { .totals { grid-template-columns:repeat(2,minmax(0,1fr)); }.legend-line { gap:var(--space-1); }.legend { gap:var(--space-2); } }
</style>
