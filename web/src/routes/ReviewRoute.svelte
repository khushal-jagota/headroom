<script lang="ts">
  import { onDestroy } from "svelte";
  import {
    mutateJsonWithResourceEffect,
    resourceCatalogue,
    type ResourceHandle
  } from "../lib/resourceCatalogue";
  import { fieldStageVisualStateFor, gatingFieldFor, lifecycleFor } from "../lib/lifecycle";
  import type {
    ReviewTicketDecision,
    TicketDetail
  } from "../lib/types";
  import Button from "../components/Button.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";

  const review = resourceCatalogue.review();
  const manifest = resourceCatalogue.workerTypeManifests();

  let skipped = $state<Record<string, boolean>>({});
  let detailResource = $state<ResourceHandle<TicketDetail> | null>(null);
  let detailError = $state<unknown>(null);
  let revisionDraft = $state("");
  let revisionError = $state<unknown>(null);
  let revisionBusy = $state(false);
  let revisionDecisionKey = $state<string | null>(null);
  const staleRefreshRequests = new Set<string>();

  function decisionKey(decision: ReviewTicketDecision): string {
    return `${decision.ticket_id}:${decision.field}`;
  }

  let decisions = $derived(review.data?.ticket_decisions || []);
  let helpRequests = $derived(review.data?.user_help_requests || []);
  let runningWorkerCount = $derived(review.data?.running_worker_count ?? 0);
  let currentDecision = $derived.by<ReviewTicketDecision | null>(() => {
    if (!decisions.length) return null;
    const live = decisions.filter((decision) => !skipped[decisionKey(decision)]);
    return live[0] || null;
  });

  // Per-Worker-type lifecycle for the current Review decision's detail. Null while the
  // manifest or the detail is still loading OR when the detail's worker_type is
  // absent from a loaded manifest; the markup tells those apart (Codex F3).
  let detailWorkerType = $derived(
    typeof detailResource?.data?.worker_type === "string"
      ? (detailResource.data.worker_type as string)
      : null
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

  $effect(() => {
    const decision = currentDecision;
    detailError = null;
    const handle = decision
      ? resourceCatalogue.ticket(decision.ticket_id)
      : null;
    detailResource = handle;
    return () => handle?.dispose();
  });

  function isStale(decision: ReviewTicketDecision, detail: TicketDetail): boolean {
    const field = decisionField(decision);
    if (!field) return true;
    if (!detail.fields?.[field]?.proposal) return true;
    return gatingFieldFor(lc, String(detail.stage)) !== field;
  }

  function decisionField(decision: ReviewTicketDecision): string | null {
    if (lc?.fieldIds.includes(decision.field)) return decision.field;
    return null;
  }

  $effect(() => {
    const decision = currentDecision;
    const detail = detailResource?.data;
    if (decision && detail && isStale(decision, detail)) {
      const key = decisionKey(decision);
      if (!staleRefreshRequests.has(key)) {
        staleRefreshRequests.add(key);
        void review.refresh().catch(() => undefined);
      }
    }
    if (detailResource?.error) detailError = detailResource.error;
  });

  $effect(() => {
    const key = currentDecision ? decisionKey(currentDecision) : null;
    if (key !== revisionDecisionKey) {
      revisionDecisionKey = key;
      revisionDraft = "";
      revisionError = null;
      revisionBusy = false;
    }
  });

  function skip(decision: ReviewTicketDecision): void {
    skipped = { ...skipped, [decisionKey(decision)]: true };
  }

  function openTicket(decision: ReviewTicketDecision): void {
    window.location.hash = `#/ticket/${decision.ticket_id}`;
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
    const decision = currentDecision;
    if (!decision) return;
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
      skip(decision);
    } else if (event.key === "o" || event.key === "O") {
      event.preventDefault();
      openTicket(decision);
    }
  }

  $effect(() => {
    window.addEventListener("keydown", onWindowKeydown);
    return () => window.removeEventListener("keydown", onWindowKeydown);
  });

  function accept(
    decision: ReviewTicketDecision,
    payload: Record<string, unknown>
  ): Promise<unknown> {
    const field = decisionField(decision);
    if (!field) return Promise.reject(new Error("Review decision is not a Ticket field"));
    const key = decisionKey(decision);
    staleRefreshRequests.add(key);
    return mutateJsonWithResourceEffect(
      `/api/tickets/${decision.ticket_id}/accept/${field}`,
      { method: "POST", body: payload },
      { kind: "reviewTicketAccepted", ticketId: decision.ticket_id }
    ).catch((error) => {
      staleRefreshRequests.delete(key);
      throw error;
    });
  }

  function saveTitle(decision: ReviewTicketDecision, title: string): Promise<unknown> {
    return mutateJsonWithResourceEffect(
      `/api/tickets/${decision.ticket_id}`,
      { method: "PATCH", body: { title } },
      { kind: "ticketTitleChanged", ticketId: decision.ticket_id }
    );
  }


  async function returnForRevision(decision: ReviewTicketDecision): Promise<void> {
    const message = revisionDraft.trim();
    if (!message || revisionBusy) return;
    revisionError = null;
    revisionBusy = true;
    const key = decisionKey(decision);
    staleRefreshRequests.add(key);
    try {
      await mutateJsonWithResourceEffect(
        `/api/tickets/${decision.ticket_id}/return-for-revision`,
        { method: "POST", body: { message } },
        { kind: "reviewTicketReturnedForRevision", ticketId: decision.ticket_id }
      );
      revisionDraft = "";
    } catch (err) {
      staleRefreshRequests.delete(key);
      revisionError = err;
    } finally {
      revisionBusy = false;
    }
  }

  onDestroy(() => {
    review.dispose();
    detailResource?.dispose();
    manifest.dispose();
  });
