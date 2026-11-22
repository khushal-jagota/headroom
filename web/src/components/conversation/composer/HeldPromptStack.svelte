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
  /** Rows whose whole message is showing; the rest are one line. */
  let expanded = $state(new Set<string>());
  function toggle(key: string): void {
    const next = new Set(expanded);
    if (!next.delete(key)) next.add(key);
    expanded = next;
  }
  let actionInFlight = $state<string | null>(null);
  function stateWord(row: HeldPromptRow): string | null {
    if (row.state === "in_flight") return "sending";
    if (row.state === "unknown") return "no answer came";
    return null;
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
  <!-- The waiting messages are the whole of it: no card and no header. Each is one quiet line of
       its own words, led by a small clock that says it is waiting, with its actions beside it. -->
  <ol class="chat-queue-stack" data-conversation-held-stack aria-label={stackLabel(displayedRows)}>
    {#each displayedRows as row (row.key)}
      {@const actions = heldPromptRowActions(row)}
      {@const actionable = actions.length > 0 && actionInFlight === null}
      {@const thisTabsOwn = row.state === "unknown" && row.senderMessageId !== null}
      {@const word = stateWord(row)}
      {@const reason = queueReason(row)}
      {@const label = heldPromptRowLabel(row)}
      {@const why = [word, reason].filter(Boolean).join(" · ")}
      <li
        class="chat-qrow"
        class:open={expanded.has(row.key)}
        data-conversation-held-row={row.key}
        data-fate={row.state}
      >
        <span class="chat-qrow-mark" title={why || (running ? "Runs when this turn ends" : "Waiting to send")} aria-hidden="true">
          {#if row.state === "in_flight"}
            <span class="chat-qrow-spin"></span>
          {:else}
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" /><path d="M12 8v4.2l2.6 1.6" /></svg>
          {/if}
        </span>
        <button
          type="button"
          class="chat-qrow-words"
          aria-expanded={expanded.has(row.key)}
          title={expanded.has(row.key) ? "Show one line" : "Show the whole message"}
          onclick={() => toggle(row.key)}
        >
          <span class="chat-qrow-txt">{label}</span>
          {#if why}<span class="chat-qrow-why">{why}</span>{/if}
        </button>
        {#each row.content as piece}
          {#if piece.piece === "file" && ("data" in piece || conversationId !== null)}
            <a
              class="chat-qrow-file"
              data-conversation-held-file
              href={"data" in piece
                ? `data:${piece.media_type};base64,${piece.data}`
                : conversationFileHref(conversationId!, piece.stored_file_id)}
              download={piece.file_name}
              title={piece.file_name}
            >{piece.file_name}</a>
          {/if}
        {/each}
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
            aria-label={thisTabsOwn ? "Stop showing this message" : "Remove this message from the queue"}
            title={thisTabsOwn ? "Stop showing this message" : "Remove"}
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
{/if}
</div></div>

<style>
  /* Light, not a tray: the lines sit on the canvas over the composer, their left edge on the
   * draft's own inset, 4px between them and 8px clear of the shell (the stack's gap). The words
   * are the body tone a step down; the clock and the actions are quieter still, and the actions
   * lift to the strongest tone under the pointer or the keyboard. */
  .chat-queue-stack {
    display: grid;
    gap: var(--cv-s1);
    margin: 0;
    padding: 0 var(--cv-s2) 0 var(--cv-s4);
    list-style: none;
    max-height: 168px;
    overflow-y: auto;
    scrollbar-width: thin;
  }
  .chat-qrow {
    display: flex;
    align-items: center;
    gap: var(--cv-s2);
    min-width: 0;
    min-height: 28px;
    animation: chat-qrow-in var(--cv-dur-base) var(--cv-ease-out);
  }
  @keyframes chat-qrow-in { from { opacity: 0; } to { opacity: 1; } }
  .chat-qrow.open { align-items: flex-start; }
  .chat-qrow-mark { flex: none; display: grid; place-items: center; width: 14px; height: 28px; color: var(--cv-t4); }
  .chat-qrow-mark svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  .chat-qrow-spin {
    width: 12px; height: 12px; border-radius: 50%;
    border: 1.6px solid rgb(255 255 255 / 16%); border-top-color: var(--cv-t2);
    animation: chat-qrow-spin var(--cv-spin) linear infinite;
  }
  @keyframes chat-qrow-spin { to { transform: rotate(360deg); } }
  /* The words are one line until pressed, then the whole message. */
  .chat-qrow-words {
    flex: 1;
    min-width: 0;
    display: grid;
    padding: 4px 0;
    background: none;
    border: 0;
    border-radius: var(--cv-r-sm);
    text-align: left;
    cursor: pointer;
    font-family: var(--cv-font);
  }
  .chat-qrow-txt {
    min-width: 0;
    color: var(--cv-t2);
    font-size: var(--cv-type-ui);
    line-height: var(--cv-leading-ui);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chat-qrow.open .chat-qrow-txt { white-space: pre-wrap; overflow-wrap: anywhere; }
  .chat-qrow-words:hover .chat-qrow-txt { color: var(--cv-t1); }
  .chat-qrow[data-fate="in_flight"] .chat-qrow-txt,
  .chat-qrow[data-fate="unknown"] .chat-qrow-txt { color: var(--cv-t3); }
  .chat-qrow-why {
    color: var(--cv-t3);
    font-size: var(--cv-type-caption);
    line-height: var(--cv-leading-caption);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chat-qrow-file {
    flex: 0 1 auto; min-width: 0; max-width: 30%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    color: var(--cv-t3); font-family: var(--cv-font); font-size: var(--cv-type-meta);
    text-decoration: underline; text-decoration-color: var(--cv-link-underline); text-underline-offset: 3px;
  }
  .chat-qrow-actions { flex: none; display: flex; align-items: center; gap: 2px; }
  /* Small, quiet actions: the ghost button at 28px in the quiet tone. */
  .chat-qrow-act { color: var(--cv-t3); padding: 0 var(--cv-s2); }
  .chat-qrow-x {
    display: grid; place-items: center; width: 28px; height: 28px; padding: 0;
    background: none; border: 0; border-radius: var(--cv-r-md);
    color: var(--cv-t3); cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .chat-qrow-x svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; }
  .chat-qrow-x:hover:not(:disabled) { background: var(--cv-control); color: var(--cv-t1); }
  .chat-qrow-x:disabled { color: var(--cv-t4); cursor: default; }
  .chat-qrow-words:focus-visible,
  .chat-qrow-x:focus-visible,
  .chat-qrow :global(.chat-qrow-act:focus-visible),
  .chat-qrow-file:focus-visible {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .chat-qrow { animation: none; }
    .chat-qrow-spin { animation: none; }
  }
</style>
