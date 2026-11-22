<script lang="ts">
  import { untrack } from "svelte";
  import { conversationHeight, fadeConversationExit } from "../../../lib/conversation/surfaceMotion";
  let stackElement: HTMLDivElement;
  import MessagePieces from "../MessagePieces.svelte";
  import SystemLine from "../SystemLine.svelte";
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
    onSendAgain,
    onEdit
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
    /** v11 (4): take a queued message back into the composer to change it: it leaves the
     *  queue and its words come back to the composer. Offered for words-only messages. */
    onEdit?: (heldPromptId: string, words: string) => Promise<void> | void;
  } = $props();

  /** The words of a words-only message (an Edit puts them back); null when it carries a
   *  picture or a file, which the composer could not take back as it was sent. */
  function editableWords(row: HeldPromptRow): string | null {
    if (row.heldPromptId === null) return null;
    if (row.content.some((piece) => piece.piece !== "text")) return null;
    const words = row.content.map((piece) => piece.piece === "text" ? piece.text : "").join("");
    return words.trim() === "" ? null : words;
  }

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

  /** v10 (2): every queued message carries the same chip, "Queued · 1 of 2", "Queued · 2 of 2":
   *  its place among the messages waiting their turn. */
  let heldKeys = $derived(displayedRows.filter(row => row.state === "held").map(row => row.key));

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

<!-- v11 (4): every queued message shows the same note, "Queued · 1 of 2", "Queued · 2 of 2", and a more button. -->
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
        <!-- Under the ghost bubble, one system line (B3), "Queued · 1 of 2", then a 28px
             quiet "more" button that opens its actions. -->
        <div class="chat-qrow-meta">
          <SystemLine align="end" mark={row.state === "in_flight" ? "spinner" : null} data-conversation-held-chip="" title={reason ?? undefined}>
            {stateWord(row)}{#if row.state === "held" && heldKeys.includes(row.key)}<span aria-hidden="true">{" · "}</span><span data-conversation-held-detail>{heldKeys.indexOf(row.key) + 1} of {heldKeys.length}</span>{/if}
          </SystemLine>
          {#if row.state !== "in_flight"}
          {@const words = editableWords(row)}
          <span class="chat-qrow-anchor">
            <button
              type="button"
              class="chat-qrow-more"
              data-conversation-held-more
              aria-haspopup="menu"
              aria-expanded={menuOpenFor === row.key}
              aria-label="Queued message actions"
              title="More"
              disabled={actions.length === 0 || actionInFlight !== null}
              onclick={() => (menuOpenFor = menuOpenFor === row.key ? null : row.key)}
            ><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.25" /><circle cx="8" cy="8" r="1.25" /><circle cx="12.5" cy="8" r="1.25" /></svg></button>
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
                {#if onEdit && words !== null}
                  <button
                    type="button"
                    role="menuitem"
                    class="chat-qrow-item"
                    data-conversation-held-edit={row.heldPromptId ?? undefined}
                    title="Take this message out of the queue and back into the composer"
                    disabled={!actionable}
                    onclick={() => { menuOpenFor = null; if (row.heldPromptId !== null) void act(row.key, () => onEdit?.(row.heldPromptId!, words)); }}
                  >Edit</button>
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
     thread, a ghost of the bubble it will become, and under it one chip that opens its
     actions. Each stands a turn's gap under what is above it. The slot ends the transcript
     and takes no rhythm gap of its own: the stack pads itself. */
  .chat-queue-slot { flex: none; min-width: 0; margin-block-start: 0; }
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
  /* Not sent yet, at a glance: no fill and a 1px frame where the bubble's edge will be. The
     frame and the chip say "not sent", so the words stay readable in the second tone. */
  .chat-qrow :global(.c2-bubble) {
    background: transparent;
    box-shadow: inset 0 0 0 1px #4a4a4a;
    color: var(--cv-t2);
  }
  /* A waiting picture is the size a sent one is (app.css, prose media); the bubble is as wide
     as its widest content. */
  .chat-qrow :global(.c2-piece-image-slot) { display: flex; justify-content: flex-end; max-width: 100%; margin: 0; }
  .chat-qrow :global(.c2-bubble--attached .c2-piece-image-slot) { justify-content: flex-start; }
  /* The row is the system line's 20px line; the 28px button overhangs it 4px above and
     below. The row's right edge is the bubble's. */
  .chat-qrow-meta { display: flex; align-items: center; justify-content: flex-end; gap: var(--cv-s1); height: 20px; }
  .chat-qrow-anchor { position: relative; display: inline-flex; }
  .chat-qrow-more {
    flex: none;
    display: inline-grid;
    place-items: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border: 0;
    border-radius: var(--cv-r-pill);
    background: none;
    color: #c4c4c4;
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .chat-qrow-more svg { width: 16px; height: 16px; fill: currentColor; }
  .chat-qrow-more:hover:not(:disabled) { background: var(--cv-control); color: var(--cv-ink); }
  .chat-qrow-more[aria-expanded="true"], .chat-qrow-more[aria-expanded="true"]:hover:not(:disabled) { position: relative; z-index: 31; background: var(--cv-control-edge); color: var(--cv-t1); }
  .chat-qrow-more:disabled { cursor: default; }
  .chat-qrow-more:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 2px; }
  /* The menu: a Surface floating with a shadow, anchored on its chip, opening upward with its
     right edge on the chip's; its rows are quiet buttons. */
  .chat-qrow-menu[hidden] { display: none; }
  .chat-qrow-menu {
    position: absolute;
    z-index: 30;
    right: 0;
    bottom: calc(100% + var(--cv-s2));
    width: max-content;
    min-width: 160px;
    display: grid;
    padding: 6px;
    border: 1px solid var(--cv-surface-edge);
    border-radius: var(--cv-surface-radius);
    background: var(--cv-surface);
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
    border-radius: var(--cv-r-pill);
    background: none;
    color: var(--cv-ink);
    font: var(--cv-weight-label) var(--cv-type-control)/var(--cv-leading-control) var(--cv-font);
    text-align: start;
    white-space: nowrap;
    cursor: pointer;
  }
  .chat-qrow-item:hover:not(:disabled), .chat-qrow-item:focus-visible { background: var(--cv-control); outline: none; }
  .chat-qrow-item:disabled { color: var(--cv-t3); cursor: default; }
  @media (prefers-reduced-motion: reduce) {
    .chat-qrow { animation: none; }
    .chat-qrow-menu { animation: none; }
  }
</style>
