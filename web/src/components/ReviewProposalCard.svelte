<script lang="ts">
  import { isBrokenTicket } from "../lib/ticketIntegrity";
  import SkeletonRegion from "./SkeletonRegion.svelte";
  /** One waiting proposal, as a card.
   *
   * This is the ask itself — the Ticket's title, its recap,
   * the approval control, and the send-back box — and nothing about a queue. It is
   * named by the shared queue's exact proposal identity, and it fetches its detail and the
   * Worker-type manifest itself, so a caller only has to know which decision it
   * wants shown. Everything about *which* decision is current — walking, skipping,
   * keyboard shortcuts — belongs to the caller.
   *
   * The Review screen (`ReviewRoute`) mounts the card.
   */
  import { onMount, onDestroy, untrack, type Snippet } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { queries } from "../lib/queryCatalogue";
  import { buildLifecycle } from "../lib/lifecycle";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { reviewProposalIdentity, reviewProposalDetailMatches } from "../lib/navigation/presentation";

  import type { NavigationReviewProposalItem, TicketDetail } from "../lib/types";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import WorkerProposalCard from "./WorkerProposalCard.svelte";
  import {
    createVoiceCapture,
    formatVoiceTime,
    voiceCaptureAvailable,
    voiceCaptureSupported,
    type VoiceCapture,
    type VoiceCaptureState
  } from "../lib/conversation/voiceCapture";

  let {
    identity,
    ticketId,
    field,
    title = null,
    onSkip,
    onResolved,
    footer,
    decisionActions,
    embedded = false,
    initialRevisionDraft = "",
    onRevisionDraftChange,
    initialEditedBody,
    onEditedBodyChange
  }: {
    identity: NavigationReviewProposalItem;
    embedded?: boolean;
    initialRevisionDraft?: string;
    initialEditedBody?: string;
    onEditedBodyChange?: (text: string) => void;
    onRevisionDraftChange?: (text: string) => void;
    /** The Ticket whose proposal this is. */
    ticketId: string;
    /** The Ticket field the proposal is filed against. */
    field: string;
    /** The queue's own title for the Ticket, shown only when the detail cannot be
     *  fetched. A caller without one omits it. */
    title?: string | null;
    /** Given, the card carries its own Skip control. */
    onSkip?: () => void;
    /** The decision has left the queue: approved, sent back, or found already
     *  gone. Fires at most once per proposal. */
    onResolved?: () => void;
    /** Rendered under the card, only once the ask itself is on screen. */
    footer?: Snippet;
    /** The queue's own Skip / Open ticket, placed in the decision card's action row. */
    decisionActions?: Snippet;
  } = $props();

  let revisionDraft = $state("");
  let revisionError = $state<unknown>(null);
  let revisionBusy = $state(false);
  let decisionKey = $state<string | null>(null);

  let resolvedAnnounced = false;

  // --- speaking a revision -----------------------------------------------------------------
  // Same machine as the composer's, worn lighter: no voice-first face — Approve is this
  // screen's primary action, so the mic is only an affordance on the revision box.
  let voiceSupported = $state(false);
  let voiceState = $state<VoiceCaptureState>({ phase: "idle" });
  let voice: VoiceCapture | null = null;

  // The Ticket behind the proposal on screen. The key follows the Ticket, so
  // moving to the next ask switches the query rather than re-opening a handle.
  const navigation = (() => { const value = navigationApplication(); if (!value) throw new Error("Review proposal requires the application navigation owner"); return value; })();

  const detail = createQuery(() => queries.reviewProposal(identity));
  let healthyDetail = $derived(detail.data && !isBrokenTicket(detail.data) ? detail.data : undefined);

  let currentKey = $derived(reviewProposalIdentity(identity));
  let ticketHref = $derived(workspaceAddress({ kind: "ticket", id: ticketId }));

  let lc = $derived(healthyDetail?.workflow ? buildLifecycle(healthyDetail.workflow) : null);
  // Approving here sets the next ceiling, so the reviewer gets the same choice of holder
  // the Ticket page offers, including handing the Ticket back to its own Manager.
  let reviewManager = $derived(
    healthyDetail?.manager_id
      ? {
          id: healthyDetail.manager_id,
          title: healthyDetail.manager_title || "Manager"
        }
      : null
  );
  let voiceAvailable = $derived(voiceCaptureAvailable(voiceSupported, true));

  let manifestMissingWorkerType = $derived(Boolean(healthyDetail && !healthyDetail.workflow));

  /** The field to accept against, or null when this Ticket's Worker type has no
   *  such field — the decision is then not a Ticket field at all. */
  let acceptField = $derived(lc?.fieldIds.includes(field) ? field : null);

  /** The spoken words join the revision draft the way typing them would have. An empty
   *  transcript is a no-op. */
  function landRevisionTranscript(transcript: string): void {
    if (transcript === "") return;
    const settled = revisionDraft.trim();
    revisionDraft = settled === "" ? transcript : `${settled}\n\n${transcript}`;
  }

  onMount(() => {
    voiceSupported = voiceCaptureSupported();
    voice = createVoiceCapture({
      onState: (state) => (voiceState = state),
      onTranscript: landRevisionTranscript
    });
    return () => {
      voice?.dispose();
      voice = null;
    };
  });

  function isStale(ticketDetail: TicketDetail): boolean {
    return !reviewProposalDetailMatches(identity,ticketDetail) || ticketDetail.pending_proposal?.field !== field;
  }

  function currentIdentity(): boolean {
    const effective = navigation.state.effective;
    return Boolean(effective && navigation.presentationFor(effective).review.items.some(item => item.review_item_type === 'ticket' && item.proposal !== null && reviewProposalIdentity(item.proposal) === currentKey));
  }

  async function write(path: string, options: Parameters<typeof mutateJson>[1]): Promise<unknown> {
    if (!currentIdentity() || !healthyDetail || isStale(healthyDetail)) throw new Error("the visible proposal changed; read it again before accepting");
    return mutateJson(path,{...options});
  }

  /** One announcement per proposal, whichever way it left the queue. */
  function announceResolved(): void {
    if (resolvedAnnounced) return;
    resolvedAnnounced = true;
    onResolved?.();
  }

  $effect(() => {
    if (currentKey !== decisionKey) {
      revisionDraft = decisionKey === null ? initialRevisionDraft : "";
      decisionKey = currentKey;
      revisionError = null;
      revisionBusy = false;

      resolvedAnnounced = false;
      // The box — and anything being spoken into it — belongs to one proposal.
      voice?.cancel();
    }
  });

  $effect(() => {
    const text = revisionDraft;
    untrack(() => onRevisionDraftChange?.(text));
  });

  let reconciledMismatch: string | null = null;
  $effect(() => {
    const loaded = healthyDetail;
    if (!loaded || detail.isFetching || !isStale(loaded)) return;
    const mismatch = JSON.stringify([currentKey,loaded.pending_proposal?.field,loaded.pending_proposal?.created_at,loaded.pending_proposal?.revision]);
    if (reconciledMismatch === mismatch) return;
    reconciledMismatch = mismatch;
    // Detail can be behind or ahead. Only the shared queue can settle membership.
    void detail.refetch().catch(() => undefined);
    void navigation.owner.recover().catch(() => undefined);
  });

  async function accept(payload: Record<string, unknown>): Promise<unknown> {
    if (!acceptField) throw new Error("Review decision is not a Ticket field");
    const result = await write(`/api/tickets/${ticketId}/accept/${acceptField}`, {
      method: "POST",
      body: payload
    });
    announceResolved();
    return result;
  }

  function saveTitle(raw: string): Promise<unknown> {
    return write(`/api/tickets/${ticketId}`, {
      method: "PATCH",
      body: { title: raw }
    });
  }

  async function returnForRevision(): Promise<void> {
    const message = revisionDraft.trim();
    if (!message || revisionBusy) return;
    revisionError = null;
    revisionBusy = true;
    try {
      await write(`/api/tickets/${ticketId}/reject`, {
        method: "POST",
        body: { message }
      });
      revisionDraft = "";
      announceResolved();
    } catch (err) {
      revisionError = err;
    } finally {
      revisionBusy = false;
    }
  }
