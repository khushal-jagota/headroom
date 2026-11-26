<script lang="ts">
  // Svelte/query translation of T3 mobile UsageRouteScreen and runtime/state/usage.
  import { onMount } from "svelte";
  import { createQuery, useQueryClient } from "@tanstack/svelte-query";
  import SkeletonRegion from "../components/SkeletonRegion.svelte";
  import UsageHeader from "../components/usage/UsageHeader.svelte";
  import RecordedUsage from "../components/usage/RecordedUsage.svelte";
  import UsageLimits from "../components/usage/UsageLimits.svelte";
  import type { UsageTab } from "../lib/usageAddress";
  import { prefixHref, type UtilityHref } from "../lib/utilityAddress";
  import { queries } from "../lib/queryCatalogue";
  import { type UsageDays } from "../lib/conversation/recordedUsageWire";
  import { refreshBackendSnapshots } from "../lib/conversation/backendRefresh";
  import { refreshBackends } from "../lib/conversation/wire";
  import { refreshMachineLimits, type MachineUsageRefreshOutcome } from "../lib/usage/machineLimits";
  import { refreshUsage } from "../lib/usage/usage";
  import { makeWindow } from "../lib/usage/usageFormat";
  let { tab = "limits", href = prefixHref("#/backends") }: {
    tab?: UsageTab; href?: UtilityHref;
  } = $props();
  const queryClient = useQueryClient();
  // Limits belong to provider accounts, not machines: every machine's readings show together.
  const machines = createQuery(() => ({...queries.machines(), enabled: tab === "limits"}));
  const driversQuery = createQuery(() => queries.drivers());
  // Usage names, colours and stacking come from the driver list, so the usage body waits
  // for it. A failed list falls back to each key's own name and the neutral colour.
  const drivers = $derived(driversQuery.data?.drivers ?? (driversQuery.isError ? [] : undefined));
  let days = $state<UsageDays>(1);
  let windowSelection = $state(makeWindow(1, new Date(), "hour"));
  let providerSelection = $state<string[] | null>(null);
  const recorded = createQuery(() => ({ ...queries.recordedUsage(windowSelection), enabled: tab === "tokens" || tab === "cost", retry: false }));
  const summary = $derived(recorded.data ?? null);
  const limits = createQuery(() => ({ ...queries.usageLimits(), enabled: tab === "limits", retry: false }));
  const readings = $derived(limits.data?.readings ?? []);
  const providerKeys = $derived([...new Set(tab === "limits" ? readings.map(reading => reading.backend_key) : summary?.sources.map(source => source.fingerprint.provider) ?? [])]);
  const selectedProviders = $derived(providerSelection === null ? providerKeys : providerKeys.filter(provider => providerSelection?.includes(provider)));
  let refreshing = $state(false);
  let refreshOutcomes = $state<MachineUsageRefreshOutcome[]>([]);
  let machineNotices = $state<string[]>([]);
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
        // Every available machine refreshes at once. An unavailable one keeps its last reading.
        const known = (await machines.refetch({ throwOnError: true })).data?.machines ?? [];
        const result = await refreshMachineLimits(known, machineId => refreshBackendSnapshots(() => refreshBackends(machineId)));
        refreshOutcomes = result.outcomes;
        machineNotices = result.notices;
        await limits.refetch({ throwOnError: true });
      } else {
        const nextWindow = makeWindow(days, new Date(), days === 1 ? "hour" : "day");
        await refreshUsage(async () => {
          // Fetch before selecting the window so its observer reuses this result.
          await queryClient.fetchQuery({ ...queries.recordedUsage(nextWindow), staleTime: 0 });
          windowSelection = nextWindow;
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
  <UsageHeader {tab} {href} {days} {providerKeys} {selectedProviders} busy={refreshing || recorded.isFetching || limits.isFetching} {drivers}
    onDays={selectWindow} onProviders={value => providerSelection = value} onRefresh={() => void refresh()} />
    {#if refreshError}<p class="error" role="alert">{refreshError}</p>{/if}
    {#if tab === "limits"}
      {#if machineNotices.length}<div class="machine-notices">{#each machineNotices as notice, index (index)}<p class="notice" role="status">{notice}</p>{/each}</div>{/if}
      {#if limits.error}<p class="error" role="alert">Could not refresh limits. The last successful observation stays available.</p>{/if}
      <SkeletonRegion shape="rows" loading={drivers === undefined || (limits.isPending && !readings.length && !limits.error)} loadingText="Reading subscription limits…">
        {#if drivers !== undefined}<UsageLimits {readings} providers={selectedProviders} outcomes={refreshOutcomes} {now} {drivers} />{/if}
      </SkeletonRegion>
    {:else}
      {#if recorded.error}<p class="error" role="alert">Usage unavailable{summary ? " · showing saved totals" : ""}</p>{/if}
      <SkeletonRegion shape="rows" loading={drivers === undefined || (recorded.isPending && !summary && !recorded.error)} loadingText="Scanning provider transcripts…">
        {#if summary && drivers !== undefined}<RecordedUsage {summary} input={windowSelection} providers={selectedProviders} metric={tab} {drivers} />{/if}
      </SkeletonRegion>
    {/if}
</section>
<style>
  .machine-notices { display:grid; gap:var(--space-1); margin-bottom:var(--space-5); }.notice { margin:0; color:var(--tone-tertiary); font-size:var(--type-body); }
  .backends-page { width:100%; max-width:var(--page-measure); min-width:0; margin-inline:auto; padding-block:var(--page-top) var(--space-7); }.error { color:var(--accent-error); font-size:var(--type-body); }
</style>