</script>

<section class="review-screen" data-screen="review">
  <ResourceState error={review.error} loading={review.loading} hasData={Boolean(review.data)} loadingText="Loading review...">
    {#if !decisions.length && !helpRequests.length}
    <div class="review-empty-state" data-review-empty>
      <div class="review-empty-mark" aria-hidden="true"><span></span></div>
      <div class="review-empty-text">There is nothing to review right now.</div>
      <div class="review-empty-meta">{runningWorkersText(runningWorkerCount)}</div>
    </div>
  {:else}
    {#if helpRequests.length}
      <section class="review-help-requests" data-review-help-requests aria-label="User help requests">
        <div class="review-help-heading">User help requested</div>
        {#each helpRequests as request}
          <article class="review-help-card" data-review-help-card data-ticket-id={request.ticket_id}>
            <div class="review-help-card-copy">
              <div class="review-help-card-label">Worker is waiting for you</div>
              <div class="review-help-card-title">{request.title}</div>
            </div>
            <a data-open-ticket href={`#/ticket/${request.ticket_id}`}>Open ticket &rsaquo;</a>
          </article>
        {/each}
      </section>
    {/if}
    {#if currentDecision}
    {@const decision = currentDecision}
    {#if detailError}
      <div>
        <ErrorLine error={detailError} />
        <div class="quiet-line">{decision.title}</div>
        <div class="review-card-actions">
          <Button variant="quiet" data-skip="" onclick={() => skip(decision)}>Skip</Button>
          <a data-open-ticket href={`#/ticket/${decision.ticket_id}`}>open ticket</a>
        </div>
      </div>
    {:else if detailResource?.loading && !detailResource.data}
      <div class="quiet-line">Loading approval...</div>
    {:else if detailResource?.data && (manifest.error || manifestMissingWorkerType)}
      {@const detail = detailResource.data}
      <div data-review-manifest-error>
        <ErrorLine
          error={manifest.error ?? { code: "unknown_worker_type", message: `no manifest for Worker type "${detail.worker_type}"` }}
        />
        <div class="review-card-actions">
          <Button variant="quiet" data-skip="" onclick={() => skip(decision)}>Skip</Button>
          <a data-open-ticket href={`#/ticket/${decision.ticket_id}`}>open ticket</a>
        </div>
      </div>
    {:else if detailResource?.data}
      {@const detail = detailResource.data}
      {@const field = decisionField(decision)}
      {#if isStale(decision, detail)}
        <div class="quiet-line">Loading approval...</div>
      {:else}
        {#key decisionKey(decision)}
          <div
            class="modern-review-content"
            data-review-card
            data-ticket-id={decision.ticket_id}
            data-field={decision.field}
          >
            <div class="review-ticket-decision-line review-arrive review-arrive--1">
              <button data-skip="" onclick={() => skip(decision)}>Skip &rsaquo;</button>
              <a data-open-ticket href={`#/ticket/${decision.ticket_id}`}>Open ticket &rsaquo;</a>
            </div>

            <div class="review-ticket-title review-arrive review-arrive--2">
              <InlineEdit
                value={detail.title}
                placeholder="Untitled"
                onSave={(raw) => saveTitle(decision, raw)}
              />
            </div>

            <div class="review-arrive review-arrive--3">
              {#if field}
                <TicketStageSection
                  variant="review"
                  name={field}
                  slot={detail.fields[field]}
                  lifecycle={lc}
                  ticketStage={detail.stage}
                  ceiling={detail.ceiling}
                  stageState={fieldStageVisualStateFor(lc, detail, field)}
                  recap={detail.recap}
                  showRecap
                  onAccept={(payload) => accept(decision, payload)}
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
                    placeholder="Or tell the employee what to change..."
                    bind:value={revisionDraft}
                    disabled={revisionBusy}
                    onkeydown={(event) => {
                      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                        event.preventDefault();
                        void returnForRevision(decision);
                      }
                    }}
                  ></textarea>
                  <Button
                    variant="quiet"
                    data-review-revision-send=""
                    disabled={revisionBusy || !revisionDraft.trim()}
                    onclick={() => void returnForRevision(decision)}
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
  {:else}
    <div class="review-empty-state" data-review-empty>
      <div class="review-empty-mark" aria-hidden="true"><span></span></div>
      <div class="review-empty-text">There is nothing to review right now.</div>
      <div class="review-empty-meta">{runningWorkersText(runningWorkerCount)}</div>
    </div>
    {/if}
    {/if}
  </ResourceState>
</section>

<style>
  .review-help-requests { display: grid; gap: var(--space-3); padding-top: var(--space-6); }
  .review-help-heading {
    color: var(--accent-bright);
    font-size: var(--type-sm);
    font-weight: 600;
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }
  .review-help-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    border: var(--border-hairline) solid var(--accent-surface);
    border-radius: var(--radius-md);
    padding: var(--space-4);
    background: var(--surface-overlay);
  }
  .review-help-card-copy { min-width: 0; }
  .review-help-card-label { color: var(--accent-bright); font-size: var(--type-xs); }
  .review-help-card-title { color: var(--text-strong); font-family: var(--font-serif); font-size: var(--type-lg); }
  .review-help-card a { color: var(--text-muted); font-size: var(--type-sm); white-space: nowrap; }
</style>