</script>

<SkeletonRegion shape="block" loading={!detail.error && ((detail.isFetching && !healthyDetail) || Boolean(healthyDetail && !manifestMissingWorkerType && isStale(healthyDetail)))} loadingText="Loading approval...">
{#if detail.error}
  <div>
    <ErrorLine error={detail.error} />
    {#if title !== null}<div class="quiet-line">{title}</div>{/if}
    {#if decisionActions}{@render decisionActions()}{:else}
    <div class="review-card-actions">
      {#if onSkip}
        <Button variant="ghost" data-skip="" onclick={onSkip}>Skip</Button>
      {/if}
      <Button variant="ghost" data-open-ticket href={ticketHref}>Open ticket</Button>
    </div>
    {/if}
  </div>
{:else if healthyDetail && manifestMissingWorkerType}
  {@const ticketDetail = healthyDetail}
  <div data-review-manifest-error>
    <ErrorLine
      error={{ code: "invalid_workflow", message: "The Ticket workflow is unavailable." }}
    />
    {#if decisionActions}{@render decisionActions()}{:else}
    <div class="review-card-actions">
      {#if onSkip}
        <Button variant="ghost" data-skip="" onclick={onSkip}>Skip</Button>
      {/if}
      <Button variant="ghost" data-open-ticket href={ticketHref}>Open ticket</Button>
    </div>
    {/if}
  </div>
{:else if healthyDetail}
  {@const ticketDetail = healthyDetail}
  {#if !isStale(ticketDetail)}
    <div
      data-proposal-identity={currentKey}
      class="modern-review-content"
      data-review-card
      data-review-item-type="proposal"
      data-ticket-id={ticketId}
      data-field={field}
    >
      {#if !embedded}
      <div class="review-ticket-decision-line review-arrive review-arrive--1">
        {#if onSkip}
          <Button variant="ghost" data-skip="" onclick={onSkip}>Skip</Button>
        {/if}
        <Button variant="ghost" data-open-ticket href={ticketHref}>Open ticket</Button>
      </div>

      <div class="review-ticket-heading review-arrive review-arrive--2">
        <div class="review-ticket-heading-main">
          <div class="review-ticket-title">
            <InlineEdit
              value={identity.title}
              placeholder="Untitled"
              onSave={(raw) => saveTitle(raw)}
            />
          </div>
          {#if ticketDetail.recap}
            <div class="review-recap" data-recap>
              <MarkdownBlock text={ticketDetail.recap} />
            </div>
          {/if}
        </div>
      </div>

      {/if}
      <div class="review-arrive review-arrive--3">
        {#if acceptField}
          {#if lc && ticketDetail.pending_proposal}
            {#key `${ticketDetail.pending_proposal.field}:${ticketDetail.pending_proposal.created_at}:${ticketDetail.pending_proposal.revision}:${lc.workerType}:${lc.stageOrder.join(",")}:${lc.ceilingRange.join(",")}`}
            <WorkerProposalCard proposal={ticketDetail.pending_proposal} lifecycle={lc} stage={ticketDetail.stage}
              ceiling={ticketDetail.ceiling} holder={ticketDetail.ceiling_holder} manager={reviewManager}
              disabled={revisionBusy} {initialEditedBody} {onEditedBodyChange} initialReturnOpen={Boolean(initialRevisionDraft)} canReturn={Boolean(ticketDetail.conversation_id)}
              onAccept={accept}
              onScope={async (body) => { await write(`/api/tickets/${ticketId}`, { method:"PATCH", body }); }}
              onReturn={async (message) => { await write(`/api/tickets/${ticketId}/reject`, { method:"POST", body:{ message } }); announceResolved(); }}
              {revisionEditor} utilityActions={decisionActions} />
            {/key}
          {:else if decisionActions}{@render decisionActions()}
          {/if}
        {:else if decisionActions}{@render decisionActions()}
        {/if}
      </div>

      {#snippet revisionEditor()}
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
                    void returnForRevision();
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
                variant="secondary"
                data-review-revision-send=""
                disabled={revisionBusy || !revisionDraft.trim()}
                onclick={() => void returnForRevision()}
              >
                Send back
              </Button>
            {/if}
          </div>
          {#if revisionError}
            <ErrorLine error={revisionError} />
          {/if}
        </div>
      {/snippet}
    </div>

    {@render footer?.()}
  {/if}
{/if}

</SkeletonRegion>
