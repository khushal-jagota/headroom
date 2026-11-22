<script lang="ts">
  import type { Snippet } from "svelte";

  /* A titled group of flat rows with hairline seams between them. A section without a title
     starts with a hairline. `first` closes the gap under the page title. `rows` false drops
     the seams, for a section that holds a block such as a Markdown well. */
  let { title, action, note, first = false, rows = true, children }: {
    title?: string;
    action?: Snippet;
    note?: string;
    first?: boolean;
    rows?: boolean;
    children: Snippet;
  } = $props();
  let headed = $derived(Boolean(title || action));
</script>

<section class="settings-section" class:settings-section--first={first} class:settings-section--headless={!headed}>
  {#if headed}
    <div class="settings-section-head"><h2>{title ?? ""}</h2>{@render action?.()}</div>
  {/if}
  {#if note}<p class="settings-section-note">{note}</p>{/if}
  <div class:settings-list={rows}>{@render children()}</div>
</section>

<style>
  .settings-section { margin-top:var(--page-section-gap); }
  .settings-section--first { margin-top:var(--page-block-gap); }
  .settings-section-head { display:flex; align-items:center; justify-content:space-between; gap:var(--space-4); min-height:var(--button-height); padding-bottom:var(--space-2); border-bottom:var(--border-hairline) solid var(--border-color); }
  .settings-section-head h2 { margin:0; font-size:var(--type-page-section); font-weight:var(--weight-page-section); color:var(--tone-primary); }
  .settings-section--headless > .settings-list { border-top:var(--border-hairline) solid var(--border-color); }
  .settings-section-note { margin:var(--space-2) 0 0; font-size:var(--type-small); color:var(--tone-tertiary); }
  .settings-list > :global(* + *) { border-top:var(--border-hairline) solid var(--border-color); }
</style>
