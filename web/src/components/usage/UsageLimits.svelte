<script lang="ts">
  // Local HTTP presentation replaces T3's connected-environment presentation atoms.
  // Pool operations and card content follow UsageLimitsPooled.tsx:69–257.
  import { collectLimitAccounts, collectLimitPools, formatDuration, formatResetsIn, limitsNotice, remainingPercent } from "../../lib/usage/usageLimits";
  import { limitPresentations, type MachineUsageLimits, type MachineUsageRefreshOutcome } from "../../lib/usage/machineLimits";
  import { usageSourceColor, usageSourceLabel } from "../../lib/usage/usageProviders";
  import type { DriverPresentation } from "../../lib/drivers/contracts";
  import AllowanceWindows from "./AllowanceWindows.svelte";
  import { backendUsageOutcomeLabel } from "../../lib/conversation/backendRefresh";
  let { readings, providers, now, outcomes = [], drivers }: { readings: readonly MachineUsageLimits[]; providers: readonly string[]; now: number; outcomes?: readonly MachineUsageRefreshOutcome[]; drivers: readonly DriverPresentation[] | undefined } = $props();
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
  const name = (key: string): string => usageSourceLabel(key, drivers);
  const shown = $derived(readings.filter(reading => providers.includes(reading.backend_key)));
  // One account signed in on several machines is one entry: its freshest reading, every machine named.
  const accounts = $derived(collectLimitAccounts(limitPresentations(shown)));
  const pools = $derived(collectLimitPools(accounts, now));
  const failed = $derived(outcomes.filter(outcome => outcome.outcome !== "succeeded"));
  const accountName = (account: (typeof accounts)[number]): string => account.email ?? account.displayName ?? name(account.driver);
</script>
<div class="limits" data-usage-limits>
  {#if failed.length}<div class="notices">
    {#each failed as outcome (`${outcome.machine_id}:${outcome.backend_key}`)}
      <p class="notice" role="status" data-allowance-outcome={outcome.outcome}>{outcome.machine_name} · {name(outcome.backend_key)}: {backendUsageOutcomeLabel(outcome.outcome)}</p>
    {/each}
  </div>{/if}
  {#if pools.length === 0}<p class="notice">No provider on any machine reports subscription limits.</p>{/if}
  {#each pools as pool (pool.driver)}
    {@const color = usageSourceColor(pool.driver, drivers)}
    <section><div class="provider-heading"><h2>{name(pool.driver)}</h2>
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
                <button type="button" onclick={() => openAccount = `${pool.driver}:${window.id}:${index}`} class="segment" aria-label={`Segment ${index + 1}, ${accountName(column.account)}, ${remainingPercent(column.window)}% left`} style:--provider-color={color} style:--remaining={`${remainingPercent(column.window)}%`}><span>{index + 1}</span></button>
              {:else}<span class="gap"></span>{/if}
            {/each}
          </div>
          {#each window.columns as column, index (column.account.key)}
            {#if column.window}
              {@const credits = column.account.limits.resetCredits?.availableCount ?? 0}
              {@const resetsIn = formatResetsIn(column.window, now)}
              <details open={openAccount === `${pool.driver}:${window.id}:${index}`}>
                <summary><span class="index">{index + 1}</span><span class="account-name">{accountName(column.account)}</span><strong>{remainingPercent(column.window)}%</strong><small>{resetsIn?.replace("resets in ", "↻ ") ?? ""}{credits ? ` · ${credits} reset credits banked` : ""}</small></summary>
                <div class="detail"><AllowanceWindows windows={[column.window]} {now} {color} />{#if column.account.plan}<p>Plan {column.account.plan}</p>{/if}<p>Signed in: {column.account.environments.map(environment => environment.label).join(", ")}</p><p>Observed {column.account.limits.checkedAt}</p>{#if column.window.resetsAt}<p>Reset {column.window.resetsAt}</p>{/if}</div>
              </details>
            {/if}
          {/each}
        </article>
      {/each}
    </div></section>
  {/each}
  {#each shown as reading (`${reading.machine_id}:${reading.backend_key}`)}
    {@const notice = limitsNotice(reading.limits)}
    {#if notice}<p class="notice">{reading.machine_name} · {name(reading.backend_key)}: {notice}</p>
      <p class="observed">{reading.machine_name} · {name(reading.backend_key)} · Observed <time datetime={reading.limits.checkedAt}>{observedLabel(reading.limits.checkedAt)}</time></p>
    {/if}
  {/each}
</div>
<style>
  .notices { display:grid; gap:var(--space-1); }.notices p { margin:0; }
  .provider-heading { display:flex; align-items:baseline; flex-wrap:wrap; gap:var(--space-2) var(--space-3); margin-bottom:var(--space-3); }.provider-heading h2 { margin:0; }.limits { display:grid; gap:var(--space-5); }h2 { font-size:var(--type-page-section); margin:0 0 var(--space-3); font-weight:var(--weight-page-section); }h3 { font-size:var(--type-body); margin:0 0 var(--space-2); font-weight:var(--weight-page-section); }.windows { display:grid; gap:var(--space-3); grid-template-columns:repeat(auto-fit,minmax(min(100%,20rem),1fr)); }.window-card { padding:var(--page-card-pad); display:grid; align-content:start; gap:var(--space-3); background:var(--page-card-fill); border-radius:var(--page-card-radius); }.row { display:flex; justify-content:space-between; gap:var(--space-3); }.heading strong { font-size:var(--page-reading); color:var(--text-strong); }strong { font-variant-numeric:tabular-nums; }.heading span,small,.notice,.observed { color:var(--tone-tertiary); }small,.observed { font-size:var(--type-small); }.segments { display:flex; gap:var(--space-1); }.segment,.gap { flex:1; min-width:0; height:28px; border:0; cursor:pointer; }.segment { display:grid; place-items:center; position:relative; border-radius:var(--radius-sm); background:var(--surface-recessed); overflow:hidden; color:var(--text-default); text-decoration:none; }.segment::before { content:""; position:absolute; inset:0; width:var(--remaining); background:var(--provider-color); opacity:.35; }.segment::after { content:""; position:absolute; inset:0 0 0 var(--remaining); background:repeating-linear-gradient(-45deg,transparent,transparent 4px,var(--provider-color) 4px,var(--provider-color) 5px); opacity:.22; }.segment span { position:relative; z-index:1; font-size:var(--type-small); }summary { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2); min-height:44px; cursor:pointer; font-size:var(--type-body); }summary .account-name { flex:1; }.index { padding:var(--space-1); background:var(--surface-recessed); border-radius:var(--radius-sm); font-size:var(--type-small); }.detail { padding:var(--space-3) 0; }.detail p { color:var(--text-faint); font-size:var(--type-small); overflow-wrap:anywhere; }.observed { margin:0; color:var(--tone-tertiary); overflow-wrap:anywhere; }button:focus-visible,summary:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--space-1); }
</style>
