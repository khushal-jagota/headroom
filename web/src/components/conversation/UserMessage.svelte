<script lang="ts">
  import MessagePieces from './MessagePieces.svelte';
  import MessageActions from './MessageActions.svelte';
  import TranscriptStatusRow from './TranscriptStatusRow.svelte';
  import { promptLabelFor, promptOutcomeStatus, type TranscriptRow, type TranscriptStatus } from '../../lib/conversation/transcript';
  import { outgoingMessageNote, type OutgoingMessage } from '../../lib/conversation/outgoing';
  import { heldPromptRowActions, heldPromptRows } from '../../lib/conversation/heldPrompts';
  import { conversationHeight } from '../../lib/conversation/surfaceMotion';
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
  // What became of the message, said under it in the thread's status voice, never inside
  // the words the person wrote.
  const status = $derived.by((): TranscriptStatus | null => {
    if (row?.kind === 'prompt_refused' || row?.kind === 'prompt_uncertain' || row?.kind === 'prompt_discarded') return promptOutcomeStatus(row);
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
  .chat-u { align-items: flex-end; }
  .chat-system { align-items: stretch; }
  .attaches-files { inline-size: 100%; }
  .chat-u :global(.c2-piece-image-slot), .chat-u :global(.conversation-file-card) { margin-block: 0; }
  .chat-u :global(.conversation-file-card) { align-self: stretch; }
  /* Someone other than the person, in the box that says so. */
  .c2-system-box { border-radius: var(--radius-md); background: var(--surface-2); padding: var(--space-2) var(--space-3); }
  .c2-label { color:var(--text-faint); font-family:var(--font-ui); font-size:var(--type-small); padding-block-end:var(--space-1); }
  .message-status-window { align-self: stretch; margin-block-start: calc(var(--space-2) * -1); }
  .message-status { display: flex; flex-direction: column; }
  .message-status > :global(.c2-status) { margin-block-start: var(--space-2); }
  .c2-status-action { height: 24px; padding: 0 var(--space-2); font-size: var(--type-small); font-weight: var(--weight-body); }
</style>
