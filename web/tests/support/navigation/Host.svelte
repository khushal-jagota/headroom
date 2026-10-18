<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { RenderObserver } from '../../../src/lib/navigation/visibility';
  import { createNavigationCollection } from '../../../src/lib/navigation/collection';
  import type { NavigationPublication } from '../../../src/lib/navigation/collection';
  import { createNavigationOwner } from '../../../src/lib/navigation/owner';
  import { createNavigationTransport } from '../../../src/lib/navigation/transport';
  import { createNavigationSelectors } from '../../../src/lib/navigation/selectors.svelte';
  import Consumer from './Consumer.svelte';

  const base = createNavigationCollection();
  let publicationHeld = false;
  let deferred: (() => void)[] = [];
  const adapter = { ...base,
    publishSnapshot(...args: Parameters<typeof base.publishSnapshot>) {
      if (publicationHeld) deferred.push(() => base.publishSnapshot(...args));
      else base.publishSnapshot(...args);
    },
    publishBatch(...args: Parameters<typeof base.publishBatch>) {
      if (publicationHeld) deferred.push(() => base.publishBatch(...args));
      else base.publishBatch(...args);
    }
  };
  const realTransport = createNavigationTransport();
  let disconnected = false;
  let closeStream = () => {};
  const transport = { ...realTransport, connect(...args: Parameters<typeof realTransport.connect>) {
    if (disconnected) return () => {};
    closeStream = realTransport.connect(...args); return closeStream;
  }};
  const owner = createNavigationOwner({ adapter, transport });
  let observer: RenderObserver | undefined;
  let route = $state(location.hash);
  function navigate() { route = location.hash; }
  window.addEventListener('hashchange', navigate);
  const selectors = createNavigationSelectors(base);
  let ownerState = $state.raw(owner.getState());
  const unsubscribeOwner = owner.subscribe(state => { ownerState = state; });
  let renderHeld = $state(false);
  let displayed = $state.raw<NavigationPublication | null>(null);
  let status = $state('idle');
  const coherenceErrors: string[] = [];
  const waitResults: unknown[] = [];
  const renderMarks: { revision: string; epoch: string; rendered_ms: number }[] = [];
  $effect(() => {
    const publication = selectors.publication;
    if (publication) {

      if (!renderHeld) displayed = publication;
    }
  });
  const harness = {
    state: () => ({owner:owner.getState().connection, revision:base.getPublication()?.continuity.revision, selector:selectors.publication?.continuity.revision,status,waitResults}), ready: () => Boolean(displayed && observer), ownerIdentity: crypto.randomUUID(),
    publishedRevision: () => base.getPublication()?.continuity.revision,
    waitStatus: () => status, coherenceErrors, renderMarks,
    holdRender(value: boolean) { renderHeld = value; },
    holdPublication(value: boolean) {
      publicationHeld = value;
      if (!value) { const work = deferred; deferred = []; work.forEach(publish => publish()); }
    },
    async change(tag: string, scenario: string) {
      const response = await fetch('/api/harness/change', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tag,scenario})});
      if (!response.ok) throw new Error('harness command failed');
      const sample = await response.json();
      status = 'pending';
      owner.waitForVisibility(sample.fence, {requiredObservers:observer ? [observer] : [],timeoutMs:10000})
        .then(result => { status = result.status; waitResults.push({tag,result}); });
      return sample;
    },
    disconnect() { disconnected = true; closeStream(); },
    reconnect() { disconnected = false; owner.recover(); },
    hasDeletedProject: () => base.getPublication()?.records.some(record => record.kind === 'project' && record.id === 'measure_delete'),
  };
  (window as any).navigationHarness = harness;
  owner.start();
  onDestroy(() => { observer?.dispose(); owner.dispose(); base.destroy(); unsubscribeOwner(); window.removeEventListener('hashchange', navigate); });
</script>
<input data-draft aria-label="local draft" />
<select data-choice aria-label="manual choice"><option value="automatic">Automatic</option><option value="manual">Manual</option></select>
{#if displayed}
  {#key route}
    <Consumer publication={displayed} generation={ownerState.generation} {owner} setObserver={(value) => { observer = value; }} {renderMarks} {coherenceErrors} />
  {/key}
{/if}
