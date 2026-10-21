<script lang="ts">
  import type { Instrument, InstrumentId, Trend } from '../../lib/vpsMetrics/contracts';
  import { formatReading, formatPercent } from '../../lib/vpsMetrics/presentation';
  import VpsTrend from './VpsTrend.svelte';
  import VpsSkeleton from './VpsSkeleton.svelte';
  let { instrument, id, title, trend, loading = false }: {
    instrument?: Instrument; id: InstrumentId; title: string; trend?: Trend; loading?: boolean;
  } = $props();
  let wide = $derived(id === 'cpu' || id === 'memory');
  let primary = $derived(instrument ? formatReading(instrument.primary, id === 'cpu' || id === 'disk' ? 0 : id === 'load' ? 1 : undefined) : null);
  let secondary = $derived(instrument?.secondary ? formatReading(instrument.secondary) : null);
  let unit = $derived(id === 'io' ? `${primary?.unit ?? ''} written` : id === 'network' ? `${primary?.unit ?? ''} in` : id === 'disk' ? '% full' : primary?.unit ?? '');
</script>

<article class="instrument" class:instrument--wide={wide} class:instrument--small={!wide} data-state={instrument?.severity ?? 'normal'} data-vps-metric={id} aria-label={title}>
  {#if loading}<div class="trend"><VpsSkeleton shape="trend" /></div>
  {:else if instrument?.primary.value != null && trend}<VpsTrend {trend} ceiling={instrument.ceiling ?? null} />{/if}
  <div class="instrument-body">
    <div class="instrument-top">
      <h2 class="instrument-name">{#if loading}<VpsSkeleton />{:else}{title}{/if}</h2>
      {#if instrument?.primary.value != null && instrument.pill}<span class="pill">{instrument.pill}</span>{/if}
    </div>
    <p class="instrument-value">
      {#if loading}<VpsSkeleton shape="value" />
      {:else if instrument?.primary.value == null}<span class="instrument-unavailable">{instrument?.primary.reason || 'Unavailable'}</span>
      {:else}<span>{primary?.value}</span><span class="instrument-unit">{unit}</span>{/if}
    </p>
    <p class="instrument-of">
      {#if loading}<VpsSkeleton shape="detail" />
      {:else if secondary}{secondary.value}{secondary.unit ? ` ${secondary.unit}` : ''} {id === 'io' ? 'read' : 'out'}
      {:else if instrument}{instrument.details.join(' · ')}{/if}
    </p>
    {#if loading && (id === 'cpu' || id === 'memory' || id === 'io')}<p class="waiting"><VpsSkeleton shape="detail" /></p>
    {:else if instrument?.waiting}
      <p class="waiting">
        {#if instrument.waiting.value !== null}<span class="waiting-track" aria-hidden="true"><span class="waiting-fill" style:width={`${Math.max(0, Math.min(100, instrument.waiting.value))}%`}></span></span>{/if}
        <span>{instrument.waiting.value === null ? `waiting ${instrument.waiting.reason || 'unavailable'}` : `waiting ${formatPercent(instrument.waiting.value)}`}</span>
      </p>
    {/if}
  </div>
</article>
