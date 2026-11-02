<script lang="ts">
  import { getContext, type Snippet } from "svelte";
  import type { Block, SegmentedView } from "../../lib/pageKit";
  import { PAGE_CONTEXT, type PageContext } from "../../lib/pageKit/context";
  import PageText from "./PageText.svelte";

  let {
    views,
    blockId,
    selected = $bindable(""),
    body
  }: {
    views: SegmentedView[];
    blockId?: string;
    selected?: string;
    body?: Snippet<[Block[]]>;
  } = $props();

  const context = getContext<PageContext | undefined>(PAGE_CONTEXT);
  let editable = $derived(Boolean(blockId && context?.authorText));
  let current = $derived(views.find((item) => item.id === selected) ?? views[0]);

  $effect(() => {
    if (!views.some((item) => item.id === selected)) selected = views[0]?.id ?? "";
  });

  function onKeydown(event: KeyboardEvent): void {
    if (event.target instanceof Element && event.target.closest('[contenteditable="true"]')) return;
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
    class:editable
    role="radiogroup"
    tabindex="-1"
    aria-label="Views"
    data-selected-view={current?.id}
    style:--segments={views.length}
    onkeydown={onKeydown}
  >
    {#each views as item (item.id)}
      {#if editable && blockId}
        <div class="editable-segment" data-selected={item.id === current?.id}>
          <button class="view-select" type="button" role="radio" aria-label={`Show ${item.label} view`}
            aria-checked={item.id === current?.id} onclick={() => { selected = item.id; }}><span aria-hidden="true"></span></button>
          <PageText {blockId} field={`views.${item.id}.label`} value={item.label} label={`View label: ${item.label}`}
            update={(block, raw) => (block.type === "section" || block.type === "segmented") && block.views
              ? {...block, views: block.views.map(view => view.id === item.id ? {...view, label: raw} : view)} : block} />
        </div>
      {:else}
        <button
          type="button"
          role="radio"
          aria-checked={item.id === current?.id}
          onclick={() => { selected = item.id; }}
        >{item.label}</button>
      {/if}
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
  .segmented.editable { height: auto; min-height: var(--space-6); }
  .editable-segment {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    min-width: 0;
    padding: 0 var(--space-3) 0 var(--space-1);
    border-radius: var(--radius-pill);
    color: var(--text-faint);
    font-size: var(--type-sm);
    font-weight: 500;
    line-height: 1.4;
  }
  .editable-segment[data-selected="true"] {
    background: var(--surface-2);
    color: var(--text-strong);
    box-shadow: var(--shadow-float);
  }
  .view-select {
    display: grid;
    place-items: center;
    flex: none;
    min-width: var(--space-5);
    min-height: var(--space-5);
    padding: 0;
  }
  .view-select span {
    width: var(--space-2);
    height: var(--space-2);
    border: var(--border-hairline) solid currentColor;
    border-radius: var(--radius-pill);
  }
  .view-select[aria-checked="true"] { background: transparent; box-shadow: none; }
  .view-select[aria-checked="true"] span { background: currentColor; }
  .segmented-body { margin-top: var(--space-5); }
</style>
