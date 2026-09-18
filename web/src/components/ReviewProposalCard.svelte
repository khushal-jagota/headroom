<script lang="ts">
  /** One waiting proposal, as a card.
   *
   * This is the ask itself — the Ticket's title, its recap, the kickoff priority,
   * the approval control, and the send-back box — and nothing about a queue. It is
   * named by a Ticket id and a field, and it fetches that Ticket's detail and the
   * Worker-type manifest itself, so a caller only has to know which decision it
   * wants shown. Everything about *which* decision is current — walking, skipping,
   * keyboard shortcuts — belongs to the caller.
   *
   * The Review screen (`ReviewRoute`) mounts the card.
   */
  import { onMount, type Snippet } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { queries } from "../lib/queryCatalogue";
  import { fieldStageVisualStateFor, gatingFieldFor, lifecycleFor } from "../lib/lifecycle";
  import type { TicketDetail } from "../lib/types";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import Disclosure from "./Disclosure.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import TicketStageSection from "./TicketStageSection.svelte";
  import TicketPriorityControl from "./TicketPriorityControl.svelte";
  import {
    createVoiceCapture,
    formatVoiceTime,
    voiceCaptureAvailable,
    voiceCaptureSupported,
    type VoiceCapture,
    type VoiceCaptureState
  } from "../lib/conversation/voiceCapture";

  let {
    ticketId,
    field,
    title = null,
    onSkip,
    onResolved,
    footer
  }: {
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
  } = $props();

  let revisionDraft = $state("");
  let revisionError = $state<unknown>(null);
  let revisionBusy = $state(false);
  let decisionKey = $state<string | null>(null);
  let priorityError = $state<unknown>(null);
  let priorityBusy = $state(false);
  let resolvedAnnounced = false;

  // --- speaking a revision -----------------------------------------------------------------
  // Same machine as the composer's, worn lighter: no voice-first face — Approve is this
  // screen's primary action, so the mic is only an affordance on the revision box.
  let voiceSupported = $state(false);
  let voiceState = $state<VoiceCaptureState>({ phase: "idle" });
  let voice: VoiceCapture | null = null;

  const manifest = createQuery(() => queries.workerTypeManifests());

  // The Ticket behind the proposal on screen. The key follows the Ticket, so
  // moving to the next ask switches the query rather than re-opening a handle.
  const detail = createQuery(() => queries.ticket(ticketId));

  let currentKey = $derived(`${ticketId}:${field}`);
  let ticketHref = $derived(workspaceAddress({ kind: "ticket", id: ticketId }));

  // Per-Worker-type lifecycle for this decision's detail. Null while the manifest or
  // the detail is still loading OR when the detail's worker_type is absent from a
  // loaded manifest; the markup tells those apart (Codex F3).
  let detailWorkerType = $derived(
    typeof detail.data?.worker_type === "string" ? (detail.data.worker_type as string) : null
  );
  let lc = $derived(lifecycleFor(manifest.data, detailWorkerType));
  let voiceAvailable = $derived(voiceCaptureAvailable(voiceSupported, true));

  let manifestMissingWorkerType = $derived(
    Boolean(
      detailWorkerType &&
        manifest.data &&
        !manifest.data.worker_types.some((item) => item.worker_type === detailWorkerType)
    )
  );

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
    if (!acceptField) return true;
    if (ticketDetail.pending_proposal?.field !== acceptField) return true;
    return gatingFieldFor(lc, String(ticketDetail.stage)) !== acceptField;
  }

  /** One announcement per proposal, whichever way it left the queue. */
  function announceResolved(): void {
    if (resolvedAnnounced) return;
    resolvedAnnounced = true;
    onResolved?.();
  }

  $effect(() => {
    if (currentKey !== decisionKey) {
      decisionKey = currentKey;
      revisionDraft = "";
      revisionError = null;
      revisionBusy = false;
      priorityError = null;
      priorityBusy = false;
      resolvedAnnounced = false;
      // The box — and anything being spoken into it — belongs to one proposal.
      voice?.cancel();
    }
  });

  // A detail that no longer holds this proposal means the queue that named it is
  // behind the Ticket. Say so once; the caller decides what to do about it. Only a
  // loaded lifecycle can tell a resolved decision from an unloaded manifest, so
  // nothing is announced until there is one.
  $effect(() => {
    const ticketDetail = detail.data;
    if (!lc || !ticketDetail) return;
    if (isStale(ticketDetail)) announceResolved();
  });

  async function accept(payload: Record<string, unknown>): Promise<unknown> {
    if (!acceptField) throw new Error("Review decision is not a Ticket field");
    const result = await mutateJson(`/api/tickets/${ticketId}/accept/${acceptField}`, {
      method: "POST",
      body: payload
    });
    announceResolved();
    return result;
  }

  function saveTitle(raw: string): Promise<unknown> {
    return mutateJson(`/api/tickets/${ticketId}`, {
      method: "PATCH",
      body: { title: raw }
    });
  }

  function saveProposal(raw: string): Promise<unknown> {
    if (!acceptField) throw new Error("Review decision is not a Ticket field");
    return mutateJson(`/api/tickets/${ticketId}/proposal`, {
      method: "PUT",
      body: { field: acceptField, body: raw }
    });
  }

  async function savePriority(
    priority: string,
    select: HTMLSelectElement,
    previousPriority: string
  ): Promise<void> {
    priorityError = null;
    priorityBusy = true;
    try {
      await mutateJson(`/api/tickets/${ticketId}`, {
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

  async function returnForRevision(): Promise<void> {
    const message = revisionDraft.trim();
    if (!message || revisionBusy) return;
    revisionError = null;
    revisionBusy = true;
    try {
      await mutateJson(`/api/tickets/${ticketId}/return-for-revision`, {
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

{#if detail.error}
  <div>
    <ErrorLine error={detail.error} />
    {#if title !== null}<div class="quiet-line">{title}</div>{/if}
    <div class="review-card-actions">
      {#if onSkip}
        <Button variant="quiet" data-skip="" onclick={onSkip}>Skip</Button>
      {/if}
      <a data-open-ticket href={ticketHref}>open ticket</a>
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
      {#if onSkip}
        <Button variant="quiet" data-skip="" onclick={onSkip}>Skip</Button>
      {/if}
      <a data-open-ticket href={ticketHref}>open ticket</a>
    </div>
  </div>
{:else if detail.data}
  {@const ticketDetail = detail.data}
  {#if isStale(ticketDetail)}
    <div class="quiet-line">Loading approval...</div>
  {:else}
    <div
      class="modern-review-content"
      data-review-card
      data-review-item-type="proposal"
      data-ticket-id={ticketId}
      data-field={field}
    >
      <div class="review-ticket-decision-line review-arrive review-arrive--1">
        {#if onSkip}
          <button data-skip="" onclick={onSkip}>Skip &rsaquo;</button>
        {/if}
        <a data-open-ticket href={ticketHref}>Open ticket &rsaquo;</a>
      </div>

      <div class="review-ticket-heading review-arrive review-arrive--2">
        <div class="review-ticket-heading-main">
          <div class="review-ticket-title">
            <InlineEdit
              value={ticketDetail.title}
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
        {#if acceptField === "kickoff"}
          <div class="review-kickoff-priority">
            <TicketPriorityControl
              priority={ticketDetail.priority}
              disabled={priorityBusy}
              surface="review"
              onChange={(priority, select) => {
                if (priority !== ticketDetail.priority) {
                  void savePriority(priority, select, ticketDetail.priority);
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

      {#if ticketDetail.guidance}
        <Disclosure title="Guidance" variant="support" defaultOpen={false} data-ticket-guidance>
          <MarkdownBlock text={ticketDetail.guidance} />
        </Disclosure>
      {/if}
      <div class="review-arrive review-arrive--3">
        {#if acceptField}
          <TicketStageSection
            variant="review"
            name={acceptField}
            value={ticketDetail.field_values[acceptField] ?? ""}
            pendingProposal={ticketDetail.pending_proposal}
            lifecycle={lc}
            ticketStage={ticketDetail.stage}
            ceiling={ticketDetail.ceiling}
            stageState={fieldStageVisualStateFor(lc, ticketDetail, acceptField)}
            approvalDisabled={acceptField === "kickoff" && priorityBusy}
            onAccept={(payload) => accept(payload)}
            onSaveProposal={saveProposal}
          />
        {/if}
      </div>

      {#if acceptField !== "kickoff"}
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
                variant="quiet"
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
      {/if}
    </div>

    {@render footer?.()}
  {/if}
{/if}
