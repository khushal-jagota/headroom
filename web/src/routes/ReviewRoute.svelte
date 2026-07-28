<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { fieldStageVisualStateFor, gatingFieldFor, lifecycleFor } from "../lib/lifecycle";
  import type {
    ReviewItem,
    ReviewProposalItem,
    TicketDetail
  } from "../lib/types";
  import Button from "../components/Button.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";

  const review = createQuery(() => queries.review());
  const manifest = createQuery(() => queries.workerTypeManifests());

  let skipped = $state<Record<string, boolean>>({});
  let revisionDraft = $state("");
  let revisionError = $state<unknown>(null);
  let revisionBusy = $state(false);
  let revisionDecisionKey = $state<string | null>(null);
  const staleRefreshRequests = new Set<string>();

  function itemKey(item: ReviewItem): string {
    return item.review_item_type === "proposal"
      ? `${item.ticket_id}:${item.field}`
      : `${item.ticket_id}:needs_user`;
  }

  let items = $derived(review.data?.items || []);
  let runningWorkerCount = $derived(review.data?.running_worker_count ?? 0);
  let currentItem = $derived.by<ReviewItem | null>(() => {
    if (!items.length) return null;
    const live = items.filter((item) => !skipped[itemKey(item)]);
    return live[0] || null;
  });
  let currentProposal = $derived.by<ReviewProposalItem | null>(() =>
    currentItem?.review_item_type === "proposal" ? currentItem : null
  );

  // The Ticket behind the proposal on screen. The key follows the proposal, so
  // moving to the next ask switches the query rather than re-opening a handle.
  const detail = createQuery(() => ({
    ...queries.ticket(currentProposal?.ticket_id ?? ""),
    enabled: currentProposal !== null
  }));

  // Per-Worker-type lifecycle for the current Review decision's detail. Null while the
  // manifest or the detail is still loading OR when the detail's worker_type is
  // absent from a loaded manifest; the markup tells those apart (Codex F3).
  let detailWorkerType = $derived(
    typeof detail.data?.worker_type === "string" ? (detail.data.worker_type as string) : null
  );
  let lc = $derived(lifecycleFor(manifest.data, detailWorkerType));
  let manifestMissingWorkerType = $derived(
    Boolean(
      detailWorkerType &&
        manifest.data &&
        !manifest.data.worker_types.some((item) => item.worker_type === detailWorkerType)
    )
  );

  function runningWorkersText(count: number): string {
    return `${count} ${count === 1 ? "agent" : "agents"} in progress`;
  }

  function isStale(item: ReviewProposalItem, ticketDetail: TicketDetail): boolean {
    const field = proposalField(item);
    if (!field) return true;
    if (!ticketDetail.fields?.[field]?.proposal) return true;
    return gatingFieldFor(lc, String(ticketDetail.stage)) !== field;
  }

  function proposalField(item: ReviewProposalItem): string | null {
    if (lc?.fieldIds.includes(item.field)) return item.field;
    return null;
  }

  $effect(() => {
    const item = currentProposal;
    const ticketDetail = detail.data;
    if (item && ticketDetail && isStale(item, ticketDetail)) {
      const key = itemKey(item);
      if (!staleRefreshRequests.has(key)) {
        staleRefreshRequests.add(key);
        void review.refetch().catch(() => undefined);
      }
    }
  });

  $effect(() => {
    const key = currentProposal ? itemKey(currentProposal) : null;
    if (key !== revisionDecisionKey) {
      revisionDecisionKey = key;
      revisionDraft = "";
      revisionError = null;
      revisionBusy = false;
    }
  });

  function skip(item: ReviewItem): void {
    skipped = { ...skipped, [itemKey(item)]: true };
  }

  function openTicket(item: ReviewItem): void {
    window.location.hash = `#/ticket/${item.ticket_id}`;
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
      const button = document.querySelector<HTMLButtonElement>(
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

  function accept(
    item: ReviewProposalItem,
    payload: Record<string, unknown>
  ): Promise<unknown> {
    const field = proposalField(item);
    if (!field) return Promise.reject(new Error("Review decision is not a Ticket field"));
    const key = itemKey(item);
    staleRefreshRequests.add(key);
    return mutateJson(`/api/tickets/${item.ticket_id}/accept/${field}`, {
      method: "POST",
      body: payload
    }).catch((error) => {
      staleRefreshRequests.delete(key);
      throw error;
    });
  }

  function saveTitle(item: ReviewProposalItem, title: string): Promise<unknown> {
    return mutateJson(`/api/tickets/${item.ticket_id}`, {
      method: "PATCH",
      body: { title }
    });
  }


  async function returnForRevision(item: ReviewProposalItem): Promise<void> {
    const message = revisionDraft.trim();
    if (!message || revisionBusy) return;
    revisionError = null;
    revisionBusy = true;
    const key = itemKey(item);
    staleRefreshRequests.add(key);
    try {
      await mutateJson(`/api/tickets/${item.ticket_id}/return-for-revision`, {
        method: "POST",
        body: { message }
      });
      revisionDraft = "";
    } catch (err) {
      staleRefreshRequests.delete(key);
      revisionError = err;
    } finally {
      revisionBusy = false;
    }
  }
</script>

<section class="review-screen" data-screen="review">
  <ResourceState error={review.error} loading={review.isFetching} hasData={Boolean(review.data)} loadingText="Loading review...">
    {#if !items.length}
      <div class="review-empty-state" data-review-empty>
        <div class="review-empty-mark" aria-hidden="true"><span></span></div>
        <div class="review-empty-text">There is nothing to review right now.</div>
        <div class="review-empty-meta">{runningWorkersText(runningWorkerCount)}</div>
      </div>
    {:else if currentItem}
      {#if currentItem.review_item_type === "needs_user"}
        {#key itemKey(currentItem)}
          <div
            class="modern-review-content"
            data-review-card
            data-review-item-type="needs_user"
            data-ticket-id={currentItem.ticket_id}
          >
            <div class="review-ticket-decision-line review-arrive review-arrive--1">
              <button data-skip="" onclick={() => skip(currentItem)}>Skip &rsaquo;</button>
              <a data-open-ticket href={`#/ticket/${currentItem.ticket_id}`}>Open ticket &rsaquo;</a>
            </div>

            <div class="review-ticket-title review-arrive review-arrive--2">
              {currentItem.title}
            </div>

            <div class="review-context review-arrive review-arrive--3">
              <div class="review-context-label review-needs-user-label">
                Worker needs your input
              </div>
            </div>
          </div>
        {/key}

        <div class="review-keys">
          <kbd>S</kbd> skip &nbsp;·&nbsp; <kbd>O</kbd> open ticket
        </div>
      {:else}
        {@const proposal = currentItem}
        {#if detail.error}
          <div>
            <ErrorLine error={detail.error} />
            <div class="quiet-line">{proposal.title}</div>
            <div class="review-card-actions">
              <Button variant="quiet" data-skip="" onclick={() => skip(proposal)}>Skip</Button>
              <a data-open-ticket href={`#/ticket/${proposal.ticket_id}`}>open ticket</a>
            </div>
          </div>
        {:else if detail.isFetching && !detail.data}
          <div class="quiet-line">Loading approval...</div>
        {:else if detail.data && (manifest.error || manifestMissingWorkerType)}
          {@const ticketDetail = detail.data}
          <div data-review-manifest-error>
            <ErrorLine
              error={manifest.error ?? { code: "unknown_worker_type", message: `no manifest for Worker type "${ticketDetail.worker_type}"` }}
            />
            <div class="review-card-actions">
              <Button variant="quiet" data-skip="" onclick={() => skip(proposal)}>Skip</Button>
              <a data-open-ticket href={`#/ticket/${proposal.ticket_id}`}>open ticket</a>
            </div>
          </div>
        {:else if detail.data}
          {@const ticketDetail = detail.data}
          {@const field = proposalField(proposal)}
          {#if isStale(proposal, ticketDetail)}
            <div class="quiet-line">Loading approval...</div>
          {:else}
            {#key itemKey(proposal)}
              <div
                class="modern-review-content"
                data-review-card
                data-review-item-type="proposal"
                data-ticket-id={currentItem.ticket_id}
                data-field={currentItem.field}
              >
                <div class="review-ticket-decision-line review-arrive review-arrive--1">
                  <button data-skip="" onclick={() => skip(proposal)}>Skip &rsaquo;</button>
                  <a data-open-ticket href={`#/ticket/${proposal.ticket_id}`}>Open ticket &rsaquo;</a>
                </div>

                <div class="review-ticket-title review-arrive review-arrive--2">
                  <InlineEdit
                    value={ticketDetail.title}
                    placeholder="Untitled"
                    onSave={(raw) => saveTitle(proposal, raw)}
                  />
                </div>

                <div class="review-arrive review-arrive--3">
                  {#if field}
                    <TicketStageSection
                      variant="review"
                      name={field}
                      slot={ticketDetail.fields[field]}
                      lifecycle={lc}
                      ticketStage={ticketDetail.stage}
                      ceiling={ticketDetail.ceiling}
                      stageState={fieldStageVisualStateFor(lc, ticketDetail, field)}
                      recap={ticketDetail.recap}
                      showRecap
                      onAccept={(payload) => accept(proposal, payload)}
                    />
                  {/if}
                </div>

                {#if field !== "kickoff"}
                  <div class="review-revise review-arrive review-arrive--4" data-review-revision>
                    <div class="review-revision-box">
                      <textarea
                        class="review-revision-input"
                        data-review-revision-input
                        rows="1"
                        placeholder="Or tell the worker what to change..."
                        bind:value={revisionDraft}
                        disabled={revisionBusy}
                        onkeydown={(event) => {
                          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                            event.preventDefault();
                            void returnForRevision(proposal);
                          }
                        }}
                      ></textarea>
                      <Button
                        variant="quiet"
                        data-review-revision-send=""
                        disabled={revisionBusy || !revisionDraft.trim()}
                        onclick={() => void returnForRevision(proposal)}
                      >
                        Send back
                      </Button>
                    </div>
                    {#if revisionError}
                      <ErrorLine error={revisionError} />
                    {/if}
                  </div>
                {/if}
              </div>
            {/key}

            <div class="review-keys">
              <kbd>⌘↩</kbd> approve &nbsp;·&nbsp; <kbd>S</kbd> skip &nbsp;·&nbsp; <kbd>O</kbd> open ticket
            </div>
          {/if}
        {/if}
      {/if}
    {:else}
      <div class="review-empty-state" data-review-empty>
        <div class="review-empty-mark" aria-hidden="true"><span></span></div>
        <div class="review-empty-text">There is nothing to review right now.</div>
        <div class="review-empty-meta">{runningWorkersText(runningWorkerCount)}</div>
      </div>
    {/if}
  </ResourceState>
</section>
