<script lang="ts">
  // Mechanical View-to-div translation of T3 mobile UsageDailyChart.tsx.
  import type { DailyTotals } from "../../lib/usage/usageTotals";
  import { buildChartDays, type UsageChartMetric } from "../../lib/usage/usageChartData";
  import { PROVIDER_COLOR, PROVIDER_LABEL } from "../../lib/usage/usageProviders";
  import { formatTokens, formatUsd, formatDateTimeShort } from "../../lib/usage/usageFormat";
  let { days, daily, metric, height = 180, timeZone }: {
    days: readonly string[]; daily: readonly DailyTotals[]; metric: UsageChartMetric; height?: number; timeZone: string;
  } = $props();
  const chartDays = $derived(buildChartDays(days, daily, metric));
  const max = $derived(chartDays.reduce((peak, day) => Math.max(peak, day.total), 0));
  const ticks = $derived([max, max / 2, 0]);
</script>
<div class="chart" style:height={`${height}px`} role="img" aria-label={`${metric === "cost" ? "Cost" : "Tokens"} by ${days.length === 24 ? "hour" : "day"}`}>
  <div class="plot">
    <div class="grid" aria-hidden="true">{#each ticks as _}<span></span>{/each}</div>
  {#each chartDays as day (day.day)}
    <div class="column" title={`${day.day.includes("T") ? formatDateTimeShort(day.day, timeZone) : day.day}: ${metric === "cost" ? formatUsd(day.total) : formatTokens(day.total)}`} data-usage-period={day.day} data-usage-value={day.total}>
      {#each day.values as entry (entry.provider)}
        <div class="segment" style:height={`${max === 0 ? 0 : (entry.value / max) * height}px`} style:background={PROVIDER_COLOR[entry.provider]}
          title={`${PROVIDER_LABEL[entry.provider]}: ${metric === "cost" ? formatUsd(entry.value) : formatTokens(entry.value)}`}></div>
      {/each}
    </div>
  {/each}
  </div>
  <div class="axis" aria-hidden="true">{#each ticks as tick}<span>{metric === "cost" ? formatUsd(tick) : formatTokens(tick)}</span>{/each}</div>
</div>
<style>
  .chart { display:flex; gap:var(--space-3); min-width:0; }
  .plot { position:relative; display:flex; align-items:flex-end; flex:1; min-width:0; gap:var(--border-hairline); }
  .grid { position:absolute; inset:0; display:flex; flex-direction:column; justify-content:space-between; pointer-events:none; }
  .grid span { border-top:var(--border-hairline) solid var(--chart-grid); }
  .axis { display:flex; flex-direction:column; justify-content:space-between; align-items:flex-end; color:var(--chart-muted); font-size:var(--chart-axis-size); line-height:1; font-variant-numeric:tabular-nums; }
  .column { position:relative; height:100%; flex:1; min-width:0; display:flex; flex-direction:column-reverse; overflow:hidden; border-radius:var(--radius-sm) var(--radius-sm) 0 0; }
  .segment { flex-shrink:0; opacity:.8; }
</style>
