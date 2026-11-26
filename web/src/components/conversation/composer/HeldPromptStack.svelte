<script lang="ts">
  import { conversationHeight } from "../../../lib/conversation/surfaceMotion";
  import { queuedMessageLabel } from "../../../lib/conversation/heldPrompts";
  import { promptLabelFor } from "../../../lib/conversation/transcript";
  import { displayedOutgoingContent, editableHeldWords, type TranscriptSendActions } from "../../../lib/conversation/transcriptOutgoing";
  import type { OutgoingMessage } from "../../../lib/conversation/outgoing";

  let { messages, actions, ownSenderLabel }: {
    /** The messages waiting for the agent, in the order it will take them. */
    messages: readonly OutgoingMessage[];
    actions: TranscriptSendActions;
    ownSenderLabel: string;
  } = $props();

  let actionInFlight = $state<string | null>(null);
  /** The line whose actions are out. A pointer shows them on hover; a touch, or a narrow
   *  composer, shows them for the line that was pressed. */
  let openLine = $state<string | null>(null);

  async function act(key: string, action: () => Promise<unknown> | void): Promise<void> {
    if (actionInFlight !== null) return;
    actionInFlight = key;
    try { await action(); }
    finally { actionInFlight = null; }
  }

  function queueReason(message: OutgoingMessage): string | undefined {
    const reason = message.presentation?.kind === "held" ? message.presentation.row.queueReason : null;
    if (reason === "attachment") return "Queued because attachments cannot join the running turn";
    if (reason === "run_change") return "Queued to apply the run change";
    if (reason === "steer_refused") return "Queued because the running turn could not take it";
    if (reason === "command_needs_its_own_turn") return "Queued because a command runs as its own turn";
    return undefined;
  }
</script>

<!-- The messages waiting for the agent: one quiet tray on the composer's top edge, one line
     each, with what can be done to it. A message leaves the tray when the agent takes it,
     and then it is in the conversation. -->
