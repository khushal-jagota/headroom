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
</script>
<article class={label === null ? 'chat-u' : 'chat-system'} class:attaches-files={attachesFiles} class:undelivered={row?.kind === 'prompt_refused' || row?.kind === 'prompt_discarded'} class:has-callout={label === null && status?.tone === 'error'} data-conversation-accepted={row?.kind === "prompt" || local?.accepted ? "true" : undefined} data-conversation-row={row?.kind} data-conversation-outgoing={local?.messageId}>
  {#if label !== null}
    <!-- Someone other than the person wrote this (Panels, another agent). v9 (11): a left-aligned system
         note with no box: "From Panels · " and the words, 13/20 #b0b0b0, at most two lines. -->
    <!-- v11 (11): a system message, not a turn label: in a #1a1a1a well (radius 10, padding
         8 12), "From Panels" #bdbdbd 500, then the words #b0b0b0, 13/20 (p17c review: no mark). -->
    <p class="c2-system-note" data-conversation-system-note title={systemText}><span class="c2-system-words"><span class="c2-label" data-conversation-prompt-label>From {label}</span><span aria-hidden="true">{" · "}</span>{systemText}</span></p>
  {:else}
    <MessagePieces {presentationRowKey} {content} {conversationId} bubble/>
  {/if}
  {#if steered}
    <TranscriptStatusRow status={{ tone: 'quiet', title: 'Steered', next: 'into the running turn', detail: null }} mark="steer" align="end" data-conversation-steered=""/>
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

  /* v6: Panels' words are a centred system note with no box: 13/20 #b0b0b0, "From Panels · "
     then the words, at most two lines. */
  /* v9 (11): a left-aligned status line on the thread's start edge, as "Worked for" is. */
  .c2-system-note {
    align-self: flex-start;
    display: flex;
    align-items: flex-start;
    gap: 8px;
    max-width: 100%;
    box-sizing: border-box;
    margin: 0;
    padding: 8px 12px;
    border-radius: 10px;
    background: #1a1a1a;
    color: #b0b0b0;
    font: var(--cv-weight-body) 13px/20px var(--cv-font);
    text-align: start;
    overflow-wrap: anywhere;
  }
  .c2-system-words {
    min-width: 0;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
  }
  .c2-label { color: #bdbdbd; font-weight: var(--cv-weight-label); }

  /* A message that never reached the agent is not drawn as one that did: its bubble steps
     back (the well, the body tone) and (v4.2) a small red "!" stands beside it on the left, as
     a message app marks one that failed to send. The callout under it says why and offers
     Resend. No edge or rule carries it. */
  .chat-u.undelivered :global(.c2-bubble) {
    position: relative;
  }
  .chat-u.undelivered :global(.c2-bubble)::before {
    content: "";
    position: absolute;
    top: 50%;
    right: calc(100% + 8px);
    width: 16px;
    height: 16px;
    margin-top: -8px;
    /* v7: failure is #f08a7e; the mark is a 16px icon. */
    background: #f08a7e;
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
