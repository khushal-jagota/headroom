<script lang="ts">
  import { isBrokenTicket } from "../lib/ticketIntegrity";
  import SkeletonRegion from "./SkeletonRegion.svelte";
  /** One waiting proposal, as a card.
   *
   * This is the ask itself — the Ticket's title, its recap,
   * the approval control, and the line for the Worker — and nothing about a queue. It is
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
  import ComposerField from "./conversation/ComposerField.svelte";
  import type { ReviewNoteDelivery } from "../lib/review/noteDelivery";
  import type { DeliveredMessage } from "../lib/conversation/wire";
  import {
    createVoiceCapture,
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
  let decisionKey = $state<string | null>(null);

  let resolvedAnnounced = false;

  // --- speaking a revision -----------------------------------------------------------------
  // Same machine as the composer's, worn lighter: Approve is this screen's primary action,
  // so the mic is only an affordance on the revision box.
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

  function requireVisibleProposal(): void {
    if (!currentIdentity() || !healthyDetail || isStale(healthyDetail)) throw new Error("the visible proposal changed; read it again before accepting");
  }

  async function write(path: string, options: Parameters<typeof mutateJson>[1]): Promise<unknown> {
    requireVisibleProposal();
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
    requireVisibleProposal();
    const proposalKey = currentKey;
    const result = await write(`/api/tickets/${ticketId}/accept/${acceptField}`, {
      method: "POST", body: payload
    });
    if (currentKey === proposalKey) announceResolved();
    return result;
  }

  function sendNote(delivery: ReviewNoteDelivery): Promise<DeliveredMessage> {
    requireVisibleProposal();
    return mutateJson<DeliveredMessage>(`/api/tickets/${ticketId}/conversation/send`, {
      method: "POST",
      body: {
        conversation_id: delivery.conversationId,
        content: delivery.message.content,
        sender_label: delivery.message.senderLabel,
        mode: delivery.message.mode,
        sender_message_id: delivery.message.messageId,
        sent_at_unix_milliseconds: delivery.message.sentAtUnixMilliseconds
      }
    });
  }

  function saveTitle(raw: string): Promise<unknown> {
    return write(`/api/tickets/${ticketId}`, {
      method: "PATCH",
      body: { title: raw }
    });
  }

  async function returnProposal(message: string, expectedProposalRevision: number): Promise<void> {
    const proposalKey = currentKey;
    await write(`/api/tickets/${ticketId}/reject`, { method: "POST", body: { message, expected_proposal_revision: expectedProposalRevision } });
    if (currentKey !== proposalKey) return;
    revisionDraft = "";
    announceResolved();
  }

</script>

<!-- The queue's own actions about this item: Skip (when the queue gives it) and Open ticket. -->
{#snippet queueActions()}
  {#if onSkip}
    <Button variant="ghost" data-skip="" onclick={onSkip}>Skip</Button>
  {/if}
  <Button variant="ghost" data-open-ticket href={ticketHref}>Open ticket</Button>
{/snippet}

<SkeletonRegion shape="block" loading={!detail.error && ((detail.isFetching && !healthyDetail) || Boolean(healthyDetail && !manifestMissingWorkerType && isStale(healthyDetail)))} loadingText="Loading approval...">
{#if detail.error}
  <div>
    <ErrorLine error={detail.error} />
    {#if title !== null}<div class="quiet-line">{title}</div>{/if}
    {#if embedded}{:else if decisionActions}{@render decisionActions()}{:else}
    <div class="review-card-actions">{@render queueActions()}</div>
    {/if}
  </div>
{:else if healthyDetail && manifestMissingWorkerType}
  {@const ticketDetail = healthyDetail}
  <div data-review-manifest-error>
    <ErrorLine
      error={{ code: "invalid_workflow", message: "The Ticket workflow is unavailable." }}
    />
    {#if embedded}{:else if decisionActions}{@render decisionActions()}{:else}
    <div class="review-card-actions">{@render queueActions()}</div>
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
      <div class="review-ticket-decision-line review-arrive review-arrive--1">{@render queueActions()}</div>

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
            <WorkerProposalCard {ticketId} conversationId={ticketDetail.conversation_id} onSendNote={sendNote} proposal={ticketDetail.pending_proposal} lifecycle={lc} stage={ticketDetail.stage}
              ceiling={ticketDetail.ceiling} holder={ticketDetail.ceiling_holder} manager={reviewManager}
              {initialEditedBody} {onEditedBodyChange} canReturn={Boolean(ticketDetail.conversation_id)}
              onAccept={accept}
              onScope={async (body) => { await write(`/api/tickets/${ticketId}`, { method:"PATCH", body }); }}
              onReturn={returnProposal}
              {revisionEditor} bind:revisionText={revisionDraft} holdDecisions={voiceState.phase !== "idle"} />
            {/key}
          {:else if decisionActions}{@render decisionActions()}
          {/if}
        {:else if decisionActions}{@render decisionActions()}
        {/if}
      </div>

      {#snippet revisionEditor()}
        <!-- One line for the Worker, always on the card: Send it back (or Cmd/Ctrl+Enter here,
             as on staging) sends it as the return's guidance, and Approve delivers it to the
             Worker and then approves. It is the composer's field (ComposerField); speaking
             with words already in the line appends to them. -->
        <div class="review-revise review-arrive review-arrive--4" data-review-revision>
          <ComposerField
            bind:value={revisionDraft}
            placeholder="Add a note for the worker…"
            inputAttributes={{ "data-review-revision-input": "" }}
            onkeydown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !event.repeat) {
                event.preventDefault();
                event.currentTarget.closest("[data-approval-block]")
                  ?.querySelector<HTMLButtonElement>("[data-send-back]")?.click();
              }
            }}
            record={voiceAvailable
              ? {
                  label: "Speak a revision",
                  onclick: () => void voice?.startRecording(),
                  attributes: { "data-voice-record": "", title: "Speak a revision" }
                }
              : undefined}
            voice={{
              state: voiceState,
              cancel: () => voice?.cancel(),
              stop: () => voice?.stopRecording(),
              retry: () => voice?.retry()
            }}
          />
        </div>
      {/snippet}
    </div>

    {@render footer?.()}
  {/if}
{/if}

</SkeletonRegion>
<!-- While the approval loads or is being refreshed, the queue's own actions (Skip, Open ticket)
     stay at hand at the right: they never wait on the proposal. -->
{#if !detail.error && (!healthyDetail || (!manifestMissingWorkerType && isStale(healthyDetail)))}
  <div class="review-card-actions">
    {#if embedded}{:else if decisionActions}{@render decisionActions()}{:else}{@render queueActions()}{/if}
  </div>
{/if}

<style>
  /* An error state's actions about the queue sit at the right, like every button row. */
  .review-card-actions { display:flex; align-items:center; justify-content:flex-end; gap:var(--space-2); margin-top:var(--space-3); }
</style>
