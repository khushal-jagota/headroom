<script lang="ts">
  import { conversationFileHref } from "../../../lib/conversation/wire";
  import { base64DecodedByteCount } from "../../../lib/conversation/pendingFiles";
  import ConversationFileCard from "../ConversationFileCard.svelte";
  import {
    heldPromptRowActions,
    heldPromptRowLabel,
    type HeldPromptRow
  } from "../../../lib/conversation/heldPrompts";

  let {
    rows,
    conversationId = null,
    supportsSteer = false,
    running = false,
    onDiscard,
    onPromote,
    onStopDrawing,
    onSendAgain
  }: {
    rows: readonly HeldPromptRow[];
    conversationId?: string | null;
    supportsSteer?: boolean;
    running?: boolean;
    onDiscard?: (heldPromptId: string) => Promise<void> | void;
    onPromote?: (heldPromptId: string, mode: "send_now" | "steer") => Promise<void> | void;
    /** Stop drawing a copy only this browser has. There is no held prompt to discard. */
    onStopDrawing?: (senderMessageId: string) => Promise<void> | void;
    onSendAgain?: (senderMessageId: string) => Promise<void> | void;
  } = $props();

  let actionInFlight = $state<string | null>(null);
  function stateWord(row: HeldPromptRow): string | null {
    if (row.state === "in_flight") return "sending";
    if (row.state === "unknown") return "no answer came";
    return null;
  }

  function queueReason(row: HeldPromptRow): string | null {
    if (row.queueReason === "attachment") return "Queued because attachments cannot steer";
    if (row.queueReason === "run_change") return "Queued to apply the run change";
    if (row.queueReason === "steer_refused") return "Queued because the turn did not accept steering";
    if (row.queueReason === "command_needs_its_own_turn") return "Queued because a command runs as its own turn";
    return null;
  }

  async function act(key: string, action: () => Promise<void> | void): Promise<void> {
    if (actionInFlight !== null) return;
    actionInFlight = key;
    try {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (!reduceMotion) await new Promise((resolve) => window.setTimeout(resolve, 90));
      await action();
    } finally {
      actionInFlight = null;
    }
  }
</script>

{#if rows.length > 0}
  <div class="chat-queue-wrap" data-conversation-held-stack>
    <ol
      class="chat-queue-stack"
      aria-label="Messages waiting"
    >
      {#each rows as row (row.key)}
        {@const actions = heldPromptRowActions(row)}
        {@const actionable = actions.length > 0 && actionInFlight === null}
        {@const thisTabsOwn = row.state === "unknown" && row.senderMessageId !== null}
        {@const word = stateWord(row)}
        {@const reason = queueReason(row)}
        <li
          class="chat-qrow"
          class:is-leaving={actionInFlight === row.key}
          data-conversation-held-row={row.key}
          data-fate={row.state}
        >
          <button
            type="button"
            class="chat-qrow-x"
            data-conversation-held-discard={row.heldPromptId ?? undefined}
            data-conversation-held-stop-drawing={
              actions.includes("stop_drawing") ? row.senderMessageId : undefined
            }
            aria-label={thisTabsOwn ? "Stop showing this message" : "Do not send this message"}
            title={thisTabsOwn ? "Stop showing this message" : "Do not send this message"}
            disabled={!actionable}
            onclick={() => {
              if (row.heldPromptId !== null) {
                void act(row.key, () => onDiscard?.(row.heldPromptId!));
              } else if (thisTabsOwn) {
                void act(row.key, () => onStopDrawing?.(row.senderMessageId!));
              }
            }}
          >×</button>
          <span class="chat-qrow-txt">{heldPromptRowLabel(row)}</span>
          {#if word}<span class="chat-qrow-state">{word}</span>{/if}
          {#if reason}<span class="chat-qrow-state">{reason}</span>{/if}
          {#if actions.includes("send_again")}
            <button
              type="button"
              class="chat-qrow-act"
              data-conversation-held-send-again={row.senderMessageId}
              title="Send these words again. They may already have arrived once."
              disabled={!actionable}
              onclick={() => row.senderMessageId === null
                ? undefined
                : void act(row.key, () => onSendAgain?.(row.senderMessageId!))}
            >Send again</button>
          {/if}
          {#if actions.includes("send_now")}
            <button
              type="button"
              class="chat-qrow-act"
              data-conversation-held-promote="send_now"
              data-held-prompt-id={row.heldPromptId ?? undefined}
              title={running ? "Stop the running turn and run this next" : "Run this next"}
              disabled={!actionable}
              onclick={() => row.heldPromptId === null
                ? undefined
                : void act(row.key, () => onPromote?.(row.heldPromptId!, "send_now"))}
            >Send now</button>
          {/if}
          {#if supportsSteer && actions.includes("steer")}
            <button
              type="button"
              class="chat-qrow-act"
              data-conversation-held-promote="steer"
              data-held-prompt-id={row.heldPromptId ?? undefined}
              title="Put this into the turn that is already running"
              disabled={!actionable || !running}
              onclick={() => row.heldPromptId === null
                ? undefined
                : void act(row.key, () => onPromote?.(row.heldPromptId!, "steer"))}
            >Steer</button>
          {/if}
          {#each row.content as piece}
            {#if piece.piece === "file" && ("data" in piece || conversationId !== null)}
              <div class="chat-qrow-file">
                <ConversationFileCard
                  href={"data" in piece
                    ? `data:${piece.media_type};base64,${piece.data}`
                    : conversationFileHref(conversationId!, piece.stored_file_id)}
                  fileName={piece.file_name}
                  mediaType={piece.media_type}
                  byteCount={"data" in piece ? base64DecodedByteCount(piece.data) : piece.byte_count}
                />
              </div>
            {/if}
          {/each}
        </li>
      {/each}
    </ol>
  </div>
{/if}

<style>
  .chat-qrow-file { grid-column: 2 / -1; min-width: 0; }
</style>
