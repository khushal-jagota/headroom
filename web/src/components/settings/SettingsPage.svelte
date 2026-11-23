<script lang="ts">
  import type { Snippet } from "svelte";

  /* The frame of every Settings page: the line above the title ("Settings", or "Settings ›
     Agents" on a detail page), the title row with the page's own action, then the page.
     A phone hides that line and shows a back button instead. */
  let { title, parent, actions, children }: {
    title: string;
    /** The list page a detail page belongs to. */
    parent?: { label: string; href: string };
    actions?: Snippet;
    children: Snippet;
  } = $props();
  let back = $derived(parent ?? { label: "Settings", href: "#/settings" });
</script>

<div class="settings-page" data-settings-page>
  <a class="button button--ghost settings-page-back" href={back.href}>
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 3.5 5.5 8l4.5 4.5" /></svg>{back.label}
  </a>
  <div class="settings-page-eyebrow">
    Settings{#if parent}<span aria-hidden="true">›</span><a href={parent.href}>{parent.label}</a>{/if}
  </div>
  <div class="settings-page-title-row">
    <h1>{title}</h1>
    {#if actions}<div class="settings-page-actions">{@render actions()}</div>{/if}
  </div>
  {@render children()}
</div>

<style>
  .settings-page { max-width:var(--page-measure); margin:0 auto; padding:var(--page-top) 0 var(--space-page-tail); animation:settings-page-in var(--motion-base) var(--motion-ease); }
  @keyframes settings-page-in { from { opacity:0; transform:translateY(var(--space-1)); } to { opacity:1; transform:none; } }
  .settings-page-eyebrow { display:flex; align-items:center; gap:var(--space-1); min-height:20px; margin-top:var(--space-6); font-size:var(--type-small); font-weight:var(--weight-small); color:var(--tone-tertiary); }
  .settings-page-eyebrow a { color:var(--tone-secondary); text-decoration:none; }
  .settings-page-eyebrow a:hover { color:var(--tone-strong); }
  .settings-page-title-row { display:flex; align-items:center; justify-content:space-between; gap:var(--space-4); margin-top:var(--space-1); }
  .settings-page-title-row h1 { margin:0; min-width:0; font-size:var(--type-page-title); font-weight:var(--weight-page-title); letter-spacing:var(--tracking-page-title); line-height:1.2; color:var(--tone-strong); }
  .settings-page-actions { display:flex; align-items:center; gap:var(--space-2); }
  .settings-page-back { display:none; }
  .settings-page-back svg { width:var(--button-icon); height:var(--button-icon); }
  @media (max-width: 720px) {
    .settings-page-eyebrow { display:none; }
    .settings-page-back { display:inline-flex; margin-left:calc(-1 * var(--button-pad-x)); }
    .settings-page-title-row { margin-top:var(--space-3); flex-wrap:wrap; }
  }
  @media (prefers-reduced-motion: reduce) { .settings-page { animation:none; } }
</style>
