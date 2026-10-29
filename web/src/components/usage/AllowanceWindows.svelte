<script lang="ts">
  // Mechanical translation of T3 mobile UsageLimitsSection.tsx:47–98.
  import type { ServerProviderUsageWindow } from "../../lib/usage/contracts";
  import { ALLOWANCE_COLOR } from "../../lib/usage/usageProviders";
  import { elapsedShare, formatResetsIn, paceOf, remainingPercent } from "../../lib/usage/usageLimits";
  let { windows, now, color }: { windows: readonly ServerProviderUsageWindow[]; now: number; color: string } = $props();
  const PACE_LABEL = { ahead: "ahead of pace", on: "on pace", under: "under pace" } as const;
</script>
<div class="allowances">
  {#each windows as window (window.id)}
    {@const remaining = remainingPercent(window)}
    {@const elapsed = elapsedShare(window, now)}
    {@const timeLeft = elapsed === null ? null : Math.round((1 - elapsed) * 100)}
    {@const pace = paceOf(window, now)}
    {@const resetsIn = formatResetsIn(window, now)}
    <div class="window" data-allowance-window={window.kind}>
      <div class="row"><span>{window.label}</span><strong>{remaining}% left</strong></div>
      <div class="meter" role="meter" aria-label={`${window.label} allowance remaining`} aria-valuenow={remaining} aria-valuemin="0" aria-valuemax="100">
        <div class="bar"><div class="fill" style:width={`${remaining}%`} style:background={remaining <= 10 ? ALLOWANCE_COLOR.low : remaining <= 30 ? ALLOWANCE_COLOR.warning : color}></div></div>
        {#if timeLeft !== null}<span class="pace-marker" style:left={`${timeLeft}%`}></span>{/if}
      </div>
      {#if pace || resetsIn}<div class="row foot"><span>{pace ? PACE_LABEL[pace] : ""}</span><span>{resetsIn ?? ""}</span></div>{/if}
    </div>
  {/each}
</div>
<style>
  .allowances,.window { display:grid; gap:var(--space-3); }.window { gap:var(--space-1); }.row { display:flex; justify-content:space-between; align-items:baseline; gap:var(--space-3); font-size:var(--type-sm); }.row strong { font-weight:500; font-variant-numeric:tabular-nums; }.meter { position:relative; min-height:12px; display:flex; align-items:center; }.bar { height:6px; width:100%; background:var(--surface-recessed); border-radius:var(--radius-sm); overflow:hidden; }.fill { height:100%; border-radius:var(--radius-sm); }.pace-marker { position:absolute; top:0; bottom:0; width:1px; background:var(--text-strong); opacity:.6; }.foot { font-size:var(--type-xs); color:var(--text-faint); }
</style>
