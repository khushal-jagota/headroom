<script lang="ts">
  import type { UsageProvider, UsageSummary } from "../../lib/conversation/recordedUsageWire";
  import { chartPointerIndex, chartSegments, formatCost, formatTokens, metricValue, providerLabel, timestampLabel } from "./usagePresentation";
  let { summary, providers, metric }: { summary: UsageSummary; providers: readonly UsageProvider[]; metric: "tokens" | "cost" } = $props();
  let selected = $state<number | null>(null);
  let rows = $derived(summary.periods);
  let series = $derived(providers.map(provider => ({ provider, segments:chartSegments(rows, provider, metric) })));
  let peak = $derived(Math.max(0,...series.flatMap(s=>s.segments.flatMap(segment=>segment.map(point=>point.value)))));
  let step = $derived(peak > 0 ? 10 ** Math.floor(Math.log10(peak / 4)) * (peak / 4 / 10 ** Math.floor(Math.log10(peak / 4)) > 5 ? 10 : peak / 4 / 10 ** Math.floor(Math.log10(peak / 4)) > 2 ? 5 : 2) : 1);
  let max = $derived(peak > 0 ? Math.ceil(peak/step)*step : 1);
  let ticks = $derived(Array.from({length:Math.ceil(max/step)+1},(_,i)=>i*step));
  let first = $derived(rows[0] ? Date.parse(rows[0].start) : 0);
  let last = $derived(rows.at(-1) ? Date.parse(rows.at(-1)!.start) : first);
  let inspected = $derived(selected === null ? null : rows[Math.min(selected, rows.length-1)]);
  const format = (value:number|null):string => metric === "tokens" ? formatTokens(value) : formatCost(value);
  const color = (provider:UsageProvider):string => provider === "codex" ? "var(--text-default)" : "var(--text-faintest)";
  const y = (value:number):number => 260-value/max*252;
  const x = (start:string):number => last === first ? 480 : (Date.parse(start)-first)/(last-first)*960;
  const path = (segment:ReturnType<typeof chartSegments>[number]):string => segment.map((point,index)=>`${index ? "L" : "M"}${x(point.period.start)},${y(point.value)}`).join(" ");
  function inspectKey(event:KeyboardEvent):void {
    if (!rows.length || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    selected = event.key === "Home" ? 0 : event.key === "End" ? rows.length-1 : Math.max(0,Math.min(rows.length-1,(selected ?? 0)+(event.key === "ArrowLeft" ? -1 : 1)));
  }
  function inspectPointer(event:MouseEvent):void {
    const target=event.currentTarget;
    if (!(target instanceof HTMLElement)) return;
    selected=chartPointerIndex(rows,event.clientX,target.getBoundingClientRect());
  }
  function inspectClick(event:MouseEvent):void {
    if (event.detail===0 && event.clientX===0 && event.clientY===0) {
      selected=selected??(rows.length?0:null);
    } else inspectPointer(event);
  }
</script>
<div class="chart-shell" data-recorded-usage-chart>
  <div class="chart-head"><span>{summary.days === 1 ? "Hourly" : "Daily"} {metric === "tokens" ? "processed tokens" : "cost"}</span><span>{summary.timezone}</span></div>
  {#if series.some(s=>s.segments.length)}
    <div class="chart-wrap">
      <div class="chart-y" aria-hidden="true">{#each ticks as tick}<span style:top={`${y(tick)/260*100}%`}>{format(tick)}</span>{/each}</div>
      <button class="plot" aria-label="Inspect recorded usage. Use left and right arrow keys for observed periods." onkeydown={inspectKey} onpointerdown={inspectPointer} onpointermove={inspectPointer} onclick={inspectClick}>
        <svg viewBox="0 0 960 260" preserveAspectRatio="none" role="img" aria-label={`${summary.days === 1 ? "Hourly" : "Daily"} recorded ${metric}. Unrecorded intervals remain gaps.`}>
          {#each ticks as tick}<line class="grid" x1="0" x2="960" y1={y(tick)} y2={y(tick)} />{/each}
          {#each series as entry (entry.provider)}
            {#each entry.segments as segment}
              {#if segment.length>1}<path d={path(segment)} fill="none" stroke={color(entry.provider)} stroke-width="2" vector-effect="non-scaling-stroke" />{/if}
              {#each segment as point}<circle cx={x(point.period.start)} cy={y(point.value)} r="3" fill={color(entry.provider)} stroke="var(--surface-1)" stroke-width="1" vector-effect="non-scaling-stroke" />{/each}
            {/each}
          {/each}
          {#if inspected}<line x1={x(inspected.start)} x2={x(inspected.start)} y1="0" y2="260" stroke="var(--text-faint)" stroke-width="1" vector-effect="non-scaling-stroke" />{/if}
        </svg>
      </button>
    </div>
    <div class="chart-x"><span>{rows[0]?.label}</span><span>{rows.length>1 ? rows.at(-1)?.label : ""}</span></div>
    {#if inspected}
      <div class="inspection" aria-live="polite"><span>{timestampLabel(inspected.start,summary.timezone)}</span>{#each providers as provider}{@const entry=inspected.providers.find(row=>row.provider===provider)}<span>{providerLabel(provider)} <strong>{entry ? format(metricValue(entry,metric)) : "—"}</strong></span>{/each}</div>
    {:else}<p class="chart-note">Observed periods only · gaps have no recorded values</p>{/if}
  {:else}<div class="chart-empty">No recorded {metric === "tokens" ? "token values" : "cost values"}</div>{/if}
</div>
<style>
  .chart-shell { min-width:0; }.chart-head { display:flex; justify-content:space-between; gap:var(--space-3); padding-left:var(--space-7); margin-bottom:var(--space-3); color:var(--text-faintest); font-size:var(--type-xs); }
  .chart-wrap { display:grid; grid-template-columns:var(--space-7) minmax(0,1fr); gap:var(--space-2); }.chart-y { position:relative; font-size:var(--type-xs); color:var(--text-faintest); font-variant-numeric:tabular-nums; }.chart-y span { position:absolute; right:0; transform:translateY(-50%); }
  .plot { display:block; min-width:0; width:100%; height:224px; padding:0; border:0; border-radius:var(--radius-sm); color:inherit; background:none; cursor:crosshair; }.plot:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:var(--space-1); }
  svg { display:block; width:100%; height:100%; overflow:visible; }.grid { stroke:var(--border-color); stroke-width:1; vector-effect:non-scaling-stroke; }
  .chart-x { display:flex; justify-content:space-between; gap:var(--space-3); padding-left:calc(var(--space-7) + var(--space-2)); margin-top:var(--space-2); color:var(--text-faintest); font-size:var(--type-xs); }.chart-x span:last-child { text-align:right; }
  .inspection { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:var(--space-3); margin-top:var(--space-3); font-size:var(--type-xs); color:var(--text-faint); }.inspection strong { color:var(--text-default); font-weight:500; font-variant-numeric:tabular-nums; }.chart-note { margin:var(--space-3) 0 0; text-align:right; font-size:var(--type-xs); color:var(--text-faintest); }.chart-empty { min-height:224px; display:grid; place-items:center; color:var(--text-faintest); font-size:var(--type-sm); }
  @media(max-width:580px) { .plot,.chart-empty { height:200px; }.chart-head { flex-wrap:wrap; }.inspection { justify-content:flex-start; } }
</style>