<div class="chat-queue-slot" use:conversationHeight><div>
{#if messages.length > 0}
  <ol class="chat-queue" aria-label={messages.length === 1 ? "1 queued message" : `${messages.length} queued messages`} data-conversation-held-stack>
    {#each messages as message (message.messageId)}
      {@const row = message.presentation?.kind === "held" ? message.presentation.row : null}
      {@const upload = message.presentation?.kind === "upload" ? message.presentation : null}
      {@const from = promptLabelFor(message.senderLabel, ownSenderLabel)}
      {@const editable = row ? editableHeldWords(row) : null}
      {@const idle = actionInFlight === null}
      {@const actionable = !!row?.heldPromptId && row.state === "held"}
      <li class="chat-qrow" data-conversation-held-row={message.messageId} data-open={openLine === message.messageId || undefined} title={queueReason(message)}>
        {#snippet words()}{#if from}<span class="chat-qrow-from">{from}{" · "}</span>{/if}{queuedMessageLabel(displayedOutgoingContent(message))}{/snippet}
        {#if actionable}
          <button type="button" class="chat-qrow-txt" aria-expanded={openLine === message.messageId}
            onclick={() => (openLine = openLine === message.messageId ? null : message.messageId)}>{@render words()}</button>
        {:else}
          <span class="chat-qrow-txt">{@render words()}</span>
        {/if}
        {#if row?.heldPromptId && actionable}
          {@const heldPromptId = row.heldPromptId}
          <span class="chat-qrow-acts">
          {#if actions.canSteer()}
            <button type="button" class="chat-qrow-act" data-conversation-held-promote="steer" data-held-prompt-id={heldPromptId}
              title="Put this into the turn that is already running" disabled={!idle || !actions.running()}
              onclick={() => void act(message.messageId, () => actions.promote(row, "steer"))}>Steer</button>
          {/if}
          <button type="button" class="chat-qrow-act" data-conversation-held-promote="send_now" data-held-prompt-id={heldPromptId}
            title={actions.running() ? "Stop the running turn and run this next" : "Run this next"} disabled={!idle}
            onclick={() => void act(message.messageId, () => actions.promote(row, "send_now"))}>Send now</button>
          {#if editable !== null && actions.canEditHeld()}
            <button type="button" class="chat-qrow-act" data-conversation-held-edit={heldPromptId}
              title="Take this message out of the queue and back into the composer" disabled={!idle}
              onclick={() => void act(message.messageId, () => actions.editHeld(row, editable))}>Edit</button>
          {/if}
          </span>
          <button type="button" class="chat-qrow-x" data-conversation-held-discard={heldPromptId}
            aria-label="Remove this message from the queue" title="Remove from the queue" disabled={!idle}
            onclick={() => void act(message.messageId, () => actions.discard(row))}
          ><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4.5 4.5 7 7m0-7-7 7" /></svg></button>
        {:else}
          <span class="chat-qrow-state" role="status">{upload?.note ?? (message.knownFate === "accepted_for_delivery" ? "Accepted for delivery" : "Sending…")}</span>
        {/if}
      </li>
    {/each}
  </ol>
{/if}
</div></div>

<style>
  .chat-queue-slot { min-width: 0; }
  /* An inset tray that sits on the composer's top edge: a step darker than the composer,
     its own edge open at the bottom where the composer's begins. */
  .chat-queue {
    margin: 0 var(--cv-s4);
    padding: var(--cv-s1) var(--cv-s1) var(--cv-s1) var(--cv-s3);
    list-style: none;
    max-height: 168px;
    overflow-y: auto;
    scrollbar-width: thin;
    background: var(--cv-well);
    border: 1px solid var(--cv-surface-edge);
    border-bottom: 0;
    border-radius: var(--cv-r-lg) var(--cv-r-lg) 0 0;
  }
  .chat-qrow {
    display: flex;
    align-items: center;
    gap: var(--cv-s1);
    min-width: 0;
    min-height: 32px;
  }
  .chat-qrow + .chat-qrow { border-top: 1px solid var(--cv-line); }
  .chat-qrow-txt {
    flex: 1;
    min-width: 0;
    padding: 0;
    border: 0;
    background: none;
    text-align: start;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--cv-t2);
    font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
  }
  button.chat-qrow-txt { cursor: pointer; }
  button.chat-qrow-txt:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 2px; border-radius: var(--cv-r-sm); }
  .chat-qrow-from, .chat-qrow-state { color: var(--cv-t3); }
  .chat-qrow-acts { flex: none; display: flex; align-items: center; gap: 2px; transition: opacity var(--cv-dur-fast) var(--cv-ease-out); }
  /* With a pointer, a line's actions come up when the pointer or the keyboard is on it. */
  @media (hover: hover) {
    .chat-qrow:not(:hover, :focus-within, [data-open]) .chat-qrow-acts { opacity: 0; }
  }
  /* A narrow composer gives the words the line; a pressed line opens under its words. */
  @container (max-width: 520px) {
    .chat-qrow { flex-wrap: wrap; }
    .chat-qrow:not([data-open]) .chat-qrow-acts { display: none; }
    .chat-qrow[data-open] .chat-qrow-txt { white-space: normal; }
    .chat-qrow[data-open] .chat-qrow-acts { order: 3; flex-basis: 100%; justify-content: flex-end; padding-block-end: var(--cv-s1); }
  }
  .chat-qrow-state {
    flex: none;
    padding-inline: var(--cv-s2);
    font: var(--cv-weight-body) var(--cv-type-control)/var(--cv-leading-control) var(--cv-font);
  }
  .chat-qrow-act, .chat-qrow-x {
    flex: none;
    height: 28px;
    border: 0;
    border-radius: var(--cv-r-pill);
    background: none;
    color: var(--cv-quiet-ink);
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .chat-qrow-act {
    padding: 0 10px;
    font: var(--cv-weight-label) var(--cv-type-control)/var(--cv-leading-control) var(--cv-font);
    white-space: nowrap;
  }
  .chat-qrow-x { display: inline-grid; place-items: center; width: 28px; padding: 0; }
  .chat-qrow-x svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; }
  .chat-qrow-act:hover:not(:disabled), .chat-qrow-x:hover:not(:disabled) { background: var(--cv-control); color: var(--cv-ink); }
  .chat-qrow-act:focus-visible, .chat-qrow-x:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: -2px; }
  .chat-qrow-act:disabled, .chat-qrow-x:disabled { color: var(--cv-t3); opacity: .55; cursor: default; }
</style>
