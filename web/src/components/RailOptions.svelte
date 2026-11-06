<script lang="ts">
  /* The sidebar's options, for the owner to click between in the running app. Shown only when
   * the address carries ?options=1 (before the #), so normal use and the rig's captures never
   * see it. It only sets two data attributes on the document's root; each option is plain CSS
   * behind them (BoardRoute.svelte, tokens.css). The choice is kept for the next visit. */
  import SegmentedControl from "./SegmentedControl.svelte";
  const KEY = "panels.rail-options";
  const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY) ?? "{}") as { divider?: string; dock?: string }; } catch { return {}; } })();
  let divider = $state<string | null>(saved.divider ?? "off");
  let dock = $state<string | null>(saved.dock ?? "bubble");
  $effect(() => {
    const root = document.documentElement;
    root.dataset.railDivider = divider ?? "off";
    root.dataset.railDock = dock ?? "bubble";
    try { localStorage.setItem(KEY, JSON.stringify({ divider, dock })); } catch { /* the options still work */ }
  });
</script>

<div class="rail-options" role="group" aria-label="Sidebar options">
  <div class="rail-option"><span class="rail-option-name">Line</span><SegmentedControl name="rail-divider" bind:value={divider} options={[{ value: "off", label: "Off" }, { value: "on", label: "On" }]} /></div>
  <div class="rail-option"><span class="rail-option-name">Dock</span><SegmentedControl name="rail-dock" bind:value={dock} options={[{ value: "band", label: "Band" }, { value: "bubble", label: "Bubble" }, { value: "split", label: "Split" }, { value: "page", label: "Page" }]} /></div>
</div>

<style>
  /* A small floating card at the main pane's bottom right, clear of the composer. */
  .rail-options { position:fixed; z-index:60; right:16px; bottom:120px; display:flex; flex-direction:column; gap:var(--space-1); padding:var(--space-2); border-radius:var(--radius-lg); background:var(--rail-float-surface); box-shadow:var(--rail-float-shadow); font-size:var(--type-small); color:var(--tone-secondary); }
  .rail-option { display:flex; align-items:center; gap:var(--space-2); }
  .rail-option-name { width:32px; flex:none; }
  /* A phone keeps its composer and bottom bar at the foot, so the card floats at the top. */
  @media (max-width:720px) { .rail-options { top:12px; bottom:auto; right:12px; } }
</style>
