<script lang="ts">
  /* The sidebar's tone variant, for the owner to compare in the running app. Shown only when the
   * address carries ?options=1 (before the #), so normal use and the rig's captures never see it.
   * It sets one data attribute on the document's root; the variant is plain CSS (tokens.css).
   * Kept for the next visit. Remove before anything ships. */
  import SegmentedControl from "./SegmentedControl.svelte";
  const KEY = "panels.tone-options";
  const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY) ?? "{}") as { tones?: string; sizes?: string }; } catch { return {}; } })();
  let tones = $state<string | null>(saved.tones ?? "stepup");
  let sizes = $state<string | null>(saved.sizes ?? "current");
  $effect(() => {
    document.documentElement.dataset.railTones = tones ?? "stepup";
    document.documentElement.dataset.railSizes = sizes ?? "current";
    try { localStorage.setItem(KEY, JSON.stringify({ tones, sizes })); } catch { /* the options still work */ }
  });
</script>

<div class="tone-options" role="group" aria-label="Sidebar options">
  <div class="tone-option"><span class="tone-option-name">Tones</span><SegmentedControl name="rail-tones" bind:value={tones} options={[{ value: "stepup", label: "Step up" }, { value: "two", label: "Two levels" }]} /></div>
  <div class="tone-option"><span class="tone-option-name">Sizes</span><SegmentedControl name="rail-sizes" bind:value={sizes} options={[{ value: "current", label: "Current" }, { value: "larger", label: "Larger" }]} /></div>
</div>

<style>
  .tone-options { position:fixed; z-index:60; right:16px; bottom:120px; display:flex; flex-direction:column; align-items:stretch; gap:var(--space-1); padding:var(--space-2); border-radius:var(--radius-lg); background:var(--rail-float-surface); box-shadow:var(--rail-float-shadow); font-size:var(--type-small); color:var(--tone-secondary); }
  .tone-option { display:flex; align-items:center; gap:var(--space-2); }
  .tone-option-name { width:44px; flex:none; padding-left:var(--space-1); }
  @media (max-width:720px) { .tone-options { top:12px; bottom:auto; right:12px; } }
</style>
