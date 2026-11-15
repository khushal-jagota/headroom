<script lang="ts">
  import { onMount } from "svelte";
  import type { Snippet } from "svelte";
  import type { Block, SegmentedView } from "../../lib/pageKit";

  let {
    views,
    selected = $bindable(""),
    body
  }: {
    views: SegmentedView[];
    blockId?: string;
    selected?: string;
    body?: Snippet<[Block[]]>;
  } = $props();

  const id = $props.id();
  let current = $derived(views.find((item) => item.id === selected) ?? views[0]);
  let currentIndex = $derived(views.indexOf(current));
  let strip: HTMLDivElement;
  let scroller: HTMLDivElement;
  let tabs: HTMLDivElement;
  let ink: HTMLSpanElement;
  let positioned = false;

  $effect(() => {
    if (!views.some((item) => item.id === selected)) selected = views[0]?.id ?? "";
  });

  function updateEdges(): void {
    strip.classList.toggle("l", scroller.scrollLeft > 2);
    strip.classList.toggle("r", scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 2);
  }

  function placeUnderline(index: number, animate: boolean): void {
    const tab = tabs.querySelectorAll<HTMLButtonElement>('[role="tab"]')[index];
    ink.classList.toggle("nomove", !animate || !positioned);
    ink.style.width = `${tab?.offsetWidth ?? 0}px`;
    ink.style.transform = `translateX(${tab?.offsetLeft ?? 0}px)`;
    positioned = Boolean(tab);
    updateEdges();
  }

  $effect(() => {
    const index = currentIndex;
    // Labels and view order can change without a change to the selected index.
    views.map((item) => item.label);
    const frame = requestAnimationFrame(() => {
      const first = !positioned;
      placeUnderline(index, !first);
      scrollTabIntoView(index, first);
    });
    return () => cancelAnimationFrame(frame);
  });

  onMount(() => {
    const observer = new ResizeObserver(() => placeUnderline(currentIndex, false));
    observer.observe(scroller);
    observer.observe(tabs);
    return () => observer.disconnect();
  });

  function scrollTabIntoView(index: number, initial = false): void {
    tabs.querySelectorAll<HTMLButtonElement>('[role="tab"]')[index]?.scrollIntoView({
      inline: "nearest", block: "nearest",
      behavior: initial || matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"
    });
  }

  function select(index: number): void {
    const view = views[index];
    if (!view) return;
    if (view.id === current?.id) scrollTabIntoView(index);
    selected = view.id;
    tabs.querySelectorAll<HTMLButtonElement>('[role="tab"]')[index]?.focus({ preventScroll: true });
  }

  function onKeydown(event: KeyboardEvent): void {
    let index = currentIndex;
    if (event.key === "ArrowRight") index = Math.min(views.length - 1, index + 1);
    else if (event.key === "ArrowLeft") index = Math.max(0, index - 1);
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = views.length - 1;
    else return;
    event.preventDefault();
    select(index);
  }
</script>

<div class="segmented-block" data-block={body ? "segmented" : undefined}>
  <div class="tabstrip" bind:this={strip}>
    <div class="tabstrip-scroll" bind:this={scroller} onscroll={updateEdges}>
      <div class="tabstrip-tabs" bind:this={tabs} role="tablist" aria-label="Views" tabindex="-1"
        data-selected-view={current?.id} onkeydown={onKeydown}>
        {#each views as item, index (item.id)}
          <button class="tab" type="button" role="tab" id={`${id}-tab-${index}`}
            aria-selected={item.id === current?.id} aria-controls={`${id}-panel`}
            tabindex={item.id === current?.id ? 0 : -1} onclick={() => select(index)}>{item.label}</button>
        {/each}
        <span class="ink nomove" bind:this={ink} aria-hidden="true"></span>
      </div>
    </div>
  </div>
  {#if body && current}
    <div class="panel" id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-tab-${currentIndex}`} tabindex="0">
      {@render body(current.blocks)}
    </div>
  {/if}
</div>

<style>
  .segmented-block { min-width: 0; }
  .tabstrip { position: relative; min-width: 0; }
  .tabstrip-scroll {
    display: flex;
    scroll-padding-inline: var(--space-6);
    overflow-x: auto;
    scrollbar-width: none;
    box-shadow: inset 0 calc(-1 * var(--border-hairline)) 0 var(--border-color);
  }
  .tabstrip-scroll::-webkit-scrollbar { display: none; }
  .tabstrip-tabs { position: relative; display: flex; gap: var(--space-5); flex: none; padding: 0 var(--border-strong); }
  .tab {
    position: relative;
    flex: none;
    height: 36px;
    padding: 0;
    margin: 0;
    border: 0;
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--tone-secondary);
    font: inherit;
    font-size: var(--type-button);
    font-weight: var(--weight-button);
    line-height: 1;
    white-space: nowrap;
    cursor: pointer;
    transition: color var(--motion-fast) var(--motion-ease);
  }
  .ink {
    position: absolute;
    left: 0;
    bottom: 0;
    height: var(--border-strong);
    width: 0;
    border-radius: var(--border-strong);
    background: var(--tone-strong);
    transform-origin: 0 0;
    pointer-events: none;
    transition: transform var(--motion-slow) var(--motion-ease), width var(--motion-slow) var(--motion-ease);
  }
  .ink.nomove { transition: none; }
  .tab:hover, .tab[aria-selected="true"] { color: var(--tone-strong); }
  .tab:focus-visible { outline: var(--focus-width) solid var(--focus-ring); outline-offset: calc(-1 * var(--focus-width)); }
  .tabstrip::before, .tabstrip::after {
    content: "";
    position: absolute;
    top: 0;
    bottom: 0;
    width: var(--space-6);
    pointer-events: none;
    opacity: 0;
    transition: opacity var(--motion-base);
    z-index: 1;
  }
  .tabstrip::before { left: 0; background: linear-gradient(90deg, var(--surface-1), transparent); }
  .tabstrip::after { right: 0; background: linear-gradient(270deg, var(--surface-1), transparent); }
  .tabstrip:global(.l)::before, .tabstrip:global(.r)::after { opacity: 1; }
  .panel { margin-top: var(--space-4); }
  .panel:focus-visible { outline: var(--focus-width) solid var(--focus-ring); outline-offset: var(--focus-offset); border-radius: var(--radius-lg); }
  @media (pointer: coarse) { .tab { height: 44px; } }
  @media (prefers-reduced-motion: reduce) {
    .tab, .ink, .tabstrip::before, .tabstrip::after { transition: none; }
  }
</style>
