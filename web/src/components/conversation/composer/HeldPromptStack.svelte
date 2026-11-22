<script lang="ts">
  import { untrack } from "svelte";
  import { conversationHeight, fadeConversationExit } from "../../../lib/conversation/surfaceMotion";
  let stackElement: HTMLDivElement;
  import MessagePieces from "../MessagePieces.svelte";
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

  // The rows there are from the first paint; later ones join, and leaving ones fade out.
  let displayedRows = $state<readonly HeldPromptRow[]>(untrack(() => rows));
  const exiting = new Set<string>();
  const faded = new Set<string>();
  $effect(() => {
    const next = rows;
    untrack(() => {
      const byKey = new Map(next.map(row => [row.key, row]));
      const previousKeys = new Set(displayedRows.map(row => row.key));
      displayedRows = [...displayedRows.map(row => byKey.get(row.key) ?? row), ...next.filter(row => !previousKeys.has(row.key))];
      for (const row of displayedRows) {
        if (byKey.has(row.key) || exiting.has(row.key)) continue;
        exiting.add(row.key);
        const element = [...(stackElement?.querySelectorAll<HTMLElement>('[data-conversation-held-row]') ?? [])]
          .find(node => node.dataset.conversationHeldRow === row.key);
        const exit = faded.has(row.key) ? Promise.resolve() : fadeConversationExit(element ?? null);
        void exit.then(() => {
          exiting.delete(row.key); faded.delete(row.key);
          if (!rows.some(current => current.key === row.key)) displayedRows = displayedRows.filter(current => current.key !== row.key);
          else element?.style.removeProperty("opacity");
        });
      }
    });
  });
  let actionInFlight = $state<string | null>(null);
  /** What became of it so far, in the thread's note voice under the message. */
  function stateWord(row: HeldPromptRow): string {
    if (row.state === "in_flight") return "Sending";
    if (row.state === "unknown") return "No answer came";
    return running ? "Queued · sends when this turn ends" : "Queued · sends next";
  }

  /** What the list is, for a screen reader: the visible lines need no header to say it. */
  function stackLabel(rows: readonly HeldPromptRow[]): string {
    const count = rows.length;
    const noun = count === 1 ? "message" : "messages";
    if (rows.some((row) => row.state === "held")) return `${count} queued ${noun}`;
    if (rows.some((row) => row.state === "in_flight")) return `${count} ${noun} sending`;
    return `${count} ${noun} not confirmed`;
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
      const row = [...stackElement.querySelectorAll<HTMLElement>("[data-conversation-held-row]")].find(node => node.dataset.conversationHeldRow === key);
      await fadeConversationExit(row ?? null);
      faded.add(key);
      await action();
    } finally {
      const remaining = [...stackElement.querySelectorAll<HTMLElement>("[data-conversation-held-row]")].find(node => node.dataset.conversationHeldRow === key);
      remaining?.style.removeProperty("opacity");
      actionInFlight = null;
    }
  }
</script>

