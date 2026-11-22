<script lang="ts">
  import MessagePieces from './MessagePieces.svelte';
  import MessageActions from './MessageActions.svelte';
  import TranscriptStatusRow from './TranscriptStatusRow.svelte';
  import SystemLine from './SystemLine.svelte';
  import BackendWords from './BackendWords.svelte';
  import { promptLabelFor, promptOutcomeStatus, statusReason, type TranscriptRow, type TranscriptStatus } from '../../lib/conversation/transcript';
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
  const content = $derived(row?.content ?? local?.content ?? []);
  /** v6: a system note's words as one plain line of text (Markdown marks dropped). */
  const systemText = $derived(content.flatMap(piece => piece.piece === 'text' ? [piece.text] : []).join(' ')
    .replace(/`([^`]*)`/g, '$1').replace(/(\*\*|__)(.+?)\1/g, '$2').replace(/\s+/g, ' ').trim());
  // A message turned away for a reason sending it again can get past offers Resend, on the
  // message itself and at rest, not on hover: it is the next step its status line names.
  // Once it has gone out again the button goes, so one press is one more message.
  const resendRecorded = getContext<ResendRecordedMessage | undefined>(RESEND_RECORDED_MESSAGE);
  let resent = $state(false);
  let resendFailed = $state(false);
  const offersResend = $derived(row?.kind === 'prompt_refused' && resendRecorded !== undefined && resendRecorded.available() && refusalOffersResend(row.reason) && recordedContentCanBeResent(row.content));
  // What became of the message, said under it in the thread's status voice, never inside
  // the words the person wrote.
  const status = $derived.by((): TranscriptStatus | null => {
    if (row?.kind === 'prompt_refused' || row?.kind === 'prompt_uncertain' || row?.kind === 'prompt_discarded') return promptOutcomeStatus(row, offersResend);
    if (row || !local) return null;
    const note = outgoingMessageNote(local);
    if (note === null) return null;
    return { tone: local.knownFate === 'answer_never_came_back' ? 'warning' : 'neutral', title: note.charAt(0).toUpperCase() + note.slice(1), detail: null };
  });
  // A message the running turn took in as it went. It reads as the person's message, and
  // a quiet line under it says it joined the turn rather than starting one.
  const steered = $derived(row?.kind === 'prompt' && row.mode === 'steer');
  const attachesFiles = $derived(content.some(piece => piece.piece === 'file' && !/^(image|video)\//.test(piece.media_type)));
  const heldActions = $derived(local && !row ? heldPromptRowActions(heldPromptRows([], [local])[0]!) : []);
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
    try { resent = await resendRecorded!.resend(conversationId, row.content); resendFailed = !resent; }
    catch { resendFailed = true; }
    finally { busy = false; }
  }
  async function resend(): Promise<void> { if (!local || busy) return; busy = true; try { await onSendAgain?.(local.messageId); } finally { busy = false; } }
  // B5: the person's message that was not delivered says so inside its own bubble.
  const undelivered = $derived(label === null && status?.tone === 'error' ? status : null);
  let detailsOpen = $state(false);
  let root = $state<HTMLElement | null>(null);
  const uid = $props.id();
  const detailsId = `c2-undelivered-details-${uid}`;
</script>
{#snippet undeliveredFooter()}
  {#if undelivered}
    <div class="c2-undelivered" role="alert" data-conversation-message-status="error">
      <p class="c2-undelivered-reason" data-conversation-status-next>Not delivered<span aria-hidden="true">{" · "}</span>{statusReason(undelivered)}</p>
      {#if (offersResend && !resent) || undelivered.action === 'choose-model' || undelivered.detail}
        <div class="c2-undelivered-actions">
          {#if offersResend && !resent}<button type="button" class="c2-undelivered-resend" data-conversation-resend-refused={row?.key} title={resendFailed ? "It did not go out. Resend it again." : "Send this message again, as a new message"} disabled={busy} onclick={()=>void resendRefused()}>{busy ? "Resending…" : resendFailed ? "Resend again" : "Resend"}</button>{/if}
          {#if undelivered.action === 'choose-model'}<button type="button" data-conversation-status-choose-model onclick={() => openComposerModelPicker(root)}>Choose another model</button>{/if}
          {#if undelivered.detail}<button type="button" aria-expanded={detailsOpen} aria-controls={detailsId} data-conversation-status-details onclick={() => (detailsOpen = !detailsOpen)}>Details</button>{/if}
        </div>
      {/if}
      {#if undelivered.detail}<BackendWords id={detailsId} detail={undelivered.detail} open={detailsOpen}/>{/if}
    </div>
  {/if}
{/snippet}
<article bind:this={root} class={label === null ? 'chat-u' : 'chat-system'} class:attaches-files={attachesFiles} data-conversation-accepted={row?.kind === "prompt" || local?.accepted ? "true" : undefined} data-conversation-row={row?.kind} data-conversation-outgoing={local?.messageId}>
  {#if label !== null}
    <!-- Someone other than the person wrote this (Panels, another agent): one system line,
         "From Panels · " and the words, at most two lines, on the thread's left edge (B3). -->
    <SystemLine data-conversation-system-note="" title={systemText}>
      <span class="c2-system-words"><span data-conversation-prompt-label>From {label}</span><span aria-hidden="true">{" · "}</span>{systemText}</span>
    </SystemLine>
  {:else}
    <MessagePieces {presentationRowKey} {content} {conversationId} bubble failed={undelivered !== null} footer={undelivered ? undeliveredFooter : undefined}/>
  {/if}
  {#if steered}
    <SystemLine align="end" data-conversation-steered="">Steered<span aria-hidden="true">{" · "}</span><span data-conversation-status-next>into the running turn</span></SystemLine>
  {/if}
  <div class="message-status-window" use:conversationHeight><div class="message-status">
    {#if status && undelivered === null}
      <TranscriptStatusRow {status} align={label === null ? 'end' : 'start'} role={status.tone === 'error' ? 'alert' : 'status'}
        data-conversation-message-status={status.tone} data-conversation-outgoing-label={local && !row ? '' : undefined}>
        {#snippet actions()}
          {#if heldActions.includes('send_again')}<button type="button" data-conversation-held-send-again={local?.messageId} title="Send these words again. They may already have arrived once." disabled={busy} onclick={()=>void resend()}>Send again</button>{/if}
          {#if heldActions.includes('stop_drawing')}<button type="button" data-conversation-held-stop-drawing={local?.messageId} title="Stop showing this message" disabled={busy} onclick={()=>void stop()}>Dismiss</button>{/if}
        {/snippet}
      </TranscriptStatusRow>
    {/if}
  </div></div>
  <MessageActions {content} createdAt={row?.createdAt ?? null}/>
</article>
<style>
  article { position: relative; }
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

  /* Not delivered: inside the bubble, under the words, a 1px rule 10 below them, "Not delivered ·
     <reason>" 10 under the rule in the failure red, then one row of text actions. */
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
  .c2-undelivered-actions { display: flex; flex-wrap: wrap; gap: var(--cv-s4); margin-block-start: var(--cv-s3); }
  .c2-undelivered-actions button {
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
