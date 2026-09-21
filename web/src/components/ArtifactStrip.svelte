<script lang="ts">
  import type { ArtifactStripItem } from "../lib/artifactStrip";
  import { artifactChipIsOverflow } from "../lib/artifactStrip";
  import { SvelteSet } from "svelte/reactivity";

  let { items }: { items: ArtifactStripItem[] } = $props();
  let expanded = $state(false);
  // A folder holds no page of its own, so it opens where it stands rather than taking the
  // reader somewhere. Which folders are open is this strip's business and nobody else's.
  let openFolders = $state(new SvelteSet<string>());

  function toggleFolder(key: string): void {
    if (openFolders.has(key)) openFolders.delete(key);
    else openFolders.add(key);
  }
</script>

{#snippet chips(row: ArtifactStripItem[], depth: number)}
  {#each row as item, index (item.key)}
    {@const overflow = depth === 0 && artifactChipIsOverflow(index, row.length)}
    {#if item.children.length}
      <button
        class="chip artifact-chip artifact-chip--folder"
        class:artifact-chip--overflow={overflow}
        class:artifact-chip--nested={depth > 0}
        type="button"
        aria-expanded={openFolders.has(item.key)}
        data-artifact-folder={item.key}
        onclick={() => toggleFolder(item.key)}
      >
        <span>{item.label}</span><small>{item.kind}</small>
      </button>
      {#if openFolders.has(item.key)}
        {@render chips(item.children, depth + 1)}
      {/if}
    {:else if item.href}
      <a
        class="chip artifact-chip"
        class:artifact-chip--overflow={overflow}
        class:artifact-chip--nested={depth > 0}
        href={item.href}
        data-artifact-chip={item.path}
      >
        <span>{item.label}</span><small>{item.kind}</small>
      </a>
    {:else}
      <span
        class="chip artifact-chip artifact-chip--unavailable"
        class:artifact-chip--overflow={overflow}
        class:artifact-chip--nested={depth > 0}
        data-artifact-unavailable
      >
        <span>{item.label}</span><small>unavailable</small>
      </span>
    {/if}
  {/each}
{/snippet}

{#if items.length}
  <nav class="artifact-strip" aria-label="Artifacts" data-artifact-strip data-expanded={expanded}>
    <span class="artifact-strip-label">Artifacts</span>
    <div class="artifact-strip-items">
      {@render chips(items, 0)}
      {#if items.length > 6}
        <button
          class="chip artifact-strip-more"
          type="button"
          aria-expanded={expanded}
          onclick={() => expanded = !expanded}
        >{expanded ? "Show fewer" : `+${items.length - 5} more`}</button>
      {/if}
    </div>
  </nav>
{/if}
