<script lang="ts">
  // Mechanical View-to-div translation of T3 mobile UsageDailyChart.tsx.
  import type { DailyTotals } from "../../lib/usage/usageMerge";
  import { buildChartDays, type UsageChartMetric } from "../../lib/usage/usageChartData";
  import { PROVIDER_COLOR, PROVIDER_LABEL } from "../../lib/usage/usageProviders";
  import { formatTokens, formatUsd, formatDateTimeShort } from "../../lib/usage/usageFormat";
  let { days, daily, metric, height = 180, timeZone }: {
    days: readonly string[]; daily: readonly DailyTotals[]; metric: UsageChartMetric; height?: number; timeZone: string;
  } = $props();
  const chartDays = $derived(buildChartDays(days, daily, metric));
  const max = $derived(chartDays.reduce((peak, day) => Math.max(peak, day.total), 0));
</script>
<div class="chart" style:height={`${height}px`} role="img" aria-label={`${metric === "cost" ? "Cost" : "Tokens"} by ${days.length === 24 ? "hour" : "day"}`}>
  {#each chartDays as day (day.day)}
    <div class="column" title={`${day.day.includes("T") ? formatDateTimeShort(day.day, timeZone) : day.day}: ${metric === "cost" ? formatUsd(day.total) : formatTokens(day.total)}`} data-usage-period={day.day} data-usage-value={day.total}>
      {#each day.values as entry (entry.provider)}
        <div class="segment" style:height={`${max === 0 ? 0 : (entry.value / max) * height}px`} style:background={PROVIDER_COLOR[entry.provider]}
          title={`${PROVIDER_LABEL[entry.provider]}: ${metric === "cost" ? formatUsd(entry.value) : formatTokens(entry.value)}`}></div>
      {/each}
    </div>
  {/each}
</div>
<style>
  .chart { display:flex; align-items:flex-end; gap:1px; }
  .column { height:100%; flex:1; min-width:0; display:flex; flex-direction:column-reverse; overflow:hidden; border-radius:var(--radius-sm); }
  .segment { flex-shrink:0; }
</style>
