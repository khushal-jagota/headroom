<script lang="ts">
  /** What a message offers when the reader points at it: when it was said, and a copy of
   *  its words. Its 24 px row always stands 4 px under the message, the way chat apps put
   *  it. The transcript keeps a turn's gap (32) after every message, so the row has its room
   *  and never covers what follows (ProjectedTranscript's rhythm). The pointer shows it from
   *  the message or from the row's own area; keyboard focus on the copy button shows it too. */
  import { messageCopyText, messageTimeLabel } from "../../lib/conversation/transcript";
  let { content, createdAt = null }: {
    content: readonly { piece: string; text?: string }[];
    /** Unix seconds, as the record keeps them. Null for a message not yet recorded. */
    createdAt?: number | null;
  } = $props();
  const copyText = $derived(messageCopyText(content));
  const time = $derived(messageTimeLabel(createdAt));
  let copied = $state<"idle" | "copied" | "failed">("idle");
  let timer: ReturnType<typeof setTimeout> | null = null;
  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(copyText);
      copied = "copied";
    } catch {
      copied = "failed";
    }
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; copied = "idle"; }, 1600);
  }
  $effect(() => () => { if (timer !== null) clearTimeout(timer); });

  let actions = $state<HTMLDivElement | null>(null);
  let pointed = $state(false);
  // The row's own area is inside the message, so the pointer finds it from below or the
  // side as well as through the message.
  $effect(() => {
    const message = actions?.parentElement;
    if (!message) return;
    const enter = (): void => { pointed = true; };
    const leave = (): void => { pointed = false; };
    message.addEventListener("pointerenter", enter);
    message.addEventListener("pointerleave", leave);
    return () => {
      message.removeEventListener("pointerenter", enter);
      message.removeEventListener("pointerleave", leave);
    };
  });
  // While shown, the message's hover area reaches down over the 4 px gap to the row, so the
  // pointer never leaves it on the way to the copy button.
  $effect(() => {
    const message = actions?.parentElement;
    if (!message) return;
    if (pointed) message.setAttribute("data-message-actions-reach", "");
    else message.removeAttribute("data-message-actions-reach");
  });

  // A touch screen has no hover: a tap on the message shows its actions, and a tap
  // anywhere else puts them away, so they never sit on every message at once.
  let tapped = $state(false);
  $effect(() => {
    const message = actions?.parentElement;
    if (!message || typeof window === "undefined" || !window.matchMedia?.("(hover: none)").matches) return;
    const onTap = (event: Event): void => {
      if (event.target instanceof Element && event.target.closest("a, button, input, textarea, [role='button']") && !actions?.contains(event.target)) return;
      tapped = true;
    };
    const onElsewhere = (event: Event): void => {
      if (!(event.target instanceof Node) || !message.contains(event.target)) tapped = false;
    };
    message.addEventListener("click", onTap);
    document.addEventListener("pointerdown", onElsewhere, true);
    return () => { message.removeEventListener("click", onTap); document.removeEventListener("pointerdown", onElsewhere, true); };
  });
</script>

{#if time || copyText}
  <div class="message-actions" class:is-shown={pointed} class:is-tapped={tapped} data-message-actions bind:this={actions}>
    {#if time}<time class="message-time" datetime={time.iso} title={time.full}>{time.short}</time>{/if}
    {#if copyText}
      {@const label = copied === "copied" ? "Copied" : copied === "failed" ? "Copy failed" : "Copy"}
      <button type="button" class="message-action" data-message-copy title={copied === "idle" ? "Copy this message" : label} aria-label={copied === "idle" ? "Copy this message" : label} onclick={() => void copy()}>
        <svg class="message-action-glyph" viewBox="0 0 16 16" aria-hidden="true">
          {#if copied === "copied"}<path d="m3.5 8.5 3 3 6-7" />{:else}<rect x="5.25" y="5.25" width="8" height="8" rx="1.5" /><path d="M10.75 5.25V4a1.25 1.25 0 0 0-1.25-1.25H4A1.25 1.25 0 0 0 2.75 4v5.5A1.25 1.25 0 0 0 4 10.75h1.25" />{/if}
        </svg>
      </button>
      <span class="message-action-status" role="status">{copied === "idle" ? "" : label}</span>
    {/if}
  </div>
{/if}

<style>
  /* The 24 px row, 4 px under its message, inside the turn gap that follows it. */
  .message-actions {
    position: absolute;
    inset-block-start: calc(100% + var(--cv-hover-row-offset));
    inset-inline-start: 0;
    /* Above the message's reach (its ::after), so the pointer lands on the row's buttons. */
    z-index: 1;
    box-sizing: border-box;
    block-size: var(--cv-hover-row-height);
    display: flex;
    align-items: center;
    gap: var(--cv-s1);
    color: var(--cv-t3);
    font-family: var(--cv-font);
    font-size: var(--cv-type-caption);
    line-height: var(--cv-leading-caption);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    opacity: 0;
    pointer-events: none;
    transition: opacity var(--cv-dur-fast) var(--cv-ease-out);
  }
  /* Under the person's bubble the actions keep the bubble's right edge. */
  :global(.chat-u) > .message-actions {
    inset-inline-start: auto;
    inset-inline-end: 0;
  }
  /* The row's place is empty room kept for it, so where a pointer exists the row's own area
     answers it even while hidden: the pointer finds the time and Copy from below or the side.
     A touch screen keeps it inert until a tap shows it. */
  @media (hover: hover) {
    .message-actions { pointer-events: auto; }
  }
  .message-actions.is-shown,
  .message-actions:focus-within,
  .message-actions.is-tapped {
    opacity: 1;
    pointer-events: auto;
  }
  /* While shown, the message's hover area covers the gap down to the row's foot. */
  :global([data-message-actions-reach])::after {
    content: "";
    position: absolute;
    inset-inline: 0;
    inset-block-start: 100%;
    block-size: calc(var(--cv-hover-row-offset) + var(--cv-hover-row-height));
  }
  /* A ghost icon button: the quiet glyph, the first tone on the control step when pointed at. */
  .message-action {
    display: inline-grid;
    place-items: center;
    inline-size: var(--cv-hover-row-height);
    block-size: var(--cv-hover-row-height);
    padding: 0;
    border: 0;
    border-radius: var(--cv-r-md);
    background: transparent;
    color: var(--cv-t3);
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .message-action:hover { background: var(--cv-control); color: var(--cv-t1); }
  .message-action:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: var(--cv-focus-offset); }
  .message-action-glyph {
    flex: none;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.4;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  /* "Copied" is said, not only shown: read by a screen reader, seen beside the check. */
  .message-action-status:empty { display: none; }
  /* The time is text at the column's edge (x = 0); the copy glyph sits 4 into its button. */
  .message-time { padding-inline: 0; }
  @media (prefers-reduced-motion: reduce) { .message-actions, .message-action { transition: none; } }
</style>
