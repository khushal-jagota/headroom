<script lang="ts">
  import { onMount } from 'svelte';
  import type { InstrumentId, MetricsController, VpsMetricsState, WindowId, WorkloadRow } from '../../lib/vpsMetrics/contracts';
  import { createMetricsController } from '../../lib/vpsMetrics/controller';
  import { metricsConfig } from '../../lib/vpsMetrics/config';
  import { freshness, instruments, machineDescription, processRows, serviceRows } from '../../lib/vpsMetrics/presentation';
  import VpsInstrument from './VpsInstrument.svelte';
  import VpsWorkloadRows from './VpsWorkloadRows.svelte';
  import VpsSkeleton from './VpsSkeleton.svelte';
  import './vps.css';

  let { visible = true }: { visible?: boolean } = $props();
  let metricsState = $state.raw<VpsMetricsState | null>(null);
  let controller = $state.raw<MetricsController | null>(null);
  let browserVisible = $state(true);
  let services = $state.raw<WorkloadRow[]>([]), processes = $state.raw<WorkloadRow[]>([]);
  let sample = $derived(metricsState?.latest?.sample ?? null);
  let loading = $derived(metricsState === null || metricsState.loading);
  let current = $derived(freshness(metricsState?.latest ?? null, metricsState?.now ?? Date.now() / 1000, metricsState?.failed ?? false));
  let cards = $derived(sample ? instruments(sample) : []);
  const placeholders: { id: InstrumentId; title: string }[] = [
    { id: 'cpu', title: 'CPU' }, { id: 'memory', title: 'Memory' }, { id: 'load', title: 'Load' },
    { id: 'io', title: 'Disk I/O' }, { id: 'network', title: 'Network' }, { id: 'disk', title: 'Disk' }
  ];
  const windows = Object.entries(metricsConfig.windows) as [WindowId, (typeof metricsConfig.windows)[WindowId]][];
  $effect(() => { controller?.setVisible(visible && browserVisible); });
  onMount(() => {
    const runtime = createMetricsController();
    const unsubscribe = runtime.subscribe(next => {
      if (next.latest?.sample !== metricsState?.latest?.sample && next.latest?.sample) services = serviceRows(next.latest.sample, services.map(row => row.id));
      if (next.processes !== metricsState?.processes && next.processes) processes = processRows(next.processes.processes, next.latest?.sample?.core_count ?? 0, processes.map(row => row.id));
      metricsState = next;
    });
    const syncVisibility = () => { browserVisible = document.visibilityState !== 'hidden'; };
    syncVisibility();
    document.addEventListener('visibilitychange', syncVisibility);
    controller = runtime;
    return () => { unsubscribe(); runtime.destroy(); document.removeEventListener('visibilitychange', syncVisibility); };
  });
</script>

<div class="vps-container">
  <div class="screen" data-screen="vps" data-current={loading ? undefined : !current.stale} aria-busy={loading}>
    <header class="screen-header">
      <div class="screen-header-row">
        <div class="screen-header-title"><h1 class="screen-header-title" data-feedback-page-title>VPS</h1>
          {#if loading}<div class="machine"><VpsSkeleton shape="detail" /></div>{:else if sample}<p class="machine">{machineDescription(sample)}</p>{/if}
        </div>
        <div class="screen-header-meta">
          <p class="asof" data-current={loading ? undefined : !current.stale} role="status" aria-live="polite">
            {#if loading}<VpsSkeleton />{:else}<span class="asof-mark" aria-hidden="true"></span><span>{current.label}</span>{/if}
          </p>
          <div class="seg" role="group" aria-label="Window">
            {#each windows as [id, window]}
              <button type="button" class="opt" data-vps-window={id} aria-pressed={(metricsState?.window ?? '5m') === id} onclick={() => controller?.setWindow(id)}>{window.label}</button>
            {/each}
          </div>
        </div>
      </div>
    </header>
    {#if loading}<p class="vps-accessible" role="status">Reading the machine</p>{/if}
    <div class="instruments">
      {#each placeholders as placeholder (placeholder.id)}
        <VpsInstrument {...placeholder} {loading} instrument={cards.find(card => card.id === placeholder.id)} trend={metricsState?.history[placeholder.id]} />
      {/each}
    </div>
    <VpsWorkloadRows kind="services" rows={services} {loading} unavailable={!loading && !sample ? metricsState?.latest?.collection.reason || 'Services unavailable' : null} />
    <VpsWorkloadRows kind="processes" rows={processes} loading={loading || !!sample && !metricsState?.processes && !metricsState?.processError} unavailable={metricsState?.processes?.availability === 'unavailable' ? metricsState.processes.reason || 'Processes unavailable' : metricsState?.processError && !metricsState.processes ? metricsState.processError : !loading && !sample ? 'Processes unavailable' : null} />
  </div>
</div>
