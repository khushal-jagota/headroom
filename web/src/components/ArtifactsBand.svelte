<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { jsonQuery } from '../lib/queryCatalogue';
  import ArtifactStrip from "./ArtifactStrip.svelte";
  import PagedArtifacts from './PagedArtifacts.svelte';
  import type { ArtifactStripItem } from "../lib/artifactStrip";
  import type { ArtifactOwnerKind, ArtifactDirectoryPage } from '../lib/catalogs/artifacts';
  let { items = [], owner }: { items?: ArtifactStripItem[]; owner?: {kind:ArtifactOwnerKind;id:string} } = $props();
  let open = $state(false);
  let observedCount = $state<number | null>(null);
  const summary = createQuery(() => ({...jsonQuery<ArtifactDirectoryPage>(
    ['artifact-catalog-count',owner?.kind,owner?.id],
    `/api/artifact-catalog/${owner?.kind}/${encodeURIComponent(owner?.id ?? '')}?limit=1`),enabled:Boolean(owner) && !open}));
  let count = $derived(owner ? summary.data?.total ?? observedCount : items.length);
</script>
<details class="artifacts-band disclosure-band" data-artifacts-band bind:open>
  <summary>Artifacts<span class="count disclosure-band-trailing">{count ?? '…'}</span></summary>
  {#if open}<div class="body disclosure-band-body">
    {#if owner}<nav class="artifact-strip" aria-label="Artifacts" data-artifact-strip><div class="artifact-strip-items"><PagedArtifacts {owner} onCount={(value)=>observedCount=value} /></div></nav>
    {:else if items.length}<ArtifactStrip {items} />{:else}<p class="empty">No artifacts yet.</p>{/if}
  </div>{/if}
</details>
<style>
  .empty { margin:var(--space-2) 0; font-size:13px; color:var(--text-faintest); }
  .body :global(.artifact-strip) { display:block; padding:0; margin:0; border:0; }
  .body :global(.artifact-strip-label) { display:none; }
  .body :global(.artifact-strip-items) { display:flex; flex-direction:column; gap:0; }
  .body :global(.artifact-chip) { width:calc(100% + 2 * var(--space-2)); margin-inline:calc(-1 * var(--space-2)); display:flex; justify-content:space-between; gap:var(--space-3); padding:9px var(--space-2); border:0; border-radius:var(--radius-sm); background:transparent; font-size:15px; color:var(--text-muted); }
  .body :global(.artifact-chip:hover) { background-image:var(--interaction-hover); color:var(--text-strong); }
  .body :global(.artifact-chip small) { font-size:13px; text-transform:none; letter-spacing:normal; color:var(--text-faintest); }
  .body :global(.artifact-chip--overflow) { display:flex; }
  .body :global(.artifact-strip-more) { display:none; }
  .body :global(.artifact-chip--nested) { padding-left:var(--space-5); }
</style>
