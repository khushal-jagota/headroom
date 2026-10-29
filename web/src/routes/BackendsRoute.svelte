<script lang="ts">
  // Svelte/query translation of T3 mobile UsageRouteScreen and runtime/state/usage.
  import { onMount, tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import SkeletonRegion from "../components/SkeletonRegion.svelte";
  import UsageHeader from "../components/usage/UsageHeader.svelte";
  import RecordedUsage from "../components/usage/RecordedUsage.svelte";
  import UsageLimits from "../components/usage/UsageLimits.svelte";
  import ConfigRoute from "./ConfigRoute.svelte";
  import type { ConfigSelection } from "../lib/shellRouting";
  import type { UsageTab } from "../lib/usageAddress";
  import { nestedHref, prefixHref, type UtilityHref } from "../lib/utilityAddress";
  import { queries } from "../lib/queryCatalogue";
  import { readUsageLimits, type UsageDays, type UsageProvider } from "../lib/conversation/recordedUsageWire";
  import { refreshUsage } from "../lib/usage/usage";
  import { makeWindow } from "../lib/usage/usageFormat";
  import type { ServerProviderUsageLimits } from "../lib/usage/contracts";
  let { tab = "limits", roleKind = "index", roleId, href = prefixHref("#/backends") }: {
    tab?: UsageTab; roleKind?: ConfigSelection["roleKind"]; roleId?: string; href?: UtilityHref;
  } = $props();
  let days = $state<UsageDays>(1);
  let windowSelection = $state(makeWindow(1, new Date(), "hour"));
  let providerSelection = $state<string[] | null>(null);
  const recorded = createQuery(() => ({ ...queries.recordedUsage(windowSelection), enabled: tab === "tokens" || tab === "cost", retry: false }));
  const summary = $derived(recorded.data ?? null);
  const limits = createQuery(() => ({ queryKey: ["usage-limits", summary?.readAt ?? null], queryFn: readUsageLimits, enabled: tab === "limits", retry: false }));
  let lastLimits = $state<Readonly<Record<string, ServerProviderUsageLimits>>>({});
  $effect(() => { if (limits.data) lastLimits = limits.data; });
  const allowance = $derived(limits.data ?? lastLimits);
  const providerKeys = $derived(tab === "limits" ? Object.keys(allowance) : [...new Set(summary?.sources.map(source => source.fingerprint.provider) ?? [])]);
  const selectedProviders = $derived(providerSelection === null ? providerKeys : providerKeys.filter(provider => providerSelection?.includes(provider)));
  const recordedProviders = $derived(selectedProviders.filter((provider): provider is UsageProvider => provider === "codex" || provider === "claude" || provider === "grok"));
  let refreshing = $state(false);
  let refreshError = $state<string | null>(null);
  let now = $state(Date.now());
  function selectWindow(value: UsageDays) {
    days = value;
    windowSelection = makeWindow(value, new Date(), value === 1 ? "hour" : "day");
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    refreshError = null;
    try {
      if (tab === "limits") {
        // The local limits endpoint completes a local scan before it returns.
        await limits.refetch({ throwOnError: true });
      } else {
        const nextWindow = makeWindow(days, new Date(), days === 1 ? "hour" : "day");
        await refreshUsage(async () => {
          windowSelection = nextWindow;
          await tick();
          await recorded.refetch({ throwOnError: true });
        });
      }
      now = Date.now();
    } catch (problem) {
      refreshError = problem instanceof Error ? problem.message : "Usage could not refresh.";
    } finally { refreshing = false; }
  }
  onMount(() => { const timer = setInterval(() => now = Date.now(), 60_000); return () => clearInterval(timer); });
</script>
<section class="backends-page" data-screen="backends">
  <UsageHeader {tab} {href} {days} {providerKeys} {selectedProviders} busy={refreshing || recorded.isFetching || limits.isFetching}
    onDays={selectWindow} onProviders={value => providerSelection = value} onRefresh={() => void refresh()} />
  {#if tab === "config"}
    {#key `${roleKind}/${roleId ?? ""}`}<ConfigRoute {roleKind} {roleId} href={nestedHref(href, ["config"])} />{/key}
  {:else}
    {#if refreshError}<p class="error" role="alert">{refreshError}</p>{/if}
    {#if tab === "limits"}
      {#if limits.error}<p class="error" role="alert">Could not refresh limits. Showing the last known values.</p>{/if}
      <SkeletonRegion shape="rows" loading={limits.isPending && !Object.keys(allowance).length && !limits.error} loadingText="Reading local subscription limits…">
        <UsageLimits limits={allowance} providers={selectedProviders} {now} />
      </SkeletonRegion>
    {:else}
      {#if recorded.error}<p class="error" role="alert">Usage unavailable{summary ? " · showing saved totals" : ""}</p>{/if}
      <SkeletonRegion shape="rows" loading={recorded.isPending && !summary && !recorded.error} loadingText="Scanning provider transcripts…">
        {#if summary}<RecordedUsage {summary} input={windowSelection} providers={recordedProviders} metric={tab} />{/if}
      </SkeletonRegion>
    {/if}
  {/if}
</section>
<style>
  .backends-page { max-width:70rem; margin:0 auto; padding:var(--space-5); }.error { color:var(--accent-error); font-size:var(--type-sm); }@media(max-width:600px) { .backends-page { padding:var(--space-3); } }
</style>
