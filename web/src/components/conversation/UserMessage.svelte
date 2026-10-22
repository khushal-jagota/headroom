<script lang="ts">
  import MessagePieces from './MessagePieces.svelte';
  import { promptLabelFor, PROMPT_DISCARDED_SENTENCE, type TranscriptRow } from '../../lib/conversation/transcript';
  import { outgoingMessageNote, type OutgoingMessage } from '../../lib/conversation/outgoing';
  import { heldPromptRowActions, heldPromptRows } from '../../lib/conversation/heldPrompts';
  import { conversationHeight, fadeConversationExit, collapseConversationRow } from '../../lib/conversation/surfaceMotion';
  let { row = null, local = null, conversationId, ownSenderLabel = null, onStopDrawingMessage, onSendAgain }: {
    row?: Extract<TranscriptRow, {kind: 'prompt'|'prompt_refused'|'prompt_uncertain'|'prompt_discarded'}> | null;
    local?: OutgoingMessage | null;
    conversationId: string;
    ownSenderLabel?: string | null;
    onStopDrawingMessage?: (id: string) => Promise<void> | void;
    onSendAgain?: (id: string) => Promise<void> | void;
  } = $props();
  let element: HTMLElement;
  let busy = $state(false);
  const label = $derived(promptLabelFor(row?.senderLabel ?? local?.senderLabel ?? '', ownSenderLabel));
  const note = $derived(row?.kind === 'prompt_refused' ? `not delivered · ${row.sentence}` : row?.kind === 'prompt_uncertain' ? 'delivery uncertain · do not resend' : row?.kind === 'prompt_discarded' ? PROMPT_DISCARDED_SENTENCE : row ? null : local ? outgoingMessageNote(local) : null);
  const actions = $derived(local && !row ? heldPromptRowActions(heldPromptRows([], [local])[0]!) : []);
  async function stop(): Promise<void> { if (!local || busy) return; busy = true; let restore: (() => void) | undefined; try { await fadeConversationExit(element); restore = await collapseConversationRow(element); await onStopDrawingMessage?.(local.messageId); } finally { restore?.(); element.style.removeProperty('opacity'); busy = false; } }
  async function resend(): Promise<void> { if (!local || busy) return; busy = true; try { await onSendAgain?.(local.messageId); } finally { busy = false; } }
</script>
<article bind:this={element} class={label === null ? 'chat-u' : 'chat-system'} class:message-outcome={note!==null} class:message-refused={row?.kind==='prompt_refused'} data-conversation-row={row?.kind} data-conversation-outgoing={local?.messageId}>
  <div class="message-label-window" use:conversationHeight><div>
    {#if label || note}<div class="c2-label" data-conversation-prompt-label={label ? '' : undefined} data-conversation-outgoing-label={local ? '' : undefined}>
      {#if actions.includes('stop_drawing')}<button type="button" class="chat-qrow-x" data-conversation-held-stop-drawing={local?.messageId} title="Stop showing this message" aria-label="Stop showing this message" disabled={busy} onclick={()=>void stop()}>×</button>{/if}
      <span>{label}{label && note ? ' · ' : ''}{note ?? ''}</span>
      {#if actions.includes('send_again')}<button type="button" class="chat-qrow-act" data-conversation-held-send-again={local?.messageId} title="Send these words again. They may already have arrived once." disabled={busy} onclick={()=>void resend()}>Send again</button>{/if}
    </div>{/if}
  </div></div>
  <MessagePieces content={row?.content ?? local?.content ?? []} {conversationId}/>
</article>
<style>
  .message-outcome { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--text-faint) 55%, transparent); }
  .message-refused { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent-error) 55%, transparent); }
  .c2-label { display:flex; align-items:center; gap:var(--space-2); color:var(--text-faint); font-family:var(--font-mono); font-size:var(--type-xs); letter-spacing:var(--tracking-mono); padding-block-end:var(--space-1); }
  .c2-label span { flex:1; }
  .message-refused .c2-label { color:var(--accent-error); }
  .chat-qrow-act { margin-inline-start:auto; }
</style>
