<script lang="ts">
  import ManagerHeader from "../ManagerHeader.svelte";
  import Button from "../Button.svelte";
  import type { UsageDays } from "../../lib/conversation/recordedUsageWire";
  import { PROVIDER_LABEL } from "../../lib/usage/usageProviders";
  const USAGE_PERIODS = [{ days: 1, label: "24h" }, { days: 7, label: "7d" }, { days: 30, label: "30d" }, { days: 90, label: "90d" }] as const;
  function providerLabel(value: string): string {
    return ({ tokens: "Tokens", cost: "Cost", limits: "Limits", config: "Config", ...PROVIDER_LABEL } as Record<string, string>)[value] ?? value;
  }
  import type { UsageTab } from "../../lib/usageAddress";
  import type { UtilityHref } from "../../lib/utilityAddress";
  let { tab, href, days, providerKeys, selectedProviders, busy, onDays, onProviders, onRefresh }: {
    tab: UsageTab; href: UtilityHref; days: UsageDays; providerKeys: readonly string[];
    selectedProviders: readonly string[]; busy: boolean;
    onDays: (days: UsageDays) => void;
    onProviders: (providers: string[]) => void; onRefresh: () => void;
  } = $props();
  function toggle(provider: string, checked: boolean): void {
    onProviders(checked ? [...selectedProviders, provider] : selectedProviders.filter(key => key !== provider));
  }
</script>
<header class="usage-header">
  <ManagerHeader>
    {#snippet title()}Usage{/snippet}
    {#snippet eyebrow()}{#if tab !== "limits" && tab !== "config"}<span>Local CLI sources</span>{/if}{/snippet}
    {#snippet actions()}
      {#if tab !== "config"}
        <details class="provider-filter">
          <summary>{selectedProviders.length === providerKeys.length ? "All providers" : `${selectedProviders.length} providers`} <span aria-hidden="true">⌄</span></summary>
          <div class="provider-popup">
            {#each providerKeys as provider (provider)}
              <label><input type="checkbox" checked={selectedProviders.includes(provider)} onchange={event => toggle(provider, event.currentTarget.checked)} />{providerLabel(provider)}</label>
            {/each}
            {#if !providerKeys.length}<span>No providers reported</span>{/if}
          </div>
        </details>
        <Button variant="ghost" aria-label="Refresh usage and allowances" aria-busy={busy} disabled={busy} onclick={onRefresh} data-backends-refresh title="Refresh local usage and allowances">{busy ? "…" : "↻"}</Button>
      {/if}
    {/snippet}
  </ManagerHeader>
  <div class="toolbar">
    <nav class="segmented metrics" aria-label="Usage tabs">
      {#each ["tokens", "cost", "limits", "config"] as value}
        <a class="segmented-option" href={href([value])} aria-current={tab === value ? "page" : undefined}>{providerLabel(value)}</a>
      {/each}
    </nav>
    {#if tab !== "config"}
      <div class="segmented periods" role="group" aria-label="Usage period">
        {#each USAGE_PERIODS as period}
          <button class="segmented-option" aria-pressed={days === period.days} disabled={tab === "limits"} onclick={() => onDays(period.days)}>{period.label}</button>
        {/each}
      </div>
    {/if}
  </div>
</header>
<style>
  .usage-header { margin-bottom:var(--page-block-gap); }
  .toolbar { display:flex; justify-content:space-between; align-items:center; gap:var(--space-3); flex-wrap:wrap; margin-top:var(--page-head-gap); }
  .metrics a { text-decoration:none; }
  .periods button:disabled { opacity:.5; cursor:default; }
  .provider-filter { position:relative; font-size:var(--type-small); color:var(--tone-secondary); }
  summary { cursor:pointer; list-style:none; white-space:nowrap; }
  summary::-webkit-details-marker { display:none; }
  .provider-popup { position:absolute; top:calc(100% + var(--space-3)); right:0; min-width:10rem; padding:var(--space-3); background:var(--surface-2); box-shadow:var(--shadow-float); border-radius:var(--page-card-radius); z-index:5; }
  .provider-popup label { display:flex; align-items:center; gap:var(--space-2); padding:var(--space-2); color:var(--tone-primary); }
  input { appearance:none; width:var(--space-4); height:var(--space-4); margin:0; border:var(--border-hairline) solid var(--tone-tertiary); border-radius:var(--radius-sm); background:var(--surface-input); }
  input:checked { background:var(--tone-primary); border-color:transparent; }
  input:checked::after { content:""; display:block; width:var(--space-2); height:var(--space-1); margin:var(--space-1) auto; border-left:var(--border-strong) solid var(--surface-ink); border-bottom:var(--border-strong) solid var(--surface-ink); transform:rotate(-45deg); }
  :is(a,summary,input):focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--space-1); }
</style>
