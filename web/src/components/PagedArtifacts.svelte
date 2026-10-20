<script lang="ts">
  import SkeletonRegion from "./SkeletonRegion.svelte";
  import { untrack } from 'svelte';
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
  let requestedPage = $state({ identity: "", offset: 0 });
  const identity = $derived(`${owner.kind}/${owner.id}/${directory}`);
  const offset = $derived(requestedPage.identity === identity ? requestedPage.offset : 0);
  let heldPage = $state<{ identity: string; page: ArtifactDirectoryPage; arriving: boolean }>();
  const displayed = $derived(heldPage?.identity === identity ? heldPage.page : undefined);
  const total = $derived(displayed?.total ?? 0);
  const displayedOffset = $derived(displayed?.offset ?? 0);
  const beforeHeight = $derived(displayedOffset * CATALOG_ROW_HEIGHT);
  const afterHeight = $derived(Math.max(0, total - displayedOffset - (displayed?.entries.length ?? 0)) * CATALOG_ROW_HEIGHT);
  let viewport = $state<HTMLDivElement | null>(null);
  let viewportScroll = $state(0);
  let pendingTop = $state(0);
  let pendingHeight = $state(0);
  let arrivingPage = $state<{identity: string; offset: number} | null>(null);
  const openFolders = new SvelteSet<string>();
  function toggleFolder(path:string) {
    if (openFolders.has(path)) openFolders.delete(path);
    else { if(openFolders.size>=64) openFolders.delete(openFolders.values().next().value!); openFolders.add(path); }
  }
  const page = createQuery(() => jsonQuery<ArtifactDirectoryPage>(
    ['artifact-catalog',owner.kind,owner.id,directory,offset],
    `/api/artifact-catalog/${owner.kind}/${encodeURIComponent(owner.id)}?directory=${encodeURIComponent(directory)}&offset=${offset}&limit=${CATALOG_PAGE_SIZE}`));
  const pendingPage = $derived(page.isFetching && (!displayed || displayed.offset !== offset));
  // The pending page occupies only the currently empty part of the viewport.
  // Keep those viewport coordinates through the exit, even when spacers change.
  $effect(() => {
    viewportScroll;
    if (!pendingPage || !displayed || !viewport) return;
    arrivingPage = { identity, offset };
    const content = viewport.querySelector<HTMLElement>(":scope > .skeleton-region > .skeleton-content > [data-artifact-page]");
    if (!content) return;
    const box = viewport.getBoundingClientRect();
    const contentBox = content.getBoundingClientRect();
    if (offset < displayedOffset) {
      pendingTop = 0;
      pendingHeight = Math.max(0, Math.min(viewport.clientHeight, contentBox.top - box.top));
    } else {
      pendingTop = Math.max(0, Math.min(viewport.clientHeight, contentBox.bottom - box.top));
      pendingHeight = viewport.clientHeight - pendingTop;
    }
  });
  $effect(() => {
    if (!page.data) return;
    heldPage = { identity, page: page.data, arriving: untrack(() => arrivingPage?.identity === identity && arrivingPage.offset === page.data?.offset) }; onCount?.(page.data.total);
    if (offset >= page.data.total && offset > 0) requestedPage = { identity, offset: Math.max(0,Math.floor((page.data.total-1)/CATALOG_PAGE_SIZE)*CATALOG_PAGE_SIZE) };
  });
  function scroll() {
    if (!viewport) return;
    viewportScroll = viewport.scrollTop;
    let logicalTop=viewport.scrollTop;
    const box=viewport.getBoundingClientRect();
    for(const child of viewport.querySelectorAll<HTMLElement>(':scope > .skeleton-region > .skeleton-content > [data-artifact-page] > .artifact-catalog-children')) {
      const start=child.getBoundingClientRect().top-box.top+viewport.scrollTop;
      logicalTop-=Math.max(0,Math.min(child.offsetHeight,viewport.scrollTop-start));
    }
    const row = Math.max(0,Math.floor(logicalTop/CATALOG_ROW_HEIGHT)-CATALOG_OVERSCAN_ROWS);
    const next = Math.floor(row/CATALOG_WINDOW_STEP)*CATALOG_WINDOW_STEP;
    if (next !== offset) { viewport.scrollTop=logicalTop; requestedPage = { identity, offset: next }; }
  }
</script>
<div class="artifact-catalog-frame">
<div class="artifact-catalog-window" bind:this={viewport} onscroll={scroll} style:max-height={directory ? '240px' : '360px'}>
  <div data-artifact-before style:height={`${beforeHeight}px`} aria-hidden="true"></div>
  {#if page.error}<p role="status">Artifacts could not load. <button onclick={() => page.refetch()}>Retry</button></p>{/if}
  <SkeletonRegion shape="rows" loading={pendingPage && !displayed} loadingText="Loading artifacts…">
  {#key `${identity}/${displayedOffset}`}
  <div data-artifact-page class:resource-arriving={heldPage?.identity === identity && heldPage.arriving}>
  {#each displayed?.entries ?? [] as item (item.key)}
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
    {#if displayed && total === 0}<p class="empty">No artifacts yet.</p>{/if}
  </div>
  {/key}
  </SkeletonRegion>
  <div data-artifact-after style:height={`${afterHeight}px`} aria-hidden="true"></div>
</div>
{#key identity}
<div class="artifact-pending-page" style:top={`${pendingTop}px`} style:height={`${pendingHeight}px`}>
  <SkeletonRegion shape="rows" layout="fill" loading={Boolean(pendingPage && displayed && pendingHeight > 0)} loadingText="Loading artifacts…" />
</div>
{/key}
</div>
<style>
  .artifact-catalog-frame { position:relative; }
  .artifact-catalog-window { overflow-y:auto; overflow-anchor:none; }
  .artifact-pending-page { position:absolute; inset-inline:0; pointer-events:none; overflow:hidden; }
  .artifact-chip { min-height:36px; box-sizing:border-box; }
  .artifact-catalog-children { padding-left:var(--space-4); }
</style>
