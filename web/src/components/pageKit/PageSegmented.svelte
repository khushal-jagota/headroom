<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Block, SegmentedView } from "../../lib/pageKit";

  let {
    views,
    selected = $bindable(""),
    body
  }: {
    views: SegmentedView[];
    selected?: string;
    body?: Snippet<[Block[]]>;
  } = $props();

  let current = $derived(views.find((item) => item.id === selected) ?? views[0]);

  $effect(() => {
    if (!views.some((item) => item.id === selected)) selected = views[0]?.id ?? "";
  });

  function onKeydown(event: KeyboardEvent): void {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const ids = views.map((item) => item.id);
    const index = Math.max(0, ids.indexOf(current?.id ?? ""));
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = ids[(index + step + ids.length) % ids.length];
    if (next) selected = next;
  }
</script>

<div class="segmented-block" data-block={body ? "segmented" : undefined}>
  <div
    class="segmented"
    role="radiogroup"
    tabindex="-1"
    aria-label="Views"
    data-selected-view={current?.id}
    style:--segments={views.length}
    onkeydown={onKeydown}
  >
    {#each views as item (item.id)}
      <button
        type="button"
        role="radio"
        aria-checked={item.id === current?.id}
        onclick={() => { selected = item.id; }}
      >{item.label}</button>
    {/each}
  </div>
  {#if body && current}
    <div class="segmented-body">
      {@render body(current.blocks)}
    </div>
  {/if}
</div>

<style>
  .segmented-block { min-width: 0; }
  .segmented {
    display: inline-grid;
    grid-auto-flow: column;
    grid-auto-columns: 1fr;
    width: fit-content;
    max-width: 100%;
    height: var(--space-6);
    box-sizing: border-box;
    align-items: stretch;
    padding: 3px;
    border: 0;
    border-radius: var(--radius-pill);
    background: var(--surface-recessed);
  }
  button {
    min-width: 4.25rem;
    margin: 0;
    border: 0;
    border-radius: var(--radius-pill);
    background: transparent;
    color: var(--text-faint);
    font: inherit;
    font-size: var(--type-sm);
    font-weight: 500;
    line-height: 1;
    padding: 0 var(--space-3);
    cursor: pointer;
  }
  button:hover { color: var(--text-strong); }
  button:focus-visible {
    outline: var(--border-strong) solid var(--accent-bright);
    outline-offset: -2px;
  }
  button[aria-checked="true"] {
    background: var(--surface-2);
    color: var(--text-strong);
    box-shadow: var(--shadow-float);
  }
  .segmented-body { margin-top: var(--space-5); }
</style>
