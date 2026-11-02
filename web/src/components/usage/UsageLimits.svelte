<script lang="ts">
  // Local HTTP presentation replaces T3's connected-environment presentation atoms.
  // Pool operations and card content follow UsageLimitsPooled.tsx:69–257.
  import { ProviderDriverKind, type ServerProviderUsageLimits } from "../../lib/usage/contracts";
  import { collectLimitPools, formatDuration, formatResetsIn, limitsNotice, remainingPercent, type LimitAccount } from "../../lib/usage/usageLimits";
  import { PROVIDER_COLOR } from "../../lib/usage/usageProviders";
  import AllowanceWindows from "./AllowanceWindows.svelte";
  import { backendUsageOutcomeLabel } from "../../lib/conversation/backendRefresh";
  import type { BackendUsageRefreshOutcome } from "../../lib/conversation/wire";
  let { limits, providers, now, outcomes = [] }: { limits: Readonly<Record<string, ServerProviderUsageLimits>>; providers: readonly string[]; now: number; outcomes?: readonly BackendUsageRefreshOutcome[] } = $props();
  let openAccount = $state<string | null>(null);
  const observedFormat = new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC", year: "numeric", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
  function observedLabel(value: string): string {
    const instant = new Date(value);
    return Number.isNaN(instant.getTime()) ? value : observedFormat.format(instant);
  }
  const PACE_LABEL = { ahead: "Ahead of pace", on: "On pace", under: "Under pace" } as const;
  const names: Record<string, string> = { codex: "Codex", claude: "Claude", claudeAgent: "Claude" };
  const entries = $derived(Object.entries(limits).filter(([provider]) => providers.includes(provider)));
  const accounts = $derived(entries.filter(([, value]) => limitsNotice(value) === null).map(([provider, value]): LimitAccount => ({
    key: provider, driver: ProviderDriverKind.make(provider === "claude" ? "claudeAgent" : provider), displayName: names[provider] ?? provider,
    email: undefined, plan: undefined, accentColor: undefined, environments: [], sourceLabel: null, redeem: null, limits: value,
  })));
  const pools = $derived(collectLimitPools(accounts, now));
