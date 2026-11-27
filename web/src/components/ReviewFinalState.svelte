<script lang="ts">
  import ReviewChiefMessage from './ReviewChiefMessage.svelte';
  import ErrorLine from './ErrorLine.svelte';
  import Button from './Button.svelte';
  import WindowPageRow from './desktopWindow/WindowPageRow.svelte';
  import { reviewFinalStatus, SKIPPED_RETURN_LABEL } from '../lib/review/return';
  import type { ReviewSummary } from '../lib/navigation/contracts';
  import { workspaceAddress } from '../lib/workspaceAddress';

  let { summary, pending = false, runningWorkerCount, returning = false, returnError = null, onReturn }: {
    summary: ReviewSummary | null;
    /** The queue view is one request behind. With no summary of its own to keep, the hero stays blank. */
    pending?: boolean;
    runningWorkerCount: number | null;
    returning?: boolean;
    returnError?: unknown;
    onReturn: () => void;
  } = $props();
  let messageBusy = $state(false);
  const blank = $derived(pending && !summary);
  const status = $derived(blank ? ['\u00a0','\u00a0'] as const : reviewFinalStatus(summary));
  const count = $derived(summary ? summary.new + summary.skipped : null);
  const returnLabel = $derived(summary?.new ? 'Back to review' : SKIPPED_RETURN_LABEL);
</script>

<WindowPageRow>
  <div class="review-final-band column">
    <div data-window-row-actions>
      <Button variant="ghost" href={workspaceAddress({kind:'chief'})}>Open Chief</Button>
    </div>
  </div>
</WindowPageRow>
<div class="review-final" data-review-empty data-review-final-count={blank ? 'pending' : count ?? 'unknown'}>
  <div class="review-final-hero">
    <div class="column">
      <h2 data-review-final-status role="status"><span class="lead">{status[0]}</span> <span class="detail">{status[1]}</span></h2>
      {#if count !== null && count > 0}
        <div class="review-final-return">
          <Button variant="secondary" class="review-final-return-button" data-review-return disabled={returning || messageBusy} onclick={onReturn}>{returnLabel}</Button>
        </div>
      {/if}
      {#if returnError}<div class="review-final-error"><ErrorLine error={returnError} /></div>{/if}
    </div>
  </div>
  <div class="review-final-dock column">
    {#if runningWorkerCount !== null && runningWorkerCount > 0}
      <p class="review-final-working">{runningWorkerCount} {runningWorkerCount === 1 ? 'agent' : 'agents'} working</p>
    {/if}
    <ReviewChiefMessage onBusy={value => messageBusy = value} />
  </div>
</div>

<style>
  .column { width:100%; padding-inline:var(--page-gutter); }
  .review-final-band { min-height:var(--page-band-height); display:flex; align-items:center; justify-content:flex-end; flex:none; }
  .review-final-band > div { margin-right:calc(var(--space-3) * -1); }
  .review-final { flex:1; min-height:0; display:flex; flex-direction:column; width:100%; }
  .review-final-hero { flex:1; min-height:0; overflow-y:auto; scrollbar-width:none; }
  .review-final-hero .column { padding-top:max(var(--space-7), calc(30cqh - var(--page-top) - var(--page-band-height))); }
  /* The desktop window lifts the action band out of the page flow. */
  :global(:root[data-desktop-window]) .review-final-hero .column { padding-top:max(var(--space-7), calc(30cqh - var(--page-top))); }
  h2 { margin:0; font-size:var(--type-review-hero); line-height:1.1; letter-spacing:var(--tracking-review-hero); font-variant-numeric:tabular-nums; }
  h2 span { display:block; }
  .lead { color:var(--tone-strong); font-weight:700; }
  .detail { color:var(--tone-tertiary); font-weight:600; }
  .review-final-return { margin-top:var(--space-5); }
  .review-final-return :global(.review-final-return-button) { height:var(--button-main-height); padding-inline:calc(var(--space-3) * 1.5); }
  .review-final-error { margin-top:var(--space-3); }
  .review-final-dock { flex:none; padding-bottom:var(--space-5); }
  .review-final-working { margin:0 0 var(--space-3); color:var(--tone-tertiary); font-size:var(--type-small); font-weight:var(--weight-small); font-variant-numeric:tabular-nums; }
  @container (max-width:720px) {
    .column { padding-inline:var(--space-4); }
    .review-final-band > div { margin-right:0; }
  }
  @container (max-width:600px) {
    h2 { font-size:var(--type-review-hero-compact); }
    .review-final-dock { padding-bottom:var(--space-3); }
  }
</style>
