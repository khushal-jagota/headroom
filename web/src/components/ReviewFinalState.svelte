<script lang="ts">
  import ReviewChiefMessage from './ReviewChiefMessage.svelte';
  import ErrorLine from './ErrorLine.svelte';

  let { returnLabel = 'Back to review', waitingCount, runningWorkerCount, returning = false, returnError = null, onReturn }: {
    returnLabel?: string;
    waitingCount: number | null;
    runningWorkerCount: number | null;
    returning?: boolean;
    returnError?: unknown;
    onReturn: () => void;
  } = $props();
  let messageBusy = $state(false);
</script>

<div class="review-final" data-review-empty data-review-final-count={waitingCount ?? 'unknown'}>
  <div class="settled">
    <div class="hero">
      <svg class="sheets" viewBox="0 0 160 160" fill="none" aria-hidden="true">
        <g stroke="currentColor" stroke-width="1.2" stroke-linejoin="round">
          <path opacity=".4" d="M12 12h55l20 20v80a6 6 0 0 1-6 6H18a6 6 0 0 1-6-6V18a6 6 0 0 1 6-6Zm55 0v20h20M27 43h36M27 55h36M27 67h24"/>
          <path fill="var(--surface-1)" opacity=".7" d="M43 31h55l20 20v80a6 6 0 0 1-6 6H49a6 6 0 0 1-6-6V37a6 6 0 0 1 6-6Zm55 0v20h20M58 62h36M58 74h36M58 86h24"/>
          <path fill="var(--surface-1)" d="M76 51h55l20 20v80a6 6 0 0 1-6 6H82a6 6 0 0 1-6-6V57a6 6 0 0 1 6-6Zm55 0v20h20M91 82h20M91 94h44M91 106h20M104 118h31M104 130h31"/>
        </g>
      </svg>
      <div class="copy">
        <h2 data-review-final-status role="status">{waitingCount === null ? 'Review is unavailable right now.' : waitingCount === 0 ? 'Nothing needs you right now.' : `${waitingCount} ${waitingCount === 1 ? 'thing awaits' : 'things await'} review.`}</h2>
        <div class="worker-slot">
          {#if runningWorkerCount !== null}<div class="review-empty-meta">{runningWorkerCount} {runningWorkerCount === 1 ? 'agent' : 'agents'} in progress</div>{/if}
        </div>
      </div>
    </div>
    <ReviewChiefMessage onBusy={value => messageBusy = value} />
    <div class="return-slot">
      <button type="button" class:primary={waitingCount !== null && waitingCount > 0} data-review-return disabled={returning || messageBusy} onclick={onReturn}>{returnLabel}</button>
    </div>
    {#if returnError}<ErrorLine error={returnError} />{/if}
  </div>
</div>

<style>
  .review-final { min-height:calc(100dvh - (var(--space-7) * 4)); display:grid; place-items:center; padding:var(--space-7) var(--page-gutter); width:100%; }
  .settled { width:100%; max-width:var(--measure-index); }
  .hero { display:flex; align-items:center; gap:var(--space-6); min-height:calc(var(--space-7)*3); margin-bottom:var(--space-5); }
  .sheets { width:calc(var(--space-7)*3); height:calc(var(--space-7)*3); flex:none; color:var(--text-faintest); }
  .copy { flex:1; min-width:0; }
  h2 { min-height:calc(var(--space-6)*2 + var(--space-4)); display:flex; align-items:flex-end; font-size:var(--type-hero); line-height:1.3; letter-spacing:var(--tracking-title); font-weight:500; margin:0; color:var(--text-strong); }
  .worker-slot { height:var(--space-6); margin-top:var(--space-2); }
  .review-empty-meta { font-size:var(--type-sm); color:var(--text-muted); font-variant-numeric:tabular-nums; }
  .return-slot { height:var(--space-7); margin-top:var(--space-2); display:flex; align-items:flex-start; }
  button { font:inherit; font-size:var(--type-sm); line-height:1.6; padding:var(--space-2) var(--space-3); border:0; border-radius:var(--radius-pill); background:none; color:var(--text-faint); cursor:pointer; }
  button:hover:not(:disabled) { background-image:var(--interaction-hover); color:var(--text-strong); }
  button.primary { font-weight:500; padding:var(--space-2) calc(var(--space-3)*1.5); background:var(--accent-bright); color:var(--surface-ink); }
  button.primary:hover:not(:disabled) { background-image:none; color:var(--surface-ink); }
  button:disabled { opacity:.5; cursor:default; }
  @media(max-width:600px) {
    .review-final { padding:var(--space-5) var(--space-4); }
    .hero { gap:var(--space-4); }
    .sheets { width:calc(var(--space-7)*2); height:calc(var(--space-7)*2); }
    h2 { font-size:var(--type-lg); }
    .review-empty-meta { font-size:var(--type-xs); }
  }
</style>
