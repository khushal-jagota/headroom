<script lang="ts">
  import { onMount, type Snippet } from 'svelte';
  let { itemKey, active, onVisible, children }: {
      itemKey: string;
      active: boolean;
      onVisible: (key: string, visible: boolean) => void;
      children: Snippet;
  } = $props();
  let node = $state<HTMLDivElement | null>(null);
  let height = $state(42);
  onMount(() => {
      const element = node!;
      const observer = new IntersectionObserver(entries => onVisible(itemKey, entries[0]?.isIntersecting ?? false), { root: element.closest('[data-conversation-thread]'), rootMargin: '100% 0px' });
      observer.observe(element);
      const resize = new ResizeObserver(() => { if (active && element.offsetHeight > 0)
          height = element.offsetHeight; });
      resize.observe(element);
      return () => { observer.disconnect(); resize.disconnect(); onVisible(itemKey, false); };
  });
</script>
<div bind:this={node} data-conversation-view-key={itemKey} style:min-height={active?'0':`${height}px`}>
  {#if active}{@render children()}{/if}
</div>

<style>
  /* Retain a measurable box for virtualization and the thread's flex alignment
     for rows rendered through display: contents transcript containers. */
  div { display: flex; flex-direction: column; }
</style>
