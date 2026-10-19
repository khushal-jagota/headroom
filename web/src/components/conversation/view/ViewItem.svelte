<script lang="ts">
  import { onMount, type Snippet } from 'svelte';
  let { itemKey, active, marker = false, assistantContinuation = false, tool = false, toolContinuation = false, onVisible, children }: {
      itemKey: string;
      active: boolean;
      marker?: boolean;
      assistantContinuation?: boolean;
      tool?: boolean;
      toolContinuation?: boolean;
      onVisible: (key: string, visible: boolean) => void;
      children: Snippet;
  } = $props();
  let node = $state<HTMLDivElement | null>(null);
  let height = $state(42);
  onMount(() => {
      const element = node!;
      const observer = new IntersectionObserver(entries => onVisible(itemKey, entries[0]?.isIntersecting ?? false), { root: element.closest('[data-conversation-thread]'), rootMargin: '100% 0px' });
      observer.observe(element);
      const resize = new ResizeObserver(() => { if (active && !marker && element.offsetHeight > 0)
          height = element.offsetHeight; });
      resize.observe(element);
      return () => { observer.disconnect(); resize.disconnect(); onVisible(itemKey, false); };
  });
</script>
<div bind:this={node} data-conversation-view-key={itemKey} class:marker class:assistant-continuation={assistantContinuation} class:tool class:tool-continuation={toolContinuation} style:min-height={active||marker?'0':`${height}px`}>
  {#if active}{@render children()}{/if}
</div>

<style>
  /* Retain a measurable box for virtualization and the thread's flex alignment
     for rows rendered through display: contents transcript containers. */
  div { display: flex; flex-direction: column; }
  /* A structural head still observes the viewport and requests its bounded scope,
     but has neither a placeholder nor its own share of the thread's flex gap. */
  .marker { height: 0; margin-block-start: calc(var(--space-5) * -1); }
  /* These are the same relationships as the unwrapped transcript. They follow
     admitted logical neighbors, including when a neighbor's body is unmounted. */
  .assistant-continuation { position: relative; margin-block-start: var(--space-4); }
  .assistant-continuation::before {
    content: "";
    position: absolute;
    inset-block-start: calc(var(--space-3) * -1);
    inset-inline-start: 0;
    inline-size: var(--space-6);
    border-block-start: var(--border-hairline) solid var(--text-faintest);
    opacity: 0.55;
    pointer-events: none;
  }
  .tool {
    position: relative;
    min-width: 0;
    max-width: 100%;
    border-inline-start: var(--border-hairline) solid var(--border-color);
    margin-inline-start: var(--space-3);
    padding-inline-start: var(--space-3);
  }
  .tool-continuation { margin-block-start: calc(var(--space-1) - var(--space-5)); }
  .tool-continuation::before {
    content: "";
    position: absolute;
    inset-block-start: calc(var(--space-1) * -1);
    inset-inline-start: calc(var(--border-hairline) * -1);
    block-size: var(--space-1);
    border-inline-start: var(--border-hairline) solid var(--border-color);
    pointer-events: none;
  }
</style>
