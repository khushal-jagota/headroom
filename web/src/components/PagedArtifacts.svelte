<script lang="ts">
  import { SvelteSet } from 'svelte/reactivity';
  import { createQuery } from '@tanstack/svelte-query';
  import { jsonQuery } from '../lib/queryCatalogue';
  import { CATALOG_PAGE_SIZE, CATALOG_ROW_HEIGHT, CATALOG_OVERSCAN_ROWS, CATALOG_WINDOW_STEP } from '../lib/catalogs/config';
  import type { ArtifactOwnerKind, ArtifactDirectoryPage } from '../lib/catalogs/artifacts';
  import { managedFileTargetFromLinkHref } from '../lib/artifactStrip';
  import { previewHashHref } from '../lib/filePreview';
  import PagedArtifacts from './PagedArtifacts.svelte';
  let { owner, directory = '', onCount }: {
    owner: { kind: ArtifactOwnerKind; id: string }; directory?: string; onCount?: (count: number) => void;
  } = $props();
  let offset = $state(0);
  let total = $state(0);
  let viewport = $state<HTMLDivElement | null>(null);
  const openFolders = new SvelteSet<string>();
  function toggleFolder(path:string) {
    if (openFolders.has(path)) openFolders.delete(path);
    else { if(openFolders.size>=64) openFolders.delete(openFolders.values().next().value!); openFolders.add(path); }
  }
  const page = createQuery(() => jsonQuery<ArtifactDirectoryPage>(
    ['artifact-catalog',owner.kind,owner.id,directory,offset],
    `/api/artifact-catalog/${owner.kind}/${encodeURIComponent(owner.id)}?directory=${encodeURIComponent(directory)}&offset=${offset}&limit=${CATALOG_PAGE_SIZE}`));
  $effect(() => {
    if (!page.data) return;
    total = page.data.total; onCount?.(total);
    if (offset >= total && offset > 0) offset = Math.max(0,Math.floor((total-1)/CATALOG_PAGE_SIZE)*CATALOG_PAGE_SIZE);
  });
  function scroll() {
    if (!viewport) return;
    let logicalTop=viewport.scrollTop;
    const box=viewport.getBoundingClientRect();
    for(const child of viewport.querySelectorAll<HTMLElement>(':scope > .artifact-catalog-children')) {
      const start=child.getBoundingClientRect().top-box.top+viewport.scrollTop;
      logicalTop-=Math.max(0,Math.min(child.offsetHeight,viewport.scrollTop-start));
    }
    const row = Math.max(0,Math.floor(logicalTop/CATALOG_ROW_HEIGHT)-CATALOG_OVERSCAN_ROWS);
    const next = Math.floor(row/CATALOG_WINDOW_STEP)*CATALOG_WINDOW_STEP;
    if (next !== offset) { viewport.scrollTop=logicalTop; offset = next; }
  }
</script>
<div class="artifact-catalog-window" bind:this={viewport} onscroll={scroll} style:max-height={directory ? '240px' : '360px'}>
  <div style:height={`${offset*CATALOG_ROW_HEIGHT}px`} aria-hidden="true"></div>
  {#if page.error}<p role="status">Artifacts could not load. <button onclick={() => page.refetch()}>Retry</button></p>{/if}
  {#each page.data?.entries ?? [] as item (item.key)}
    {@const target = managedFileTargetFromLinkHref(item.href)}
    {#if item.directory}
      <button class="chip artifact-chip artifact-chip--folder" type="button" aria-expanded={openFolders.has(item.directory)}
        data-artifact-folder={item.path} onclick={() => toggleFolder(item.directory!)}>
        <span>{item.label}</span><small>{item.kind}</small>
      </button>
      {#if openFolders.has(item.directory)}<div class="artifact-catalog-children"><PagedArtifacts {owner} directory={item.directory} /></div>{/if}
    {:else if target}
      <a class="chip artifact-chip" href={previewHashHref(target)} data-artifact-chip={item.path}><span>{item.label}</span><small>{item.kind}</small></a>
    {:else}
      <span class="chip artifact-chip artifact-chip--unavailable" data-artifact-unavailable><span>{item.label}</span><small>unavailable</small></span>
    {/if}
  {/each}
  {#if page.isPending}<p role="status">Loading artifacts…</p>{:else if total===0}<p class="empty">No artifacts yet.</p>{/if}
  <div style:height={`${Math.max(0,total-offset-(page.data?.entries.length ?? 0))*CATALOG_ROW_HEIGHT}px`} aria-hidden="true"></div>
</div>
<style>
  .artifact-catalog-window { overflow-y:auto; overflow-anchor:none; }
  .artifact-chip { min-height:36px; box-sizing:border-box; }
  .artifact-catalog-children { padding-left:var(--space-4); }
</style>
