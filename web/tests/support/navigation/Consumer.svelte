<script lang="ts">
  import { tick, onDestroy, untrack } from 'svelte';
  import type { createNavigationOwner } from '../../../src/lib/navigation/owner';
  import type { NavigationPublication } from '../../../src/lib/navigation/collection';
  import type { RenderObserver } from '../../../src/lib/navigation/visibility';
  let { publication, generation, owner, setObserver, renderMarks, coherenceErrors }: {
    publication: NavigationPublication;
    generation: number;
    owner: ReturnType<typeof createNavigationOwner>;
    setObserver: (observer: RenderObserver) => void;
    renderMarks: { revision: string; epoch: string; rendered_ms: number }[];
    coherenceErrors: string[];
  } = $props();
  const observer = untrack(() => owner.registerObserver(crypto.randomUUID()));
  const consumerIdentity = crypto.randomUUID();
  untrack(() => setObserver(observer));
  onDestroy(() => observer.dispose());
  let root: HTMLDivElement;
  const title = (id: string) => {
    const record = publication.records.find(row => row.kind === 'ticket' && row.id === id);
    return record?.kind === 'ticket' ? record.value.title : '';
  };
  $effect(() => {
    const observed = publication;
    const observation = observer.observeSelectors(observed, generation);
    void tick().then(() => {
      if (publication !== observed) return;
      const expected = observed.records.filter(row => row.kind === 'ticket' && ['t_measure_0000','t_measure_0001'].includes(row.id));
      const actual = [...root.querySelectorAll('[data-title]')].map(element => element.textContent);
      const wanted = expected.sort((a,b) => a.id.localeCompare(b.id)).map(row => row.kind === 'ticket' ? row.value.title : '');
      if (JSON.stringify(actual) !== JSON.stringify(wanted) || root.dataset.revision !== observed.continuity.revision) {
        coherenceErrors.push(`incoherent DOM at ${observed.continuity.revision}`); return;
      }
      renderMarks.push({revision:observed.continuity.revision,epoch:observed.continuity.epoch,rendered_ms:performance.now()});
      if (observation) observer.acknowledgeRender(observed, observation);
    });
  });
</script>
<div data-consumer={consumerIdentity} bind:this={root} data-epoch={publication.continuity.epoch} data-revision={publication.continuity.revision}>
  <span data-left data-title>{title('t_measure_0000')}</span>
  <span data-right data-title>{title('t_measure_0001')}</span>
</div>
