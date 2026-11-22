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
    <div class="c2-system-box">
      <div class="c2-label" data-conversation-prompt-label>{label}</div>
      <MessagePieces {presentationRowKey} {content} {conversationId}/>
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
          {#if offersResend && !resent}<button type="button" class="button button--ghost c2-status-action" data-conversation-resend-refused={row?.key} title={resendFailed ? "It did not go out. Resend it again." : "Send this message again, as a new message"} disabled={busy} onclick={()=>void resendRefused()}>{busy ? "Resending…" : resendFailed ? "Resend again" : "Resend"}</button>{/if}
          {#if heldActions.includes('send_again')}<button type="button" class="button button--ghost c2-status-action" data-conversation-held-send-again={local?.messageId} title="Send these words again. They may already have arrived once." disabled={busy} onclick={()=>void resend()}>Send again</button>{/if}
          {#if heldActions.includes('stop_drawing')}<button type="button" class="button button--ghost c2-status-action" data-conversation-held-stop-drawing={local?.messageId} title="Stop showing this message" disabled={busy} onclick={()=>void stop()}>Dismiss</button>{/if}
        {/snippet}
      </TranscriptStatusRow>
    {/if}
  </div></div>
  <MessageActions {content} createdAt={row?.createdAt ?? null}/>
</article>
<style>
  article { position: relative; }
  /* The message is a column of the things that make it up: the bubble of words, what was
     attached beside it, and what became of it. Only the words are in a bubble. */
  .chat-u, .chat-system { display: flex; flex-direction: column; gap: var(--space-2); background: transparent; padding: 0; border-radius: 0; }
  /* A turn of the person's reads as a turn on the dark page: their words, and the files
     they sent, stand on the raised plane with a hairline edge, a clear step off the
     page (#121212) and still a step under the composer's field (#2a2a2a). */
  .chat-u { align-items: flex-end; --user-turn-fill: var(--surface-raised); }
  .chat-u :global(.c2-bubble) { background: var(--user-turn-fill); box-shadow: inset 0 0 0 var(--border-hairline) var(--border-raised); }
  .chat-u :global(.conversation-file-card .file-preview-band) { background: var(--user-turn-fill); }
  .chat-system { align-items: stretch; }
  .attaches-files { inline-size: 100%; }
  .chat-u :global(.c2-piece-image-slot), .chat-u :global(.conversation-file-card) { margin-block: 0; }
  .chat-u :global(.conversation-file-card) { align-self: stretch; }
  /* One group with one right edge, the bubble's. A picture's slot can be wider than the
     picture (a large image sizes the slot to the column, then the picture caps itself
     inside it), so the picture is set against the slot's end, not left at its start. */
  .chat-u :global(.c2-piece-image-slot) { display: flex; justify-content: flex-end; max-width: 100%; }
  /* A sent file is one of the person's things, not a document dropped into the thread:
     its corners and inner inset are the bubble's, so text, preview and file name start on
     one line, and its preview is a glance (the full file is one click away). */
  .chat-u :global(.conversation-file-card .file-preview-card) { border-radius: var(--radius-lg); }
  .chat-u :global(.conversation-file-card .file-preview-clip:not(.file-preview-clip--loading)) { max-height: 12rem; padding: var(--space-3) var(--space-4) 0; }
  .chat-u :global(.conversation-file-card .file-preview-band) { padding: var(--space-3) var(--space-4); }
  /* Someone other than the person, in the box that says so. */
  .c2-system-box { border-radius: var(--radius-md); background: var(--surface-2); padding: var(--space-2) var(--space-3); }
  .c2-label { color:var(--text-faint); font-family:var(--font-ui); font-size:var(--type-small); padding-block-end:var(--space-1); }
  .message-status-window { align-self: stretch; margin-block-start: calc(var(--space-2) * -1); }
  .message-status { display: flex; flex-direction: column; }
  .message-status > :global(.c2-status) { margin-block-start: var(--space-2); }
  .c2-status-action { height: 24px; padding: 0 var(--space-2); font-size: var(--type-small); font-weight: var(--weight-body); }
</style>
