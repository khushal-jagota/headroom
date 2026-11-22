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
  /** The row whose actions are open, if any: one dropdown at a time. */
  let menuOpenFor = $state<string | null>(null);
  $effect(() => {
    if (menuOpenFor === null) return;
    const shut = (event: KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      menuOpenFor = null;
    };
    const away = (event: PointerEvent): void => {
      if (event.target instanceof Element && event.target.closest(".chat-qrow-anchor")) return;
      menuOpenFor = null;
    };
    window.addEventListener("keydown", shut, true);
    window.addEventListener("pointerdown", away, true);
    return () => { window.removeEventListener("keydown", shut, true); window.removeEventListener("pointerdown", away, true); };
  });
  /** What became of it so far, in the thread's note voice under the message. */
  function stateWord(row: HeldPromptRow): string {
    if (row.state === "in_flight") return "Sending";
    if (row.state === "unknown") return "No answer came";
    return "Queued";
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

  /** v9 (12): the first message waiting its turn carries the caption's detail. */
  let firstHeldKey = $derived(displayedRows.find(row => row.state === "held")?.key ?? null);

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

<!-- v9 (12): the full caption "Queued · sends after this turn" stands only under the first
     queued message; the ones after it say "Queued". -->
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
        <!-- v4: under the ghost bubble, one chip that says what it is ("Queued") and opens
             its actions as a dropdown anchored on it. Nothing else. -->
        <div class="chat-qrow-meta">
          {#if row.state === "in_flight"}
            <span class="chat-qrow-chip is-static" data-conversation-held-chip title={reason ?? undefined}>
              <span class="chat-qrow-spin" aria-hidden="true"></span><span>Sending</span>
            </span>
          {:else}
          <span class="chat-qrow-anchor">
            <button
              type="button"
              class="chat-qrow-chip"
              data-conversation-held-chip
              aria-haspopup="menu"
              aria-expanded={menuOpenFor === row.key}
              title={reason ?? undefined}
              disabled={actions.length === 0 || actionInFlight !== null}
              onclick={() => (menuOpenFor = menuOpenFor === row.key ? null : row.key)}
            >
              <svg class="chat-qrow-clock" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M12 8v4.2l2.6 1.6" /></svg>
              <span class="chat-qrow-word">{stateWord(row)}</span>{#if row.state === "held" && running && row.key === firstHeldKey}<span class="chat-qrow-sep" aria-hidden="true">·</span><span class="chat-qrow-detail" data-conversation-held-detail>sends after this turn</span>{/if}
            </button>
            <!-- Drawn closed as hidden, so its actions are always the same buttons. -->
              <div class="chat-qrow-menu" role="menu" aria-label="Queued message" hidden={menuOpenFor !== row.key} data-conversation-held-menu>
                {#if actions.includes("send_again")}
                  <button
                    type="button"
                    role="menuitem"
                    class="chat-qrow-item"
                    data-conversation-held-send-again={row.senderMessageId}
                    title="Send these words again. They may already have arrived once."
                    disabled={!actionable}
                    onclick={() => { menuOpenFor = null; if (row.senderMessageId !== null) void act(row.key, () => onSendAgain?.(row.senderMessageId!)); }}
                  >Send again</button>
                {/if}
                {#if supportsSteer && actions.includes("steer")}
                  <button
                    type="button"
                    role="menuitem"
                    class="chat-qrow-item"
                    data-conversation-held-promote="steer"
                    data-held-prompt-id={row.heldPromptId ?? undefined}
                    title="Put this into the turn that is already running"
                    disabled={!actionable || !running}
                    onclick={() => { menuOpenFor = null; if (row.heldPromptId !== null) void act(row.key, () => onPromote?.(row.heldPromptId!, "steer")); }}
                  >Steer now</button>
                {/if}
                {#if actions.includes("send_now")}
                  <button
                    type="button"
                    role="menuitem"
                    class="chat-qrow-item"
                    data-conversation-held-promote="send_now"
                    data-held-prompt-id={row.heldPromptId ?? undefined}
                    title={running ? "Stop the running turn and run this next" : "Run this next"}
                    disabled={!actionable}
                    onclick={() => { menuOpenFor = null; if (row.heldPromptId !== null) void act(row.key, () => onPromote?.(row.heldPromptId!, "send_now")); }}
                  >Send now</button>
                {/if}
                <button
                  type="button"
                  role="menuitem"
                  class="chat-qrow-item chat-qrow-x"
                  data-conversation-held-discard={row.heldPromptId ?? undefined}
                  data-conversation-held-stop-drawing={actions.includes("stop_drawing") ? row.senderMessageId : undefined}
                  aria-label={thisTabsOwn ? "Stop showing this message" : "Remove this message from the queue"}
                  title={thisTabsOwn ? "Stop showing this message" : "Remove from the queue"}
                  disabled={!actionable}
                  onclick={() => {
                    menuOpenFor = null;
                    if (row.heldPromptId !== null) {
                      void act(row.key, () => onDiscard?.(row.heldPromptId!));
                    } else if (thisTabsOwn) {
                      void act(row.key, () => onStopDrawing?.(row.senderMessageId!));
                    }
                  }}
                >Remove</button>
              </div>
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
   * thread, a ghost bubble, and under it one chip that opens its actions. */
  /* v8 (3): the stack's own 40px (a turn's gap) stands under the Working line; the content
     box's 16px block gap does not add to it, nor stand alone when the stack is empty. */
  .chat-queue-slot { flex: none; margin-block-start: 0; }
  .chat-queue-stack {
    display: grid;
    gap: var(--cv-gap-turn);
    margin: 0;
    padding: var(--cv-gap-turn) 0 0;
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
  /* v4.2: not sent yet, at a glance: a ghost of the bubble it will become. No fill and a 1px
     frame where the bubble's edge will be; the frame and the Queued chip say "not sent", so
     the words stay readable in #d9d9d9. */
  .chat-qrow :global(.c2-bubble) {
    background: transparent;
    /* v5.2: the frame at 16% so it holds against the canvas. */
    box-shadow: inset 0 0 0 1px rgb(255 255 255 / 16%);
    color: var(--cv-t2);
  }
  /* A picture waiting to go is a thumbnail of itself: what it is, not the picture at size. */
  .chat-qrow :global(.c2-piece-image-slot) { display: flex; justify-content: flex-end; max-width: 100%; margin: 0; }
  /* v6: a queued picture wears the queued bubble's frame (1px at 16%), at full strength. */
  .chat-qrow :global(.c2-piece-image-slot img) { width: auto; max-width: 160px; max-height: 96px; object-fit: cover; border-radius: var(--cv-r-lg); outline: 1px solid rgb(255 255 255 / 16%); outline-offset: -1px; }
  .chat-qrow-meta { display: flex; justify-content: flex-end; }
  .chat-qrow-anchor { position: relative; display: inline-flex; }
  /* The chip: clock, "Queued", chevron, 13 in the meta tone; its hover lifts onto a fill. */
  .chat-qrow-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    /* v7: the Queued chip is a Composer control: 32 tall, text-only at rest (#d9d9d9, a 16px
       clock and a 12px chevron), hover #2a2a2a, open #3a3a3a. */
    height: 32px;
    /* v8 (14): no chevron; the words are the control. v9 (3): a resting #232323 fill, so
       the chip reads as a control; its right edge meets the bubble's. */
    padding: 0 12px;
    border: 0;
    border-radius: 999px;
    background: #232323;
    color: #d9d9d9;
    font: var(--cv-weight-label) 13px/20px var(--cv-font);
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  /* v8 (3): "Queued · sends after this turn": a status line inside the chip, the label 500
     #ececec, the detail 400 #b0b0b0. */
  .chat-qrow-word { color: #ececec; }
  .chat-qrow-sep { margin-inline: -2px; color: #b0b0b0; font-weight: var(--cv-weight-body); }
  .chat-qrow-detail { color: #b0b0b0; font-weight: var(--cv-weight-body); }
  .chat-qrow-chip.is-static { cursor: default; background: none; margin-inline-end: -12px; }
  .chat-qrow-chip:not(.is-static):hover:not(:disabled) { background: #2a2a2a; color: #ececec; }
  /* p12c: an open chip is filled #3a3a3a, its words #f2f2f2, its chevron pointing up. */
  .chat-qrow-chip[aria-expanded="true"], .chat-qrow-chip[aria-expanded="true"]:hover:not(:disabled) { position: relative; z-index: 31; background: #3a3a3a; color: #f2f2f2; }
  .chat-qrow-chip:disabled { cursor: default; }
  .chat-qrow-chip:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 2px; }
  .chat-qrow-clock { width: 16px; height: 16px; fill: none; stroke: #bdbdbd; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
  .chat-qrow-spin {
    width: 12px; height: 12px; box-sizing: border-box; border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 22%); border-top-color: var(--cv-t1);
    animation: chat-qrow-spin var(--cv-spin) linear infinite;
  }
  @keyframes chat-qrow-spin { to { transform: rotate(360deg); } }
  /* The dropdown (v4): the float plane anchored on its chip, opening upward from its right
     edge; labels only, 14px on 36px rows. Remove is in the danger ink. */
  .chat-qrow-menu[hidden] { display: none; }
  .chat-qrow-menu {
    position: absolute;
    z-index: 30;
    /* v9 (4): the menu's right edge on its own chip's right edge. */
    right: 0;
    bottom: calc(100% + 8px);
    width: max-content;
    min-width: 160px;
    display: grid;
    padding: 6px;
    /* v7: a menu is a Surface (#1c1c1c, a 1px #2a2a2a edge, radius 20) floating with a shadow;
       its rows are quiet buttons. */
    border: 1px solid #2a2a2a;
    border-radius: 20px;
    background: #1c1c1c;
    box-shadow: var(--cv-shadow-float);
    animation: chat-qrow-pop var(--cv-dur-base) var(--cv-ease-out);
  }
  @keyframes chat-qrow-pop { from { opacity: 0; transform: translateY(4px); } }
  .chat-qrow-item {
    display: flex;
    align-items: center;
    min-height: 32px;
    padding: 0 14px;
    border: 0;
    border-radius: 999px;
    background: none;
    color: #ececec;
    font: var(--cv-weight-label) 13px/20px var(--cv-font);
    text-align: start;
    white-space: nowrap;
    cursor: pointer;
  }
  .chat-qrow-item:hover:not(:disabled), .chat-qrow-item:focus-visible { background: #2a2a2a; outline: none; }
  .chat-qrow-item:disabled { color: var(--cv-t3); cursor: default; }
  .chat-qrow-x { color: #ececec; }
  .chat-qrow-x:hover:not(:disabled) { color: #ececec; }
  @media (prefers-reduced-motion: reduce) {
    .chat-qrow { animation: none; }
    .chat-qrow-spin { animation: none; }
    .chat-qrow-menu { animation: none; }
  }
</style>
