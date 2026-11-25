<script lang="ts">
  /** The desktop window's top row: a 52px drag region with no fill, where macOS draws the
   *  traffic lights, then the sidebar button. Each page's own row is
   *  pinned on the same line by WindowPageRowSlot. Nothing renders outside the desktop window. */
  import { READING_COLUMN_CLEARANCE_PX, TOOLTIP_DELAY_MS } from "../../lib/desktopWindow/config";
  import { isDesktopWindow, keyTogglesWindowSidebar, toggleWindowSidebar, windowSidebar } from "../../lib/desktopWindow/windowState.svelte";

  let { reviewCount, unreachable = false }: { reviewCount: number; unreachable?: boolean } = $props();

  const shown = isDesktopWindow();
  let controls = $state<HTMLElement | null>(null);
  let tipShown = $state(false);
  let tipTimer: ReturnType<typeof setTimeout> | undefined;
  const sidebarLabel = $derived((windowSidebar.hidden ? "Show sidebar" : "Hide sidebar") + (windowSidebar.hidden && unreachable ? " — cannot reach Panels" : ""));

  function toggle(): void {
    hideTip();
    toggleWindowSidebar();
  }

  function onKeydown(event: KeyboardEvent): void {
    if (!shown || !keyTogglesWindowSidebar(event)) return;
    event.preventDefault();
    toggle();
  }

  function showTipSoon(): void {
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => { tipShown = true; }, TOOLTIP_DELAY_MS);
  }

  function hideTip(): void {
    clearTimeout(tipTimer);
    tipShown = false;
  }

  // With the sidebar hidden, the reading column keeps clear of the sidebar button: the
  // button's right edge plus the clearance, published for the page's column insets.
  $effect(() => {
    if (!controls) return;
    const observed = controls;
    const root = document.documentElement;
    const publish = () => root.style.setProperty("--window-controls-clear", `${Math.ceil(observed.getBoundingClientRect().right) + READING_COLUMN_CLEARANCE_PX}px`);
    const sizes = new ResizeObserver(publish);
    sizes.observe(observed);
    // Full screen moves the controls without resizing them.
    const states = new MutationObserver(publish);
    states.observe(root, { attributes: true, attributeFilter: ["data-window-full-screen"] });
    publish();
    return () => { sizes.disconnect(); states.disconnect(); root.style.removeProperty("--window-controls-clear"); clearTimeout(tipTimer); };
  });
</script>

<svelte:window onkeydown={onKeydown} />

