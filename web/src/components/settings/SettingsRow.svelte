<script lang="ts">
  import type { Snippet } from "svelte";
  import { settingsSearch } from "../../lib/settings/search.svelte";

  /* One flat Settings row: label and description on the left, the control on the right.
     A row with `href` is a link row with a chevron. `rowId` is the name search results use
     to scroll to the row and flash it. */
  let { label, tag, description, control, lead, after, error = null, href, rowId }: {
    label: string;
    tag?: string;
    description?: string | Snippet;
    control?: Snippet;
    lead?: Snippet;
    after?: Snippet;
    error?: string | null;
    href?: string;
    rowId?: string;
  } = $props();

  let element = $state<HTMLElement | null>(null);
  let flashing = $state(false);
  $effect(() => {
    if (!rowId || settingsSearch.flashRowId !== rowId || !element) return;
    settingsSearch.flashRowId = null;
    element.scrollIntoView({ block: "center" });
    flashing = true;
    const timer = setTimeout(() => (flashing = false), 1600);
    return () => clearTimeout(timer);
  });
</script>

{#snippet body()}
  {@render lead?.()}
  <div class="settings-row-label">
    <div class="settings-row-title">{label}{#if tag}<span class="settings-row-tag">{tag}</span>{/if}</div>
    {#if typeof description === "string"}<div class="settings-row-description">{description}</div>
    {:else if description}<div class="settings-row-description">{@render description()}</div>{/if}
  </div>
  {@render control?.()}
  {#if href}<svg class="settings-row-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 3.5 4.5 4.5L6 12.5" /></svg>{/if}
{/snippet}

<div class="settings-item" bind:this={element} data-settings-row={rowId}>
  {#if href}
    <a class="settings-row settings-row--link" class:settings-row--flash={flashing} {href}>{@render body()}</a>
  {:else}
    <div class="settings-row" class:settings-row--flash={flashing}>{@render body()}</div>
  {/if}
  {#if error}<div class="settings-row-error" role="alert">{error}</div>{/if}
  {@render after?.()}
</div>

<style>
  .settings-row { position:relative; display:flex; align-items:center; gap:var(--space-4); min-height:52px; padding:var(--space-3) 0; color:inherit; text-decoration:none; }
  .settings-row--link { cursor:pointer; }
  .settings-row--link::before { content:""; position:absolute; inset:2px calc(-1 * var(--page-row-outset)); border-radius:var(--radius-md); background:transparent; transition:background var(--motion-fast) var(--motion-ease); pointer-events:none; }
  .settings-row--link:hover::before { background:var(--wash-context); }
  .settings-row--link:focus-visible { outline:0; }
  .settings-row--link:focus-visible::before { outline:var(--focus-width) solid var(--focus-ring); }
  .settings-row-label { flex:1; min-width:0; }
  .settings-row-title { display:flex; align-items:baseline; gap:var(--space-2); font-size:var(--type-body); color:var(--tone-primary); }
  .settings-row-tag { color:var(--tone-tertiary); font-size:var(--type-small); font-weight:var(--weight-small); }
  .settings-row-description { margin-top:2px; font-size:var(--type-small); line-height:1.45; color:var(--tone-tertiary); }
  .settings-row-chevron { flex:none; width:14px; height:14px; color:var(--tone-faint); }
  .settings-row--link:hover .settings-row-chevron { color:var(--tone-secondary); }
  .settings-row-error { padding:0 0 var(--space-3); font-size:var(--type-small); color:var(--accent-error); }
  .settings-row-error::before { content:"● "; font-size:8px; vertical-align:2px; }
  .settings-row--flash { animation:settings-row-flash 1.6s var(--motion-ease); }
  @keyframes settings-row-flash { 0%, 35% { background:color-mix(in srgb, var(--accent-bright) 12%, transparent); } 100% { background:transparent; } }
  @media (max-width: 720px) {
    .settings-row { flex-wrap:wrap; row-gap:var(--space-2); }
    .settings-row-label { flex-basis:60%; }
  }
  @media (prefers-reduced-motion: reduce) { .settings-row--flash { animation:none; } .settings-row--link::before { transition:none; } }
</style>
