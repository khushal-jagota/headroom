<script lang="ts">
  /** One stop on Atlas's review walk.
   *
   * The world has sailed to a worker and raised the panel; what stands in it is the
   * Review screen's own proposal card, not a copy of one. This component adds only
   * the walk: where in the queue this stop is, and the way forward and back. When
   * the proposal is decided the stop says so, and the route moves the walk on.
   */
  import ReviewProposalCard from "../ReviewProposalCard.svelte";

  let {
    ticketId,
    field,
    position,
    total,
    onPrevious,
    onSkip,
    onResolved
  }: {
    ticketId: string;
    field: string;
    /** 1-based place in the walk. */
    position: number;
    total: number;
    onPrevious: () => void;
    onSkip: () => void;
    onResolved: () => void;
  } = $props();
</script>

<div class="atlas-review-stop" data-atlas-review-stop>
  <div class="atlas-review-stop-body">
    <ReviewProposalCard {ticketId} {field} {onResolved} />
  </div>

  <div class="atlas-review-walk" data-atlas-walk-bar>
    <button type="button" data-atlas-walk-previous onclick={onPrevious}>&lsaquo; Prev</button>
    <span class="atlas-review-walk-position" data-atlas-walk-position>{position} of {total}</span>
    <button type="button" data-atlas-walk-skip onclick={onSkip}>Skip &rsaquo;</button>
  </div>
</div>

<style>
  /* The stop fills the panel: the ask scrolls, the walk bar is pinned to the foot. */
  .atlas-review-stop {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .atlas-review-stop-body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 0 var(--space-5) var(--space-5);
  }

  .atlas-review-walk {
    flex: none;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-3) var(--space-5);
    border-top: var(--border-hairline) solid var(--border-color);
    background: var(--surface-1);
  }

  .atlas-review-walk button {
    background: none;
    border: 0;
    padding: 0;
    color: var(--text-faintest);
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    cursor: pointer;
    transition: color var(--motion-base) var(--motion-ease);
  }

  .atlas-review-walk button:hover {
    color: var(--text-default);
  }

  .atlas-review-walk-position {
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    color: var(--text-faintest);
  }
</style>