{#if shown}
  <!-- The empty row: a drag region, first in the page and under everything, so every control
       drawn over it (this row's button, the page row) takes the click instead. -->
  <div class="window-drag-region" aria-hidden="true"></div>
  <div class="window-controls" bind:this={controls}>
    <button type="button" class="window-sidebar-button" aria-label={sidebarLabel} aria-expanded={!windowSidebar.hidden} aria-keyshortcuts="Control+Meta+S"
      data-window-sidebar-button onclick={toggle} onpointerenter={showTipSoon} onpointerleave={hideTip}>
      <!-- The macOS sidebar glyph (sidebar.left) at its native 18x14 size and stroke. -->
      <svg viewBox="0 0 18 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" aria-hidden="true"><rect x="0.65" y="0.65" width="16.7" height="12.7" rx="3" /><path d="M6.5 0.9v12.2" /><path d="M2.6 3.6h1.9M2.6 5.8h1.9M2.6 8h1.9" /></svg>
      <span class="window-offline-dot" class:window-offline-dot--shown={windowSidebar.hidden && unreachable} aria-hidden="true" data-window-offline-dot></span>
      {#if reviewCount > 0 && !unreachable}<span class="window-review-pip" class:window-review-pip--shown={windowSidebar.hidden} aria-hidden={!windowSidebar.hidden} data-window-review-pip>{reviewCount}</span>{/if}
    </button>
    <span class="window-tooltip" class:window-tooltip--shown={tipShown} aria-hidden="true">{sidebarLabel}<kbd>⌃⌘S</kbd></span>
  </div>
{/if}

<style>
  .window-offline-dot { position:absolute; top:var(--space-1); right:calc(var(--space-1) - var(--border-hairline)); width:var(--desktop-status-dot-size); height:var(--desktop-status-dot-size); border-radius:var(--radius-pill); background:var(--accent-error); box-shadow:0 0 0 var(--desktop-status-dot-ring) var(--surface-1); pointer-events:none; opacity:0; transform:scale(var(--rail-grip-rest-scale)); transition:opacity var(--motion-base) var(--motion-ease), transform var(--motion-base) var(--motion-ease); }
  .window-offline-dot--shown { opacity:1; transform:none; }
  @media (prefers-reduced-motion: reduce) { .window-offline-dot { transition:none; } }

  .window-drag-region { position:absolute; top:0; left:0; right:0; height:var(--window-top-row); -webkit-app-region:drag; }
  /* The lights and the button share one centre line, the row's middle (y 26). The button
     starts where a native toolbar's first control starts (x 96), a clear gap after the lights. */
  .window-controls { position:absolute; z-index:30; top:calc((var(--window-top-row) - var(--window-control-size)) / 2); left:var(--window-controls-left); display:flex; align-items:center; gap:var(--space-2); height:var(--window-control-size); }
  /* In full screen the lights are gone, and the button takes their place (x 20). */
  :global(:root[data-window-full-screen]) .window-controls { left:calc(var(--space-4) + var(--space-1)); }
  /* A native toolbar button: the glyph alone at rest, a round fill under the pointer. */
  .window-sidebar-button { position:relative; display:grid; place-items:center; width:var(--window-control-size); height:var(--window-control-size); padding:0; border:0; border-radius:var(--radius-pill); background:transparent; color:var(--tone-secondary); cursor:pointer; -webkit-app-region:no-drag;
    transition:background var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .window-sidebar-button svg { width:var(--window-control-glyph-width); height:var(--window-control-glyph-height); }
  .window-sidebar-button:hover { background:var(--surface-hover); color:var(--tone-strong); }
  .window-sidebar-button:active { background:var(--surface-selected); }
  .window-sidebar-button:focus-visible { outline-offset:0; }
  :global(:root[data-window-inactive]) .window-sidebar-button:not(:hover) { color:var(--tone-tertiary); }
  /* While the sidebar is hidden, what waits in Review shows on the button's corner, in the
     Review badge's colours over the page. */
  .window-review-pip { position:absolute; top:0; right:-4px; min-width:var(--space-4); height:var(--space-4); padding:0 var(--space-1); border-radius:var(--radius-pill); background:linear-gradient(var(--accent-badge), var(--accent-badge)), var(--surface-1); color:var(--accent-bright); font-size:calc(var(--type-small) * 0.84); font-weight:var(--weight-title); line-height:var(--space-4); font-variant-numeric:tabular-nums; pointer-events:none;
    opacity:0; transform:scale(0.6); transition:opacity var(--motion-base) var(--motion-ease), transform var(--motion-base) var(--motion-ease); }
  .window-review-pip--shown { opacity:1; transform:none; }
  /* The button's name after a pause, under it, as macOS shows one. */
  .window-tooltip { position:absolute; top:calc(100% + 6px); left:calc(100% - var(--window-control-size)); padding:3px var(--space-2); border-radius:6px; background:var(--surface-raised); box-shadow:0 0 0 1px var(--border-raised), var(--shadow-float); color:var(--tone-primary); font-size:var(--type-small); white-space:nowrap; pointer-events:none; opacity:0; transition:opacity var(--motion-base); }
  .window-tooltip--shown { opacity:1; }
  .window-tooltip kbd { margin-left:6px; color:var(--tone-tertiary); font:inherit; }
  @media (prefers-reduced-motion: reduce) { .window-review-pip, .window-tooltip, .window-sidebar-button { transition:none; } }
</style>
