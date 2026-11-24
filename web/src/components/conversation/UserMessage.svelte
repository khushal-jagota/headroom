<script lang="ts">
  import MessagePieces from './MessagePieces.svelte';
  import MessageActions from './MessageActions.svelte';
  import TranscriptStatusRow from './TranscriptStatusRow.svelte';
  import SystemLine from './SystemLine.svelte';
  import BackendWords from './BackendWords.svelte';
  import DetailsChevron from './DetailsChevron.svelte';
  import QueuedMessageActions from './composer/QueuedMessageActions.svelte';
  import { displayedOutgoingContent, editableHeldWords, TRANSCRIPT_SEND_ACTIONS, type TranscriptSendActions } from '../../lib/conversation/transcriptOutgoing';
  import { promptLabelFor, promptOutcomeStatus, messageNotDelivered, type TranscriptRow, type TranscriptStatus } from '../../lib/conversation/transcript';
  import { openComposerModelPicker } from '../../lib/conversation/modelPicker';
  import { outgoingMessageNote, type OutgoingMessage } from '../../lib/conversation/outgoing';
  import { heldPromptRowActions, heldPromptRows } from '../../lib/conversation/heldPrompts';
  import { conversationHeight } from '../../lib/conversation/surfaceMotion';
  import { refusalOffersResend } from '../../lib/conversation/transcript';
  import { recordedContentCanBeResent, RESEND_RECORDED_MESSAGE, type ResendRecordedMessage } from '../../lib/conversation/resendRefused';
  import { getContext } from 'svelte';
  let { presentationRowKey = null, row = null, local = null, conversationId, ownSenderLabel = null, onStopDrawingMessage, onSendAgain }: {
    presentationRowKey?: string | null;
    row?: Extract<TranscriptRow, {kind: 'prompt'|'prompt_refused'|'prompt_uncertain'|'prompt_discarded'}> | null;
    local?: OutgoingMessage | null;
    conversationId: string;
    ownSenderLabel?: string | null;
    onStopDrawingMessage?: (id: string) => Promise<void> | void;
    onSendAgain?: (id: string) => Promise<void> | void;
  } = $props();
  let busy = $state(false);
  const label = $derived(promptLabelFor(row?.senderLabel ?? local?.senderLabel ?? '', ownSenderLabel));
  const content = $derived(row?.content ?? (local ? displayedOutgoingContent(local) : []));
  const actions = getContext<TranscriptSendActions | undefined>(TRANSCRIPT_SEND_ACTIONS);
  const held = $derived(!row && local?.presentation?.kind === "held" ? local.presentation : null);
  const upload = $derived(!row && local?.presentation?.kind === "upload" ? local.presentation : null);
  const queuedWords = $derived(held ? editableHeldWords(held.row) : null);
  const pendingNames = $derived(!row && !held && !upload ? local?.attachmentNames ?? [] : []);
  /** v6: a system note's words as one plain line of text (Markdown marks dropped). */
  const systemText = $derived(content.flatMap(piece => piece.piece === 'text' ? [piece.text] : []).join(' ')
    .replace(/`([^`]*)`/g, '$1').replace(/(\*\*|__)(.+?)\1/g, '$2').replace(/\s+/g, ' ').trim());
  // A message turned away for a reason sending it again can get past offers Resend, on the
  // message itself and at rest, not on hover: it is the next step its status line names.
  // Once it has gone out again the button goes, so one press is one more message.
  const resendRecorded = getContext<ResendRecordedMessage | undefined>(RESEND_RECORDED_MESSAGE);
  let resent = $state(false);
  let resendFailed = $state(false);
  let edited = $state(false);
  let editFailed = $state(false);
  const offersResend = $derived(row?.kind === 'prompt_refused' && resendRecorded !== undefined && resendRecorded.available() && refusalOffersResend(row.reason) && recordedContentCanBeResent(row.content));
  const localFailed = $derived(!row && (local?.knownFate === 'not_sent' || local?.knownFate === 'machine_offline'));
  const localUpload = $derived(local?.content.some(piece => piece.piece === 'video_upload' || piece.piece === 'attachment_upload') ?? false);
  const offersEdit = $derived(!edited && !resent && resendRecorded?.available() && (
    row?.kind === 'prompt_refused' && resendRecorded.edit && row.content.length > 0
    || localFailed && resendRecorded.editLocal
  ));
  // What became of the message, said under it in the thread's status voice, never inside
  // the words the person wrote.
  const status = $derived.by((): TranscriptStatus | null => {
    if (row?.kind === 'prompt_refused' || row?.kind === 'prompt_uncertain' || row?.kind === 'prompt_discarded') return promptOutcomeStatus(row, offersResend);
    if (held) {
      if (held.row.state === "unknown") return {tone: "warning", title: "The agent did not confirm receipt.", detail: null};
      const place = held.count > 1 ? ` · ${held.position} of ${held.count}` : "";
      const reason = held.row.queueReason === "attachment" ? "Attachments cannot join the running turn."
        : held.row.queueReason === "run_change" ? "The run settings will change when it starts."
        : held.row.queueReason === "steer_refused" ? "The running turn could not take the message."
        : held.row.queueReason === "command_needs_its_own_turn" ? "The command needs its own turn." : null;
      return {tone: "neutral", title: `Queued${place}`, detail: reason};
    }
    if (upload) return {tone: upload.failed ? "error" : "neutral", title: upload.note, detail: null};
    if (local && (local.accepted || row?.kind === "prompt") && label === null) {
      return {tone: "neutral", title: "Sent", detail: null};
    }
    if (row || !local) return null;
    // The machine was away: not delivered, said inside the bubble (B5), the server's words behind Details.
    if (local.knownFate === 'machine_offline') return { tone: 'error', title: local.refusal?.reason ?? 'The machine is offline', detail: local.refusal?.detail || null };
    if (local.knownFate === 'not_sent') return { tone: 'error', title: local.failureNote ?? 'The message did not reach Panels.', detail: null };
    const note = outgoingMessageNote(local);
    if (note === null) return null;
    return { tone: (local.knownFate === 'answer_never_came_back' || local.knownFate === 'agent_unconfirmed') ? 'warning' : 'neutral', title: note.charAt(0).toUpperCase() + note.slice(1), detail: null };
  });
  // A message the running turn took in as it went. It reads as the person's message, and
  // a quiet line under it says it joined the turn rather than starting one.
  const steered = $derived(row?.kind === 'prompt' && row.mode === 'steer');
  const attachesFiles = $derived(content.some(piece => piece.piece === 'file' && !/^(image|video)\//.test(piece.media_type)));
  // A message the offline machine turned away can go again once the machine is back, or be let go.
  const machineOffline = $derived(!row && local?.knownFate === 'machine_offline');
  const heldActions = $derived(local && !row ? heldPromptRowActions(heldPromptRows([], [local])[0]!) : []);
  const canRetryUnanswered = $derived(heldActions.includes('send_again') && !!local?.sendTarget);
  const hasQueuedActions = $derived(held?.row.state === "held" && !!held.row.heldPromptId && !!actions);
  const hasHeldLineActions = $derived(hasQueuedActions || canRetryUnanswered || heldActions.includes('stop_drawing'));
  async function recoverUpload(editing: boolean) {
    if (!local || !actions || busy) return;
    busy = true;
    try {
      if (editing) await actions.editUpload?.(local.messageId);
      else await actions.retryUpload?.(local.messageId);
    } finally {
      busy = false;
    }
  }
  async function stop(): Promise<void> {
    if (!local || busy) return;
    busy = true;
    try { await onStopDrawingMessage?.(local.messageId); }
    finally { busy = false; }
  }
  async function resendRefused(): Promise<void> {
    if (!offersResend || !row || busy || resent) return;
    busy = true;
    resendFailed = false;
    try { resent = await resendRecorded!.resend(conversationId, row.content, row.sequence); resendFailed = !resent; }
    catch { resendFailed = true; }
    finally { busy = false; }
  }
  async function edit(): Promise<void> {
    if (!offersEdit || busy) return;
    busy = true;
    editFailed = false;
    try {
      if (row) edited = await resendRecorded!.edit!(conversationId, row.content);
      else if (local) edited = await resendRecorded!.editLocal!(local.messageId);
    } catch {
      editFailed = true;
    } finally {
      busy = false;
    }
  }
  async function resend(): Promise<void> { if (!local || busy) return; busy = true; try { await onSendAgain?.(local.messageId); } finally { busy = false; } }
  // B5: the person's message that was not delivered says so inside its own bubble.
  const undelivered = $derived(label === null && status?.tone === 'error' ? status : null);
  let detailsOpen = $state(false);
  let root = $state<HTMLElement | null>(null);
  const uid = $props.id();
  const detailsId = `c2-undelivered-details-${uid}`;
</script>
<!-- Passed only when there is an action: an empty actions slot keeps its gap, and the
     caption would end short of the bubble's edge. -->
{#snippet heldLineActions()}
  {#if hasQueuedActions && held?.row.heldPromptId && actions}
    {@const queuedRow = held.row}
    <QueuedMessageActions heldPromptId={held.row.heldPromptId} showSteer={actions.canSteer()} running={actions.running()}
      onDiscard={() => actions.discard(queuedRow)} onPromote={mode => actions.promote(queuedRow, mode)}
      onEdit={queuedWords !== null && actions.canEditHeld() ? () => actions.editHeld(queuedRow, queuedWords!) : undefined}/>
  {/if}
  {#if canRetryUnanswered}<button type="button" data-conversation-held-send-again={local?.messageId} disabled={busy} onclick={()=>void resend()}>Try Again</button>{/if}
  {#if heldActions.includes('stop_drawing')}<button type="button" data-conversation-held-stop-drawing={local?.messageId} title="Stop showing this message" disabled={busy} onclick={()=>void stop()}>Dismiss</button>{/if}
{/snippet}
{#snippet undeliveredFooter()}
  {#if undelivered}
    <div class="c2-undelivered" role="alert" data-conversation-message-status="error">
      <p class="c2-undelivered-reason" data-conversation-status-next>{messageNotDelivered(undelivered.brief ?? undelivered.title)}</p>
      {#if (offersResend && !resent) || localFailed || offersEdit || undelivered.action === 'choose-model' || undelivered.detail}
        <div class="c2-undelivered-actions">
          {#if offersResend && !resent && !edited}<button type="button" class="c2-undelivered-resend" data-conversation-resend-refused={row?.key} title={resendFailed ? "It did not go out. Try again." : "Send this message again"} disabled={busy} onclick={()=>void resendRefused()}>{busy ? "Sending…" : "Try Again"}</button>{/if}
          {#if upload?.failed && actions}
            <button type="button" data-conversation-upload-retry disabled={busy} onclick={() => void recoverUpload(false)}>Try Again</button>
            <button type="button" data-conversation-upload-edit disabled={busy} onclick={() => void recoverUpload(true)}>Edit</button>
          {/if}
          {#if !upload && localFailed && !edited && !localUpload && onSendAgain}<button type="button" class="c2-undelivered-resend" data-conversation-held-send-again={local?.messageId} title="Send this message again" disabled={busy} onclick={()=>void resend()}>{busy ? "Sending…" : "Try Again"}</button>{/if}
          {#if !upload && offersEdit}<button type="button" data-conversation-edit-unsent disabled={busy} onclick={() => void edit()}>Edit</button>{/if}
          {#if undelivered.action === 'choose-model'}<button type="button" data-conversation-status-choose-model onclick={() => openComposerModelPicker(root)}>Choose another model</button>{/if}
          {#if undelivered.detail}<button type="button" aria-expanded={detailsOpen} aria-controls={detailsId} data-conversation-status-details onclick={() => (detailsOpen = !detailsOpen)}>Details<DetailsChevron open={detailsOpen}/></button>{/if}
          {#if localFailed && (machineOffline || edited)}<button type="button" data-conversation-held-stop-drawing={local?.messageId} title="Stop showing this message" disabled={busy} onclick={()=>void stop()}>Dismiss</button>{/if}
        </div>
      {/if}
      {#if editFailed}<p role="status">An attachment could not be restored.</p>{/if}
      {#if undelivered.detail}<BackendWords id={detailsId} detail={undelivered.detail} open={detailsOpen}/>{/if}
    </div>
  {/if}
{/snippet}
{#snippet bubbleFooter()}
  {#each upload?.files ?? [] as file}
    <div class="c2-outgoing-attachment">
      <span>{file.name}</span>
      {#if file.progress !== null}
        <progress max="100" value={file.progress} aria-label={`Uploading ${file.name}`}></progress>
        <span>{file.progress}%</span>
      {:else}
        <span>{file.note}</span>
      {/if}
    </div>
  {/each}
  {#each pendingNames as name}<div class="c2-outgoing-attachment">{name}</div>{/each}
  {@render undeliveredFooter()}
{/snippet}
<article bind:this={root} class={label === null ? 'chat-u' : 'chat-system'} class:attaches-files={attachesFiles} data-conversation-accepted={row?.kind === "prompt" || local?.accepted ? "true" : undefined} data-conversation-row={row?.kind} data-conversation-outgoing={local?.messageId}
  data-conversation-held-row={held?.row.key} data-local-video-send={upload ? local?.messageId : undefined}>
  {#if label !== null}
    <!-- Someone other than the person wrote this (Panels, another agent): one system line,
         "From Panels · " and the words, at most two lines, on the thread's left edge (B3). -->
    <SystemLine data-conversation-system-note="" title={systemText}>
      <span class="c2-system-words"><span data-conversation-prompt-label>From {label}</span><span aria-hidden="true">{" · "}</span>{systemText}</span>
    </SystemLine>
  {:else}
    <MessagePieces {presentationRowKey} {content} {conversationId} bubble failed={undelivered !== null} footer={undelivered || upload?.files.length || pendingNames.length ? bubbleFooter : undefined}/>
  {/if}
  {#if steered}
    <SystemLine align="end" data-conversation-steered="">Steered<span aria-hidden="true">{" · "}</span><span data-conversation-status-next>into the running turn</span></SystemLine>
  {/if}
  <div class="message-status-window" use:conversationHeight><div class="message-status">
    {#if status && undelivered === null}
      <TranscriptStatusRow {status} align={label === null ? 'end' : 'start'} role={status.tone === 'error' ? 'alert' : 'status'}
        data-conversation-message-status={status.tone} data-conversation-outgoing-label={local && !row ? '' : undefined}
        actions={hasHeldLineActions ? heldLineActions : undefined}/>
    {/if}
  </div></div>
  <MessageActions {content} createdAt={row?.createdAt ?? null}/>
</article>
<style>
  article { position: relative; }
  .c2-outgoing-attachment { display: flex; flex-wrap: wrap; gap: var(--cv-s2); align-items: center; overflow-wrap: anywhere; }
  .c2-outgoing-attachment progress { max-inline-size: 8rem; }
  .c2-outgoing-attachment + .c2-outgoing-attachment { margin-block-start: var(--cv-s1); }
  /* The message is a column of the things that make it up: the bubble (with what was
     attached inside it) and the captions under it, on one right edge. */
  .chat-u, .chat-system {
    display: flex;
    flex-direction: column;
    gap: var(--cv-gap-bubble-stack);
    background: transparent;
    padding: 0;
    border-radius: 0;
    color: var(--cv-t1);
    font-family: var(--cv-font);
  }
  .chat-u { align-self: flex-end; align-items: flex-end; max-width: 80%; }
  .chat-system { align-self: stretch; align-items: flex-start; }
  .attaches-files { min-inline-size: min(100%, 320px); }
  .chat-u :global(.c2-piece-image-slot), .chat-u :global(.conversation-file-card) { margin-block: 0; }
  /* One group with one right edge, the bubble's. A picture's slot can be wider than the
     picture, so the picture is set against the slot's end. */
  .chat-u :global(.c2-piece-image-slot) { display: flex; justify-content: flex-end; max-width: 100%; }
  .chat-u :global(.conversation-file-card--media) { align-self: flex-end; }

  .c2-system-words {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
  }

  /* Keep the failure sentence and its actions beneath the message. */
  .c2-undelivered {
    margin-block-start: 10px;
    padding-block-start: 10px;
    border-block-start: 1px solid rgb(255 143 133 / 30%);
    font-family: var(--cv-font);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
    text-align: start;
  }
  .c2-undelivered-reason { margin: 0; color: var(--cv-failure-ink); }
  /* The backend's words open inside the bubble's current width and never widen it: they
     add nothing to the bubble's own width, and a long word breaks. */
  .c2-undelivered :global(.c2-backend-words) { contain: inline-size; }
  .c2-undelivered :global(.c2-backend-words-text) { overflow-wrap: anywhere; }
  .c2-undelivered-actions { display: flex; flex-wrap: wrap; gap: var(--cv-s4); margin-block-start: var(--cv-s3); }
  .c2-undelivered-actions button {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    /* A 24 px target on a 20 px line: the extra 2 px above and below take no space. */
    padding: 2px 0;
    margin-block: -2px;
    border: 0;
    background: none;
    color: var(--cv-system-action-ink);
    font: inherit;
    cursor: pointer;
  }
  .c2-undelivered-actions button:hover:not(:disabled) { color: var(--cv-hover-ink); }
  .c2-undelivered-actions button:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 2px; border-radius: var(--cv-r-sm); }
  .c2-undelivered-actions button:disabled { color: var(--cv-system-ink); cursor: default; }
  .c2-undelivered-actions .c2-undelivered-resend { color: #ffffff; font-weight: var(--cv-weight-label); }

  .message-status-window { align-self: stretch; margin-block-start: calc(var(--cv-gap-bubble-stack) * -1); }
  .message-status { display: flex; flex-direction: column; }
  .message-status > :global(.c2-status) { margin-block-start: var(--cv-gap-bubble-stack); }
</style>
