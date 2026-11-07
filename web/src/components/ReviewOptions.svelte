<script lang="ts">
  /* The review card's layouts, for the owner to click between in the running app. Shown only
   * when the address carries ?options=1 (before the #), so normal use and the rig's captures
   * never see it. It only sets one data attribute on the document's root; each layout is plain
   * CSS behind it (WorkerProposalCard.svelte, ReviewTicketCard.svelte). Kept for the next visit.
   * Remove before anything ships. */
  import SegmentedControl from "./SegmentedControl.svelte";
  const KEY = "panels.review-options";
  const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY) ?? "{}") as { layout?: string }; } catch { return {}; } })();
  let layout = $state<string | null>(saved.layout ?? "split");
  $effect(() => {
    document.documentElement.dataset.reviewLayout = layout ?? "split";
    try { localStorage.setItem(KEY, JSON.stringify({ layout })); } catch { /* the option still works */ }
  });
</script>

<div class="review-options" role="group" aria-label="Review card options">
  <span class="review-option-name">Review card</span>
  <SegmentedControl name="review-layout" bind:value={layout} options={[{ value: "split", label: "Split" }, { value: "top", label: "Top" }]} />
</div>

<style>
  /* A small floating card at the main pane's bottom right, clear of the composer. */
  .review-options { position:fixed; z-index:60; right:16px; bottom:120px; display:flex; align-items:center; gap:var(--space-2); padding:var(--space-2); border-radius:var(--radius-lg); background:var(--rail-float-surface); box-shadow:var(--rail-float-shadow); font-size:var(--type-small); color:var(--tone-secondary); }
  .review-option-name { padding-left:var(--space-1); }
  @media (max-width:720px) { .review-options { top:12px; bottom:auto; right:12px; } }
</style>
