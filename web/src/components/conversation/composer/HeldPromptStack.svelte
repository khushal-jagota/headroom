<script lang="ts">
  import { untrack } from "svelte";
  import { conversationHeight, fadeConversationExit } from "../../../lib/conversation/surfaceMotion";
  let stackElement: HTMLDivElement;
  import { conversationFileHref } from "../../../lib/conversation/wire";
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

  let displayedRows = $state<readonly HeldPromptRow[]>([]);
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
  const headId = $props.id();
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
  {@const waitingForTheTurn = displayedRows.some((row) => row.state === "held")}
  {@const title = waitingForTheTurn ? "Queued" : displayedRows.some((row) => row.state === "in_flight") ? "Sending" : "Not confirmed"}
  <div class="chat-queue-wrap" data-conversation-held-stack>
    <!-- What the tray is, in words: these messages have not reached the agent yet. -->
    <div class="chat-queue-head" id={headId}>
      <span class="chat-queue-title">{title}</span>
      <span class="chat-queue-count">{displayedRows.length}</span>
      {#if waitingForTheTurn && running}<span class="chat-queue-note">Runs when this turn ends</span>{/if}
    </div>
    <ol
      class="chat-queue-stack"
      aria-labelledby={headId}
    >
      {#each displayedRows as row (row.key)}
        {@const actions = heldPromptRowActions(row)}
        {@const actionable = actions.length > 0 && actionInFlight === null}
        {@const thisTabsOwn = row.state === "unknown" && row.senderMessageId !== null}
        {@const word = stateWord(row)}
        {@const reason = queueReason(row)}
        {@const label = heldPromptRowLabel(row)}
        <li
          class="chat-qrow"
          data-conversation-held-row={row.key}
          data-fate={row.state}
        >
          <span class="chat-qrow-words">
            <span class="chat-qrow-txt" title={label}>
              {label}
              {#each row.content as piece}
                {#if piece.piece === "file" && ("data" in piece || conversationId !== null)}
                  · <a
                    data-conversation-held-file
                    href={"data" in piece
                      ? `data:${piece.media_type};base64,${piece.data}`
                      : conversationFileHref(conversationId!, piece.stored_file_id)}
                    download={piece.file_name}
                    title={piece.file_name}
                  >{piece.file_name}</a>
                {/if}
              {/each}
            </span>
            {#if word || reason}
              <span class="chat-qrow-why">{[word, reason].filter(Boolean).join(" · ")}</span>
            {/if}
          </span>
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
              >Steer</button>
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
              ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg></button>
          </span>
        </li>
      {/each}
    </ol>
  </div>
{/if}
</div></div>

<style>
  /* The tray reads as its own small list: a header that says what these are, then one row
   * per message with its words on the left in the body tone and its actions on the right in
   * the interface face, each a real button with a 24px target. */
  .chat-queue-wrap { padding: var(--space-2) var(--space-2) var(--space-1); }
  .chat-queue-head {
    display: flex; align-items: baseline; gap: var(--space-2);
    padding: 0 var(--space-1) var(--space-1);
    font-family: var(--font-ui); font-size: var(--type-small); font-weight: var(--weight-small);
    color: var(--tone-tertiary);
  }
  .chat-queue-title { color: var(--tone-secondary); font-weight: var(--weight-needs-you); }
  .chat-queue-count { font-variant-numeric: tabular-nums; }
  .chat-queue-note { margin-inline-start: auto; }
  .chat-qrow {
    gap: var(--space-3);
    padding: var(--space-1) 0 var(--space-1) var(--space-1);
    border-top: var(--border-hairline) solid var(--border-color);
    border-bottom: 0;
  }
  .chat-qrow-words { flex: 1; min-width: 0; display: grid; gap: 1px; }
  .chat-qrow-txt {
    color: var(--tone-primary);
    font-size: var(--type-row);
    line-height: 1.45;
  }
  .chat-qrow[data-fate="in_flight"] .chat-qrow-txt,
  .chat-qrow[data-fate="unknown"] .chat-qrow-txt { color: var(--tone-secondary); }
  .chat-qrow-why {
    color: var(--tone-tertiary);
    font-size: var(--type-small);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chat-qrow-actions { flex: none; display: flex; align-items: center; gap: 2px; }
  .chat-qrow-act {
    display: inline-flex; align-items: center; min-height: 26px;
    padding: 0 var(--space-2);
    border: 0; border-radius: var(--button-radius);
    background: transparent;
    color: var(--tone-secondary);
    font-family: var(--font-ui); font-size: var(--type-small); font-weight: var(--weight-small);
    letter-spacing: normal;
  }
  .chat-qrow-act:hover:not(:disabled) { background: var(--button-ghost-fill-hover); color: var(--tone-strong); }
  .chat-qrow-x {
    display: grid; place-items: center; width: 26px; height: 26px; padding: 0;
    border-radius: var(--button-radius); color: var(--tone-tertiary);
  }
  .chat-qrow-x svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; }
  .chat-qrow-x:hover:not(:disabled) { background: var(--button-ghost-fill-hover); color: var(--tone-strong); background-image: none; }
  .chat-qrow-act:disabled, .chat-qrow-x:disabled { opacity: .45; }
  .chat-qrow-txt a { color: inherit; text-decoration: underline; text-underline-offset: 2px; }
  @media (max-width: 520px) {
    .chat-queue-note { display: none; }
  }
</style>
