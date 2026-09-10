<script lang="ts">
  import { conversationFileHref } from "../../../lib/conversation/wire";
  import { base64DecodedByteCount } from "../../../lib/conversation/pendingFiles";
  import ConversationFileCard from "../ConversationFileCard.svelte";
  import { heldPromptRowLabel, type HeldPromptRow } from "../../../lib/conversation/heldPrompts";

  let {
    rows,
    conversationId = null,
    supportsSteer = false,
    running = false,
    onDiscard,
    onPromote
  }: {
    rows: readonly HeldPromptRow[];
    conversationId?: string | null;
    supportsSteer?: boolean;
    running?: boolean;
    onDiscard?: (heldPromptId: string) => Promise<void> | void;
    onPromote?: (heldPromptId: string, mode: "send_now" | "steer") => Promise<void> | void;
  } = $props();

  let actionInFlight = $state<string | null>(null);
  function stateWord(row: HeldPromptRow): string | null {
    if (row.state === "in_flight") return "sending";
    if (row.state === "unknown") return "no answer came";
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
        {@const actionable = row.heldPromptId !== null && actionInFlight === null}
        {@const word = stateWord(row)}
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
            aria-label="Do not send this message"
            title="Do not send this message"
            disabled={!actionable}
            onclick={() => row.heldPromptId === null
              ? undefined
              : void act(row.key, () => onDiscard?.(row.heldPromptId!))}
          >×</button>
          <span class="chat-qrow-txt">{heldPromptRowLabel(row)}</span>
          {#if word}<span class="chat-qrow-state">{word}</span>{/if}
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
          {#if supportsSteer}
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
