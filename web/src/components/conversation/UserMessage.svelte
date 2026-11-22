<script lang="ts">
  import MessagePieces from './MessagePieces.svelte';
  import MessageActions from './MessageActions.svelte';
  import TranscriptStatusRow from './TranscriptStatusRow.svelte';
  import { promptLabelFor, promptOutcomeStatus, type TranscriptRow, type TranscriptStatus } from '../../lib/conversation/transcript';
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
</script>
<article class={label === null ? 'chat-u' : 'chat-system'} class:attaches-files={attachesFiles} data-conversation-accepted={row?.kind === "prompt" || local?.accepted ? "true" : undefined} data-conversation-row={row?.kind} data-conversation-outgoing={local?.messageId}>
  {#if label !== null}
    <!-- Someone other than the person wrote this (Panels, another agent). It is a quiet
         system row: an arrow, who sent it, then their words, all in the meta voice. No box:
         the small size, the quiet tone and the left edge say it is not the person's. -->
    <div class="c2-system-row">
      <svg class="c2-system-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9.5M9 4.5 12.5 8 9 11.5" /></svg>
      <div class="c2-system-body">
        <div class="c2-label" data-conversation-prompt-label>{label}</div>
        <MessagePieces {presentationRowKey} {content} {conversationId}/>
      </div>
    </div>
  {:else}
    <MessagePieces {presentationRowKey} {content} {conversationId} bubble/>
  {/if}
  {#if steered}
    <TranscriptStatusRow status={{ tone: 'quiet', title: 'Steered into the running turn', detail: null }} mark="steer" align="end" data-conversation-steered=""/>
  {/if}
  <div class="message-status-window" use:conversationHeight><div class="message-status">
    {#if status}
      <TranscriptStatusRow {status} align={label === null ? 'end' : 'start'} role={status.tone === 'error' ? 'alert' : 'status'}
        data-conversation-message-status={status.tone} data-conversation-outgoing-label={local && !row ? '' : undefined}>
        {#snippet actions()}
          {#if offersResend && !resent}<button type="button" class="cv-button cv-button--secondary cv-button--small" data-conversation-resend-refused={row?.key} title={resendFailed ? "It did not go out. Resend it again." : "Send this message again, as a new message"} disabled={busy} onclick={()=>void resendRefused()}>{busy ? "Resending…" : resendFailed ? "Resend again" : "Resend"}</button>{/if}
          {#if heldActions.includes('send_again')}<button type="button" class="cv-button cv-button--ghost cv-button--small" data-conversation-held-send-again={local?.messageId} title="Send these words again. They may already have arrived once." disabled={busy} onclick={()=>void resend()}>Send again</button>{/if}
          {#if heldActions.includes('stop_drawing')}<button type="button" class="cv-button cv-button--ghost cv-button--small" data-conversation-held-stop-drawing={local?.messageId} title="Stop showing this message" disabled={busy} onclick={()=>void stop()}>Dismiss</button>{/if}
        {/snippet}
      </TranscriptStatusRow>
    {/if}
  </div></div>
  <MessageActions {content} createdAt={row?.createdAt ?? null}/>
</article>
<style>
  article { position: relative; }
  /* The message is a column of the things that make it up: what was attached, the bubble of
     words, and what became of it, 8 apart on one right edge (design v3 §3.2). */
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
  .chat-system { align-self: stretch; align-items: stretch; }
  .attaches-files { min-inline-size: min(100%, 320px); }
  .chat-u :global(.c2-piece-image-slot), .chat-u :global(.conversation-file-card) { margin-block: 0; }
  /* One group with one right edge, the bubble's. A picture's slot can be wider than the
     picture, so the picture is set against the slot's end. */
  .chat-u :global(.c2-piece-image-slot) { display: flex; justify-content: flex-end; max-width: 100%; }
  .chat-u :global(.conversation-file-card--media) { align-self: flex-end; }

  .c2-system-row {
    display: grid;
    grid-template-columns: 14px minmax(0, 1fr);
    column-gap: var(--cv-s2);
    color: var(--cv-t3);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
  }
  .c2-system-mark {
    inline-size: 14px;
    block-size: 14px;
    margin-block-start: 3px;
    color: var(--cv-t4);
    fill: none;
    stroke: currentColor;
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  /* One line in the thread's event voice, like "Steered into the running turn": who sent it,
     then their words running on after it, wrapping under both. */
  .c2-system-body { min-width: 0; display: block; }
  .c2-label { float: left; margin-inline-end: 6px; color: var(--cv-t2); font-weight: var(--cv-weight-label); }
  /* The sent words keep their structure but speak in the row's small, quiet voice. */
  .c2-system-body :global(.markdown) {
    color: var(--cv-t3);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
  }
  .c2-system-body :global(.markdown :is(p, ul, ol, blockquote)) { margin-block-end: var(--cv-s1); }
  .c2-system-body :global(.markdown > :last-child) { margin-block-end: 0; }
  .c2-system-body :global(.markdown :is(h1, h2, h3, h4, h5, h6)) {
    margin-block: var(--cv-s2) 0;
    color: var(--cv-t2);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
  }
  .c2-system-body :global(.markdown strong) { color: var(--cv-t2); }

  .message-status-window { align-self: stretch; margin-block-start: calc(var(--cv-gap-bubble-stack) * -1); }
  .message-status { display: flex; flex-direction: column; }
  .message-status > :global(.c2-status) { margin-block-start: var(--cv-gap-bubble-stack); }
</style>
