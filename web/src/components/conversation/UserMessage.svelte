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
<article class={label === null ? 'chat-u' : 'chat-system'} class:attaches-files={attachesFiles} class:undelivered={row?.kind === 'prompt_refused' || row?.kind === 'prompt_discarded'} class:has-callout={label === null && status?.tone === 'error'} data-conversation-accepted={row?.kind === "prompt" || local?.accepted ? "true" : undefined} data-conversation-row={row?.kind} data-conversation-outgoing={local?.messageId}>
  {#if label !== null}
    <!-- Someone other than the person wrote this (Panels, another agent). v4.2: a meta label
         "From Panels" (13, #a3a3a3) on its own line, then the words in the prose style in
         #a3a3a3. No arrow, no box. -->
    <div class="c2-system-row">
      <div class="c2-system-body">
        <div class="c2-label" data-conversation-prompt-label>From {label}</div>
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
          {#if offersResend && !resent}<button type="button" class="cv-button c2-status-main" data-conversation-resend-refused={row?.key} title={resendFailed ? "It did not go out. Resend it again." : "Send this message again, as a new message"} disabled={busy} onclick={()=>void resendRefused()}>{busy ? "Resending…" : resendFailed ? "Resend again" : "Resend"}</button>{/if}
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

  .c2-system-row { color: var(--cv-t3); }
  /* The label on its own line in the meta style, 6px over the words (v4.3), as a turn's meta line
     stands over its prose. */
  .c2-system-body { min-width: 0; display: flex; flex-direction: column; gap: var(--cv-gap-meta); }
  .c2-label {
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
  }
  /* The sent words in the prose style (15/24), in #a3a3a3: Panels' words, not the agent's. */
  .c2-system-body :global(.markdown) {
    color: var(--cv-t3);
    font-size: var(--cv-type-prose);
    line-height: var(--cv-leading-prose);
  }
  .c2-system-body :global(.markdown :is(p, ul, ol, blockquote)) { margin-block-end: var(--cv-s3); }
  .c2-system-body :global(.markdown > :last-child) { margin-block-end: 0; }
  .c2-system-body :global(.markdown :is(h1, h2, h3, h4, h5, h6)) {
    margin-block: var(--cv-s3) 0;
    color: var(--cv-t3);
    font-size: var(--cv-type-prose);
    line-height: var(--cv-leading-prose);
  }
  .c2-system-body :global(.markdown :is(strong, b, h1, h2, h3, h4, h5, h6)) { color: var(--cv-t3); }

  /* A message that never reached the agent is not drawn as one that did: its bubble steps
     back (the well, the body tone) and (v4.2) a small red "!" stands beside it on the left, as
     a message app marks one that failed to send. The callout under it says why and offers
     Resend. No edge or rule carries it. */
  .chat-u.undelivered :global(.c2-bubble) {
    position: relative;
    background: var(--cv-well);
    color: var(--cv-t2);
  }
  .chat-u.undelivered :global(.c2-bubble)::before {
    content: "";
    position: absolute;
    top: 50%;
    right: calc(100% + 10px);
    width: 18px;
    height: 18px;
    margin-top: -9px;
    background: #e5848a;
    -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M8 1a7 7 0 1 1 0 14A7 7 0 0 1 8 1Zm0 3.2a.9.9 0 0 0-.9.9v3.4a.9.9 0 0 0 1.8 0V5.1a.9.9 0 0 0-.9-.9Zm0 6.1a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z'/%3E%3C/svg%3E") center / contain no-repeat;
            mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M8 1a7 7 0 1 1 0 14A7 7 0 0 1 8 1Zm0 3.2a.9.9 0 0 0-.9.9v3.4a.9.9 0 0 0 1.8 0V5.1a.9.9 0 0 0-.9-.9Zm0 6.1a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z'/%3E%3C/svg%3E") center / contain no-repeat;
  }
  /* v4.1: a message with an error callout spans the column so its callout can take the full
     width, left-aligned; the message's own pieces keep their right edge and 80% measure. */
  .chat-u.has-callout { align-self: stretch; max-width: none; }
  .chat-u.has-callout > :global(:not(.message-status-window)) { max-width: 80%; }

  .message-status-window { align-self: stretch; margin-block-start: calc(var(--cv-gap-bubble-stack) * -1); }
  .message-status { display: flex; flex-direction: column; }
  .message-status > :global(.c2-status) { margin-block-start: var(--cv-gap-bubble-stack); }
</style>