</script>
<div class="limits" data-usage-limits>
  {#each outcomes.filter(outcome => outcome.outcome !== "succeeded") as outcome (outcome.backend_key)}
    <p class="notice" role="status" data-allowance-outcome={outcome.outcome}>{names[outcome.backend_key] ?? outcome.backend_key}: {backendUsageOutcomeLabel(outcome.outcome)}</p>
  {/each}
  {#if pools.length === 0}<p class="notice">No provider on the selected environments reports subscription limits.</p>{/if}
  {#each pools as pool (pool.driver)}
    {@const color = pool.driver === "claudeAgent" ? PROVIDER_COLOR.claude : PROVIDER_COLOR.codex}
    <section><div class="provider-heading"><h2>{names[pool.driver] ?? pool.driver}</h2>
      {#each accounts.filter(account => account.driver === pool.driver) as account (account.key)}
        <p class="observed">Observed <time datetime={account.limits.checkedAt}>{observedLabel(account.limits.checkedAt)}</time></p>
      {/each}
    </div><div class="windows">
      {#each pool.windows as window (`${window.kind}:${window.id}`)}
        {@const nextRefill = window.resets.find(reset => reset.restoresPercent > 0)}
        <article class="window-card" data-allowance-window={window.kind}>
          <div class="row heading"><div><h3>{window.label}</h3><strong>{window.remainingPercent}%</strong> <span>left</span></div>{#if window.pace}<small>{PACE_LABEL[window.pace]}</small>{/if}</div>
          {#if nextRefill}<small>↻ +{nextRefill.restoresPercent}% {nextRefill.at <= now ? "now" : `in ${formatDuration(nextRefill.at - now)}`}</small>{/if}
          <div class="segments">
            {#each window.columns as column, index (column.account.key)}
              {#if column.window}
                <button type="button" onclick={() => openAccount = `${pool.driver}:${window.id}:${index}`} class="segment" aria-label={`Segment ${index + 1}, ${column.account.displayName}, ${remainingPercent(column.window)}% left`} style:--provider-color={color} style:--remaining={`${remainingPercent(column.window)}%`}><span>{index + 1}</span></button>
              {:else}<span class="gap"></span>{/if}
            {/each}
          </div>
          {#each window.columns as column, index (column.account.key)}
            {#if column.window}
              {@const credits = column.account.limits.resetCredits?.availableCount ?? 0}
              {@const resetsIn = formatResetsIn(column.window, now)}
              <details open={openAccount === `${pool.driver}:${window.id}:${index}`}>
                <summary><span class="index">{index + 1}</span><span class="account-name">{column.account.displayName}</span><strong>{remainingPercent(column.window)}%</strong><small>{resetsIn?.replace("resets in ", "↻ ") ?? ""}{credits ? ` · ${credits} reset credits banked` : ""}</small></summary>
                <div class="detail"><AllowanceWindows windows={[column.window]} {now} {color} /><p>Observed {column.account.limits.checkedAt}</p>{#if column.window.resetsAt}<p>Reset {column.window.resetsAt}</p>{/if}</div>
              </details>
            {/if}
          {/each}
        </article>
      {/each}
    </div></section>
  {/each}
  {#each entries as [provider, value] (provider)}
    {@const notice = limitsNotice(value)}
    {#if notice}<p class="notice">{names[provider] ?? provider}: {notice}</p>
      <p class="observed">{names[provider] ?? provider} · Observed <time datetime={value.checkedAt}>{observedLabel(value.checkedAt)}</time></p>
    {/if}
  {/each}
</div>
<style>
  .provider-heading { display:flex; align-items:baseline; flex-wrap:wrap; gap:var(--space-2) var(--space-3); margin-bottom:var(--space-3); }.provider-heading h2 { margin:0; }.limits { display:grid; gap:var(--space-5); }h2 { font-size:var(--type-md); margin:0 0 var(--space-3); font-weight:500; }h3 { font-size:var(--type-sm); margin:0 0 var(--space-2); font-weight:500; }.windows { display:grid; gap:var(--space-3); grid-template-columns:repeat(auto-fit,minmax(min(100%,20rem),1fr)); }.window-card { padding:var(--space-4); display:grid; align-content:start; gap:var(--space-3); background:var(--surface-1); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-md); }.row { display:flex; justify-content:space-between; gap:var(--space-3); }.heading strong { font-size:var(--type-display); color:var(--text-strong); }strong { font-variant-numeric:tabular-nums; }.heading span,small,.notice,.observed { color:var(--text-faint); }small,.observed { font-size:var(--type-xs); }.segments { display:flex; gap:var(--space-1); }.segment,.gap { flex:1; min-width:0; height:28px; border:0; cursor:pointer; }.segment { display:grid; place-items:center; position:relative; border-radius:var(--radius-sm); background:var(--surface-recessed); overflow:hidden; color:var(--text-default); text-decoration:none; }.segment::before { content:""; position:absolute; inset:0; width:var(--remaining); background:var(--provider-color); opacity:.35; }.segment::after { content:""; position:absolute; inset:0 0 0 var(--remaining); background:repeating-linear-gradient(-45deg,transparent,transparent 4px,var(--provider-color) 4px,var(--provider-color) 5px); opacity:.22; }.segment span { position:relative; z-index:1; font-size:var(--type-xs); }summary { display:flex; align-items:center; gap:var(--space-2); min-height:44px; cursor:pointer; font-size:var(--type-sm); }summary .account-name { flex:1; }.index { padding:var(--space-1); background:var(--surface-recessed); border-radius:var(--radius-sm); font-size:var(--type-xs); }.detail { padding:var(--space-3) 0; }.detail p { color:var(--text-faint); font-size:var(--type-xs); overflow-wrap:anywhere; }.observed { margin:0; color:var(--text-default); }button:focus-visible,summary:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:var(--space-1); }
</style>
