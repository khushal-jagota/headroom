<script lang="ts">
  // Svelte/query translation of T3 mobile UsageRouteScreen and runtime/state/usage.
  import { onMount } from "svelte";
  import { createQueries, createQuery, useQueryClient } from "@tanstack/svelte-query";
  import SkeletonRegion from "../components/SkeletonRegion.svelte";
  import MachineChoice from "../components/machines/MachineChoice.svelte";
  import UsageHeader from "../components/usage/UsageHeader.svelte";
  import RecordedUsage from "../components/usage/RecordedUsage.svelte";
  import UsageLimits from "../components/usage/UsageLimits.svelte";
  import type { UsageTab } from "../lib/usageAddress";
  import { prefixHref, type UtilityHref } from "../lib/utilityAddress";
  import { queries } from "../lib/queryCatalogue";
  import { type UsageDays } from "../lib/conversation/recordedUsageWire";
  import { refreshBackendSnapshots } from "../lib/conversation/backendRefresh";
  import { refreshBackends, type BackendUsageRefreshOutcome } from "../lib/conversation/wire";
  import { refreshUsage } from "../lib/usage/usage";
  import { makeWindow } from "../lib/usage/usageFormat";
  import { mergeUsageSummaries, type MachineUsage } from "../lib/usage/usageMerge";
  let { tab = "limits", href = prefixHref("#/backends") }: {
    tab?: UsageTab; href?: UtilityHref;
  } = $props();
  const queryClient = useQueryClient();
  const machines = createQuery(() => queries.machines());
  let selectedMachineId = $state<string | null>(null);
  const machine = $derived(machines.data?.machines.find(item => selectedMachineId === null ? item.is_home : item.machine_id === selectedMachineId));
  const driversQuery = createQuery(() => queries.drivers());
  // Usage names, colours and stacking come from the driver list, so the usage body waits
  // for it. A failed list falls back to each key's own name and the neutral colour.
  const drivers = $derived(driversQuery.data?.drivers ?? (driversQuery.isError ? [] : undefined));
  let days = $state<UsageDays>(1);
  let windowSelection = $state(makeWindow(1, new Date(), "hour"));
  let providerSelection = $state<string[] | null>(null);
  // Tokens and Cost add every machine's recorded usage. The home read never waits for
  // the machine list or a joined machine; each joined answer joins the totals as it lands.
  const recordedTab = $derived(tab === "tokens" || tab === "cost");
  const homeMachine = $derived(machines.data?.machines.find(item => item.is_home));
  const joinedMachines = $derived(machines.data?.machines.filter(item => !item.is_home) ?? []);
  const recorded = createQuery(() => ({ ...queries.recordedUsage(windowSelection), enabled: recordedTab, retry: false }));
  const joinedRecorded = createQueries(() => ({ queries: joinedMachines.map(item => ({
    // The machine list polls. A failed machine is asked again on Refresh, not on each poll.
    ...queries.recordedUsage(windowSelection, item.machine_id), enabled: recordedTab, retryOnMount: false })) }));
  const merged = $derived(mergeUsageSummaries([
    ...(recorded.data ? [{ machineId: homeMachine?.machine_id ?? "", name: homeMachine?.name ?? "", isHome: true, summary: recorded.data }] : []),
    ...joinedMachines.flatMap((item, index): MachineUsage[] => {
      const summary = joinedRecorded[index]?.data;
      return summary ? [{ machineId: item.machine_id, name: item.name, isHome: false, summary }] : [];
    }),
  ]));
  const summary = $derived(merged?.summary ?? null);
  const machineNotices = $derived(joinedMachines.flatMap((item, index) => {
    const read = joinedRecorded[index];
    if (!read?.error) return [];
    return [{ machineId: item.machine_id, text: read.data
      ? `${item.name} could not refresh. ${read.error.message} Showing its saved totals.`
      : `${item.name} is not in these totals. ${read.error.message}` }];
  }));
  const limits = createQuery(() => ({ ...queries.usageLimits(machine?.machine_id ?? ""), enabled: tab === "limits" && machine !== undefined, retry: false }));
  const allowance = $derived(limits.data ?? {});
  const providerKeys = $derived(tab === "limits" ? Object.keys(allowance) : [...new Set(summary?.sources.map(source => source.fingerprint.provider) ?? [])]);
  const selectedProviders = $derived(providerSelection === null ? providerKeys : providerKeys.filter(provider => providerSelection?.includes(provider)));
  let refreshing = $state(false);
  let refreshOutcomes = $state<BackendUsageRefreshOutcome[]>([]);
  let refreshError = $state<string | null>(null);
  let now = $state(Date.now());
  function selectWindow(value: UsageDays) {
    days = value;
    windowSelection = makeWindow(value, new Date(), value === 1 ? "hour" : "day");
  }
  function selectMachine(machineId: string) {
    selectedMachineId = machineId;
    providerSelection = null;
    refreshOutcomes = [];
    refreshError = null;
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    refreshError = null;
    try {
      if (tab === "limits") {
        if (!machine) return;
        const machineId = machine.machine_id;
        const result = await refreshBackendSnapshots(() => refreshBackends(machineId));
        refreshOutcomes = result.outcomes;
        refreshError = result.error;
        await limits.refetch({ throwOnError: true });
      } else {
        const nextWindow = makeWindow(days, new Date(), days === 1 ? "hour" : "day");
        await refreshUsage(async () => {
          // Fetch before selecting the window so its observer reuses this result.
          // A joined machine rescans alongside and never holds the home answer back.
          for (const item of joinedMachines)
            void queryClient.prefetchQuery({ ...queries.recordedUsage(nextWindow, item.machine_id), staleTime: 0 });
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
      <div class="limits-machine">
        {#if machine}<MachineChoice machines={machines.data?.machines ?? []} value={machine.machine_id} disabled={refreshing} onchange={selectMachine} />
        {:else if machines.error}<p class="error" role="alert">Could not read machines.</p>
        {:else}<span role="status">Loading machines…</span>{/if}
      </div>
      {#if limits.error}<p class="error" role="alert">Could not refresh limits. The last successful observation stays available.</p>{/if}
      <SkeletonRegion shape="rows" loading={drivers === undefined || (limits.isPending && !Object.keys(allowance).length && !limits.error)} loadingText="Reading subscription limits…">
        {#if drivers !== undefined}<UsageLimits limits={allowance} providers={selectedProviders} outcomes={refreshOutcomes} {now} {drivers} />{/if}
      </SkeletonRegion>
    {:else}
      {#if recorded.error}<p class="error" role="alert">Usage unavailable{recorded.data ? " · showing saved totals" : ""}</p>{/if}
      {#each machineNotices as notice (notice.machineId)}<p class="error" role="alert">{notice.text}</p>{/each}
      <SkeletonRegion shape="rows" loading={drivers === undefined || (recorded.isPending && !summary && !recorded.error)} loadingText="Scanning provider transcripts…">
        {#if summary && drivers !== undefined}<RecordedUsage {summary} sources={merged?.sources} input={windowSelection} providers={selectedProviders} metric={tab} {drivers} />{/if}
      </SkeletonRegion>
    {/if}
</section>
<style>
  .limits-machine { margin-bottom:var(--page-block-gap); }
  .backends-page { width:100%; max-width:var(--page-measure); min-width:0; margin-inline:auto; padding-block:var(--page-top) var(--space-7); }.error { color:var(--accent-error); font-size:var(--type-body); }
</style>
