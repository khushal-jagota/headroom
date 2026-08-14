<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { workspaceAddress } from "../lib/workspaceAddress";
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
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";
  import TicketPriorityControl from "../components/TicketPriorityControl.svelte";
  import {
    createVoiceCapture,
    formatVoiceTime,
    voiceCaptureSupported,
    type VoiceCapture,
    type VoiceCaptureState
  } from "../lib/conversation/voiceCapture";

  const review = createQuery(() => queries.review());
  const manifest = createQuery(() => queries.workerTypeManifests());

  let skipped = $state<Record<string, boolean>>({});
  let revisionDraft = $state("");
  let revisionError = $state<unknown>(null);
  let revisionBusy = $state(false);
  let revisionDecisionKey = $state<string | null>(null);
  let priorityError = $state<unknown>(null);
  let priorityBusy = $state(false);
  const staleRefreshRequests = new Set<string>();

  // --- speaking a revision -----------------------------------------------------------------
  // Same machine as the composer's, worn lighter: no voice-first face — Approve is this
  // screen's primary action, so the mic is only an affordance on the revision box.
  let coarsePointer = $state(false);
  let voiceSupported = $state(false);
  let voiceState = $state<VoiceCaptureState>({ phase: "idle" });
  let voice: VoiceCapture | null = null;

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
  let voiceAvailable = $derived(
    coarsePointer && voiceSupported && Boolean(detail.data?.conversation_id)
  );

  /** The spoken words join the revision draft the way typing them would have. An empty
   *  transcript is a no-op. */
  function landRevisionTranscript(transcript: string): void {
    if (transcript === "") return;
    const settled = revisionDraft.trim();
    revisionDraft = settled === "" ? transcript : `${settled}\n\n${transcript}`;
  }

  onMount(() => {
    coarsePointer = window.matchMedia("(pointer: coarse)").matches;
    voiceSupported = voiceCaptureSupported();
    voice = createVoiceCapture({
      conversationId: () => detail.data?.conversation_id ?? null,
      onState: (state) => (voiceState = state),
      onTranscript: landRevisionTranscript
    });
    return () => {
      voice?.dispose();
      voice = null;
    };
  });

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
      priorityError = null;
      priorityBusy = false;
      // The box — and anything being spoken into it — belongs to one proposal.
      voice?.cancel();
    }
  });

  function skip(item: ReviewItem): void {
    skipped = { ...skipped, [itemKey(item)]: true };
  }

  function openTicket(item: ReviewItem): void {
    window.location.hash = workspaceAddress({ kind: "ticket", id: item.ticket_id });
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

  async function savePriority(
    item: ReviewProposalItem,
    priority: string,
    select: HTMLSelectElement,
    previousPriority: string
  ): Promise<void> {
    priorityError = null;
    priorityBusy = true;
    try {
      await mutateJson(`/api/tickets/${item.ticket_id}`, {
        method: "PATCH",
        body: { priority }
      });
    } catch (err) {
      priorityError = err;
      select.value = previousPriority;
    } finally {
      priorityBusy = false;
    }
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
              <a data-open-ticket href={workspaceAddress({ kind: "ticket", id: currentItem.ticket_id })}>Open ticket &rsaquo;</a>
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
              <a data-open-ticket href={workspaceAddress({ kind: "ticket", id: proposal.ticket_id })}>open ticket</a>
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
              <a data-open-ticket href={workspaceAddress({ kind: "ticket", id: proposal.ticket_id })}>open ticket</a>
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
                  <a data-open-ticket href={workspaceAddress({ kind: "ticket", id: proposal.ticket_id })}>Open ticket &rsaquo;</a>
                </div>

                <div class="review-ticket-heading review-arrive review-arrive--2">
                  <div class="review-ticket-heading-main">
                    <div class="review-ticket-title">
                      <InlineEdit
                        value={ticketDetail.title}
                        placeholder="Untitled"
                        onSave={(raw) => saveTitle(proposal, raw)}
                      />
                    </div>
                    {#if ticketDetail.recap}
                      <div class="review-recap" data-recap>
                        <MarkdownBlock text={ticketDetail.recap} />
                      </div>
                    {/if}
                  </div>
                  {#if field === "kickoff"}
                    <div class="review-kickoff-priority">
                      <TicketPriorityControl
                        priority={ticketDetail.priority}
                        disabled={priorityBusy}
                        surface="review"
                        onChange={(priority, select) => {
                          if (priority !== ticketDetail.priority) {
                            void savePriority(
                              proposal,
                              priority,
                              select,
                              ticketDetail.priority
                            );
                          }
                        }}
                      />
                      {#if priorityError}
                        <div data-review-priority-error>
                          <ErrorLine error={priorityError} />
                        </div>
                      {/if}
                    </div>
                  {/if}
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
                      suggestedNextCeiling={ticketDetail.suggested_next_ceiling}
                      stageState={fieldStageVisualStateFor(lc, ticketDetail, field)}
                      approvalDisabled={field === "kickoff" && priorityBusy}
                      onAccept={(payload) => accept(proposal, payload)}
                    />
                  {/if}
                </div>

                {#if field !== "kickoff"}
                  <div class="review-revise review-arrive review-arrive--4" data-review-revision>
                    <div class="review-revision-box" data-review-voice={voiceState.phase}>
                      {#if voiceState.phase !== "idle"}
                        <!-- The same voice language as the composer: the centred shimmered
                             word where the words would go, actions where "Send back" sits. -->
                        <div class="chat-voice-mid review-voice-mid">
                          {#if voiceState.phase === "recording"}
                            <span class="live-text-shimmer">recording</span>
                            <span class="chat-voice-time">{formatVoiceTime(voiceState.elapsedMs)}</span>
                          {:else if voiceState.phase === "transcribing"}
                            <span class="live-text-shimmer">transcribing</span>
                          {:else}
                            <span class="chat-voice-fail">
                              transcription failed · kept
                              <span class="chat-voice-time">{formatVoiceTime(voiceState.keptMs)}</span>
                            </span>
                          {/if}
                        </div>
                        <button
                          type="button"
                          class="chat-voice-cancel review-voice-action"
                          data-voice-cancel
                          aria-label={voiceState.phase === "recording"
                            ? "Cancel recording"
                            : voiceState.phase === "transcribing"
                              ? "Cancel transcription"
                              : "Discard recording"}
                          onclick={() => voice?.cancel()}
                        >
                          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5l14 14M19 5L5 19" /></svg>
                        </button>
                        {#if voiceState.phase === "recording"}
                          <button
                            type="button"
                            class="chat-voice-stop review-voice-action"
                            data-voice-stop
                            aria-label="Stop recording and transcribe"
                            onclick={() => voice?.stopRecording()}
                          ><span class="chat-voice-square"></span></button>
                        {:else if voiceState.phase === "failed"}
                          <button
                            type="button"
                            class="chat-voice-retry review-voice-action"
                            data-voice-retry
                            onclick={() => voice?.retry()}
                          >retry</button>
                        {/if}
                      {:else}
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
                        {#if voiceAvailable}
                          <!-- Speaking with words already in the box appends to them. -->
                          <button
                            type="button"
                            class="review-voice-mic"
                            data-voice-record
                            aria-label="Speak a revision"
                            title="Speak a revision"
                            disabled={revisionBusy}
                            onclick={() => void voice?.startRecording()}
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <rect x="9" y="3" width="6" height="11" rx="3" />
                              <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                            </svg>
                          </button>
                        {/if}
                        <Button
                          variant="quiet"
                          data-review-revision-send=""
                          disabled={revisionBusy || !revisionDraft.trim()}
                          onclick={() => void returnForRevision(proposal)}
                        >
                          Send back
                        </Button>
                      {/if}
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
