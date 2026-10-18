<script lang="ts">
  import type { UsageDays } from "../../lib/conversation/recordedUsageWire";
  import { USAGE_PERIODS, providerLabel } from "./usagePresentation";
  import { usageTabHref, type UsageTab } from "../../lib/usageAddress";
  let { tab, days, providerKeys, selectedProviders, busy, onDays, onProviders, onRefresh }: {
    tab: UsageTab; days: UsageDays; providerKeys: readonly string[];
    selectedProviders: readonly string[]; busy: boolean;
    onDays: (days: UsageDays) => void;
    onProviders: (providers: string[]) => void; onRefresh: () => void;
  } = $props();
  function toggle(provider: string, checked: boolean): void {
    onProviders(checked ? [...selectedProviders, provider] : selectedProviders.filter(key => key !== provider));
  }
</script>
<header class="usage-header">
  <div class="breadcrumbs">
    <h1>Usage</h1>
    {#if tab !== "config"}
    <span class="slash" aria-hidden="true">/</span>
    {#if tab !== "limits"}<span class="source">Local CLI sources</span><span class="slash" aria-hidden="true">/</span>{/if}
    <details class="provider-filter">
      <summary>{selectedProviders.length === providerKeys.length ? "All backends" : `${selectedProviders.length} backends`} <span aria-hidden="true">⌄</span></summary>
      <div class="provider-popup">
        {#each providerKeys as provider (provider)}
          <label><input type="checkbox" checked={selectedProviders.includes(provider)} onchange={event => toggle(provider, event.currentTarget.checked)} />{providerLabel(provider)}</label>
        {/each}
        {#if !providerKeys.length}<span>No backends reported</span>{/if}
      </div>
    </details>
    {/if}
  </div>
  <div class="toolbar">
    <nav class="segmented metrics" aria-label="Usage tabs">
      {#each ["tokens", "cost", "limits", "config"] as value}
        <a href={usageTabHref(value as UsageTab)} aria-current={tab === value ? "page" : undefined}>{providerLabel(value)}</a>
      {/each}
    </nav>
    {#if tab !== "config"}
    <div class="segmented periods" role="group" aria-label="Usage period">
      {#each USAGE_PERIODS as period}
        <button aria-pressed={days === period.days} disabled={tab === "limits"} onclick={() => onDays(period.days)}>{period.label}</button>
      {/each}
    </div>
    <select class="mobile-period" aria-label="Usage period" value={days} disabled={tab === "limits"} onchange={event => onDays(Number(event.currentTarget.value) as UsageDays)}>
      {#each USAGE_PERIODS as period}<option value={period.days}>{period.label}</option>{/each}
    </select>
    <button class="refresh" aria-label="Refresh usage and allowances" aria-busy={busy} disabled={busy} onclick={onRefresh} data-backends-refresh title="Refresh local CLI records and provider allowances">{busy ? "…" : "↻"}</button>
    {/if}
  </div>
</header>
<style>
  .usage-header { display:flex; justify-content:space-between; align-items:center; gap:var(--space-4); padding:var(--space-3) 0; border-bottom:var(--border-hairline) solid var(--border-color); margin-bottom:var(--space-6); }
  .breadcrumbs,.toolbar { display:flex; align-items:center; gap:var(--space-3); min-width:0; }
  h1 { margin:0; font:600 var(--type-md) var(--font-ui); color:var(--text-strong); }
  .slash,.source { color:var(--text-faintest); font-size:var(--type-xs); white-space:nowrap; }
  button,a,summary,select { font:inherit; font-size:var(--type-xs); color:var(--text-faint); }
  button { cursor:pointer; border:0; background:none; }
  button:disabled,select:disabled { cursor:default; opacity:.5; }
  .segmented { display:flex; align-items:center; gap:var(--border-strong); padding:var(--border-strong); background:var(--surface-recessed); border-radius:var(--radius-md); }
  .segmented button,.segmented a { text-decoration:none; padding:var(--space-1) var(--space-2); white-space:nowrap; border-radius:var(--radius-sm); }
  .segmented [aria-pressed="true"],.segmented [aria-current="page"] { background:var(--surface-2); color:var(--text-strong); }
  .segmented button:hover,.segmented a:hover,.refresh:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .refresh { width:var(--space-6); height:var(--space-6); font-size:var(--type-lg); border-radius:var(--radius-sm); flex:none; }
  .provider-filter { position:relative; font-size:var(--type-sm); }
  summary { cursor:pointer; list-style:none; white-space:nowrap; color:var(--text-muted); }
  summary::-webkit-details-marker { display:none; }
  .provider-popup { position:absolute; top:calc(100% + var(--space-3)); right:0; min-width:10rem; padding:var(--space-3); background:var(--surface-2); border:var(--border-hairline) solid var(--border-color); box-shadow:var(--shadow-float); border-radius:var(--radius-md); z-index:5; }
  .provider-popup label { display:flex; align-items:center; gap:var(--space-2); padding:var(--space-2); color:var(--text-default); }
  input { accent-color:var(--text-muted); }
  .mobile-period { display:none; background:none; border:0; }
  :is(button,a,summary,select,input):focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:var(--space-1); }
  @media(max-width:1100px) { .periods { display:none; }.mobile-period { display:block; max-width:calc(var(--space-7) + var(--space-2)); } }
  @media(max-width:760px) { .usage-header { flex-wrap:wrap; gap:var(--space-3); }.breadcrumbs { width:100%; gap:var(--space-2); }.toolbar { width:100%; gap:var(--space-2); }.metrics { flex:1; justify-content:space-between; }.metrics a { padding:var(--space-1) var(--space-2); } }
</style>
