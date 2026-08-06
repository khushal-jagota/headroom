<script lang="ts">
  import { tick } from "svelte";
  import { messageContentText } from "../../../lib/conversation/wire";
  import type { HeldPromptRow } from "../../../lib/conversation/heldPrompts";

  let {
    rows,
    hermes = false,
    running = false,
    onDiscard,
    onPromote
  }: {
    rows: readonly HeldPromptRow[];
    hermes?: boolean;
    running?: boolean;
    onDiscard?: (heldPromptId: string) => Promise<void> | void;
    onPromote?: (heldPromptId: string, mode: "send_now" | "steer") => Promise<void> | void;
  } = $props();

  let actionInFlight = $state<string | null>(null);
  let stackElement = $state<HTMLOListElement | null>(null);
  let stackClientHeight = $state(0);
  let stackScrollHeight = $state(0);
  let scrollable = $derived(stackScrollHeight > stackClientHeight + 1);

  $effect(() => {
    rows;
    stackClientHeight;
    void tick().then(() => {
      stackScrollHeight = stackElement?.scrollHeight ?? 0;
    });
  });

  function rowText(row: HeldPromptRow): string {
    const words = messageContentText(row.content);
    if (words !== "") return words;
    const imageCount = row.content.filter((piece) => piece.piece === "image").length;
    return imageCount === 1 ? "Image message" : `${imageCount} images`;
  }

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
      class:is-scrollable={scrollable}
      aria-label="Messages waiting"
      bind:this={stackElement}
      bind:clientHeight={stackClientHeight}
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
          <span class="chat-qrow-txt">{rowText(row)}</span>
          {#if word}<span class="chat-qrow-state">{word}</span>{/if}
          {#if hermes}
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
        </li>
      {/each}
    </ol>
  </div>
{/if}
