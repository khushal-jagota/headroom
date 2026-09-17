<script lang="ts">
  import type { ArtifactStripItem } from "../lib/artifactStrip";
  import { artifactChipIsOverflow } from "../lib/artifactStrip";

  let { items }: { items: ArtifactStripItem[] } = $props();
  let expanded = $state(false);
</script>

{#if items.length}
  <nav class="artifact-strip" aria-label="Artifacts" data-artifact-strip data-expanded={expanded}>
    <span class="artifact-strip-label">Artifacts</span>
    <div class="artifact-strip-items">
      {#each items as item, index (item.key)}
        {#if item.href}
          <a class="chip artifact-chip" class:artifact-chip--overflow={artifactChipIsOverflow(index, items.length)} href={item.href} data-artifact-chip={item.path}>
            <span>{item.label}</span><small>{item.kind}</small>
          </a>
        {:else}
          <span class="chip artifact-chip artifact-chip--unavailable" class:artifact-chip--overflow={artifactChipIsOverflow(index, items.length)} data-artifact-unavailable>
            <span>{item.label}</span><small>unavailable</small>
          </span>
        {/if}
      {/each}
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
