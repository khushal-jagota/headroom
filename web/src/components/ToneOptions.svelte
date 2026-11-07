<script lang="ts">
  /* The sidebar's tone variant, for the owner to compare in the running app. Shown only when the
   * address carries ?options=1 (before the #), so normal use and the rig's captures never see it.
   * It sets one data attribute on the document's root; the variant is plain CSS (tokens.css).
   * Kept for the next visit. Remove before anything ships. */
  import SegmentedControl from "./SegmentedControl.svelte";
  const KEY = "panels.tone-options";
  const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY) ?? "{}") as { tones?: string }; } catch { return {}; } })();
  let tones = $state<string | null>(saved.tones ?? "two");
  $effect(() => {
    document.documentElement.dataset.railTones = tones ?? "two";
    try { localStorage.setItem(KEY, JSON.stringify({ tones })); } catch { /* the option still works */ }
  });
</script>

<div class="tone-options" role="group" aria-label="Sidebar tone options">
  <span class="tone-option-name">Sidebar tones</span>
  <SegmentedControl name="rail-tones" bind:value={tones} options={[{ value: "two", label: "Two levels" }, { value: "stepup", label: "Step up" }]} />
</div>

<style>
  .tone-options { position:fixed; z-index:60; right:16px; bottom:120px; display:flex; align-items:center; gap:var(--space-2); padding:var(--space-2); border-radius:var(--radius-lg); background:var(--rail-float-surface); box-shadow:var(--rail-float-shadow); font-size:var(--type-small); color:var(--tone-secondary); }
  .tone-option-name { padding-left:var(--space-1); }
  @media (max-width:720px) { .tone-options { top:12px; bottom:auto; right:12px; } }
</style>