<div class="chat-queue-slot" use:conversationHeight><div bind:this={stackElement}>
{#if displayedRows.length > 0}
  <!-- Messages waiting to be sent stand at the end of the thread, where they will land, as the
       person's own messages not sent yet: the bubble a step back in tone, and under it one
       quiet line saying so, with its actions. No card, no header, no rule. -->
  <ol class="chat-queue-stack" data-conversation-held-stack aria-label={stackLabel(displayedRows)}>
    {#each displayedRows as row (row.key)}
      {@const actions = heldPromptRowActions(row)}
      {@const actionable = actions.length > 0 && actionInFlight === null}
      {@const thisTabsOwn = row.state === "unknown" && row.senderMessageId !== null}
      {@const reason = queueReason(row)}
      <li
        class="chat-qrow"
        data-conversation-held-row={row.key}
        data-fate={row.state}
        aria-label={`Not sent yet: ${heldPromptRowLabel(row)}`}
      >
        <MessagePieces content={row.content} conversationId={conversationId ?? ""} bubble />
        <div class="chat-qrow-meta">
          <span class="chat-qrow-mark" aria-hidden="true">
            {#if row.state === "in_flight"}
              <span class="chat-qrow-spin"></span>
            {:else}
              <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" /><path d="M12 8v4.2l2.6 1.6" /></svg>
            {/if}
          </span>
          <span class="chat-qrow-why" title={reason ?? undefined}>{stateWord(row)}</span>
          {#if actions.length > 0}
          <span class="chat-qrow-actions">
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
              >Steer now</button>
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
            <button
              type="button"
              class="chat-qrow-act chat-qrow-x"
              data-conversation-held-discard={row.heldPromptId ?? undefined}
              data-conversation-held-stop-drawing={
                actions.includes("stop_drawing") ? row.senderMessageId : undefined
              }
              aria-label={thisTabsOwn ? "Stop showing this message" : "Remove this message from the queue"}
              title={thisTabsOwn ? "Stop showing this message" : "Remove from the queue"}
              disabled={!actionable}
              onclick={() => {
                if (row.heldPromptId !== null) {
                  void act(row.key, () => onDiscard?.(row.heldPromptId!));
                } else if (thisTabsOwn) {
                  void act(row.key, () => onStopDrawing?.(row.senderMessageId!));
                }
              }}
            >Remove</button>
          </span>
          {/if}
        </div>
      </li>
    {/each}
  </ol>
{/if}
</div></div>

<style>
  /* Each waiting message is the person's own message, not sent yet: on their side of the
   * thread, its bubble a step back (the well, not the raised plane, and the body tone), and
   * one note line under it in the thread's meta voice: a clock, what will happen, and its
   * actions as quiet words, 16px clear of that sentence. */
  .chat-queue-slot { flex: none; }
  .chat-queue-stack {
    display: grid;
    gap: var(--cv-s6);
    margin: 0;
    padding: var(--cv-s6) 0 0;
    list-style: none;
  }
  .chat-qrow {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: var(--cv-gap-bubble-stack);
    max-width: 80%;
    margin-inline-start: auto;
    animation: chat-qrow-in var(--cv-dur-base) var(--cv-ease-out);
  }
  @keyframes chat-qrow-in { from { opacity: 0; } to { opacity: 1; } }
  .chat-qrow :global(.c2-bubble) { background: var(--cv-well); color: var(--cv-t2); }
  /* A picture waiting to go is a thumbnail of itself: what it is, not the picture at size. */
  .chat-qrow :global(.c2-piece-image-slot) { display: flex; justify-content: flex-end; max-width: 100%; margin: 0; }
  .chat-qrow :global(.c2-piece-image-slot img) { width: auto; max-width: 160px; max-height: 96px; object-fit: cover; opacity: .8; }
  .chat-qrow-meta {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
    column-gap: 6px;
    row-gap: var(--cv-s1);
    color: var(--cv-t3);
    font-family: var(--cv-font);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
  }
  .chat-qrow-mark { flex: none; display: grid; place-items: center; width: 14px; height: 20px; }
  .chat-qrow-mark svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  .chat-qrow-spin {
    width: 12px; height: 12px; border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 22%); border-top-color: var(--cv-t1);
    animation: chat-qrow-spin var(--cv-spin) linear infinite;
  }
  @keyframes chat-qrow-spin { to { transform: rotate(360deg); } }
  .chat-qrow-why { min-width: 0; }
  /* The last action's words end on the bubble's edge; its hover fill reaches past it. */
  .chat-qrow-actions { display: inline-flex; align-items: center; gap: 2px; margin-inline: var(--cv-s3) calc(var(--cv-s2) * -1); }
  /* The actions are words, a step brighter than the sentence, lifting onto a control fill. */
  .chat-qrow-act {
    height: 28px;
    padding: 0 var(--cv-s2);
    border: 0;
    border-radius: var(--cv-r-md);
    background: none;
    color: var(--cv-t2);
    font: var(--cv-weight-label) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .chat-qrow-act:hover:not(:disabled) { background: var(--cv-control); color: var(--cv-t1); }
  .chat-qrow-act:disabled { color: var(--cv-t4); cursor: default; }
  .chat-qrow-act:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 2px; }
  @media (prefers-reduced-motion: reduce) {
    .chat-qrow { animation: none; }
    .chat-qrow-spin { animation: none; }
  }
</style>
