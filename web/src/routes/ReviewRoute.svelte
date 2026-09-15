<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { queries } from "../lib/queryCatalogue";
  import type { ReviewItem } from "../lib/types";
  import ResourceState from "../components/ResourceState.svelte";
  import ReviewProposalCard from "../components/ReviewProposalCard.svelte";

  const review = createQuery(() => queries.review());

  let skipped = $state<Record<string, boolean>>({});
  // The route's own element. The approve shortcut's lookup is scoped to it, so a
  // card mounted by another screen at the same time can never be the one a Review
  // keystroke reaches.
  let screenElement = $state<HTMLElement | null>(null);

  function itemKey(item: ReviewItem): string {
    return `${item.ticket_id}:${item.field}`;
  }

  let items = $derived(review.data?.items || []);
  let runningWorkerCount = $derived(review.data?.running_worker_count ?? 0);
  let currentItem = $derived.by<ReviewItem | null>(() => {
    if (!items.length) return null;
    const live = items.filter((item) => !skipped[itemKey(item)]);
    return live[0] || null;
  });

  function runningWorkersText(count: number): string {
    return `${count} ${count === 1 ? "agent" : "agents"} in progress`;
  }

  function skip(item: ReviewItem): void {
    skipped = { ...skipped, [itemKey(item)]: true };
  }

  function openTicket(item: ReviewItem): void {
    window.location.hash = workspaceAddress({ kind: "ticket", id: item.ticket_id });
  }

  // The card says a decision has left the queue — approved, sent back, or already
  // gone by the time its Ticket was read. Re-read the queue so the next ask comes up.
  function proposalResolved(): void {
    void review.refetch().catch(() => undefined);
  }

  // Global review shortcuts (approved addition): s = skip, o = open ticket,
  // Cmd/Ctrl+Enter = approve the current ask. They fire only when the keystroke
  // did not originate in an editable control and was not already handled there —
  // InlineEdit's own Cmd/Ctrl+Enter preventDefaults and blurs to <body>, so an
  // activeElement check alone would let that approve by accident.
  function typingTarget(event: KeyboardEvent): boolean {
    const target = event.target as HTMLElement | null;
    if (!target) return false;
    if (target.isContentEditable) return true;
    return ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName);
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    const item = currentItem;
    if (!item) return;
    // Ignore auto-repeat: holding a key must not skip/approve through the decisions.
    if (event.repeat || event.defaultPrevented || typingTarget(event)) return;

    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      const button = screenElement?.querySelector<HTMLButtonElement>(
        "[data-review-card] [data-accept]"
      );
      if (button && !button.disabled) {
        event.preventDefault();
        button.click();
      }
      return;
    }

    if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;

    if (event.key === "s" || event.key === "S") {
      event.preventDefault();
      skip(item);
    } else if (event.key === "o" || event.key === "O") {
      event.preventDefault();
      openTicket(item);
    }
  }

  $effect(() => {
    window.addEventListener("keydown", onWindowKeydown);
    return () => window.removeEventListener("keydown", onWindowKeydown);
  });
</script>

{#snippet proposalKeys()}
  <div class="review-keys">
    <kbd>⌘↩</kbd> approve &nbsp;·&nbsp; <kbd>S</kbd> skip &nbsp;·&nbsp; <kbd>O</kbd> open ticket
  </div>
{/snippet}

<section class="review-screen" data-screen="review" bind:this={screenElement}>
  <ResourceState error={review.error} loading={review.isFetching} hasData={Boolean(review.data)} loadingText="Loading review...">
    {#if !items.length}
      <div class="review-empty-state" data-review-empty>
        <div class="review-empty-mark" aria-hidden="true"><span></span></div>
        <div class="review-empty-text">There is nothing to review right now.</div>
        <div class="review-empty-meta">{runningWorkersText(runningWorkerCount)}</div>
      </div>
    {:else if currentItem}
      {@const proposal = currentItem}
      {#key itemKey(proposal)}
        <ReviewProposalCard
          ticketId={proposal.ticket_id}
          field={proposal.field}
          title={proposal.title}
          onSkip={() => skip(proposal)}
          onResolved={proposalResolved}
          footer={proposalKeys}
        />
      {/key}
    {:else}
      <div class="review-empty-state" data-review-empty>
        <div class="review-empty-mark" aria-hidden="true"><span></span></div>
        <div class="review-empty-text">There is nothing to review right now.</div>
        <div class="review-empty-meta">{runningWorkersText(runningWorkerCount)}</div>
      </div>
    {/if}
  </ResourceState>
</section>
