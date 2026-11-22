<script lang="ts">
  /** What a message offers when the reader points at it: when it was said, and a copy of
   *  its words. Its 24 px slot stands 4 px under the message, inside the gap that already
   *  follows, so showing it moves nothing and adds no space (B9). Where that gap is too
   *  small, it stands under the system line that follows instead, or it is not drawn
   *  (hoverRowPlacement.ts). The pointer keeps it shown across the message, the gap, those
   *  lines and the row itself; keyboard focus on the copy button keeps it shown too. */
  import { messageCopyText, messageTimeLabel } from "../../lib/conversation/transcript";
  import { placeHoverRow, type FollowingItem } from "../../lib/conversation/hoverRowPlacement";
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
  let offset = $state(0);
  let placed = $state(true);
  let pointed = $state(false);
  /** The system lines the row stands under, besides the message: part of its hover area. */
  let passed = $state.raw<Element[]>([]);

  /** The first element after `from` in the thread that draws a box, leaving display:contents
   *  wrappers and page edges aside. */
  function drawnAfter(from: Element): Element | null {
    let node: Element | null = from;
    while (node !== null && !node.matches(".chat-thread-content")) {
      for (let next = node.nextElementSibling; next !== null; next = next.nextElementSibling) {
        const drawn = firstDrawn(next);
        if (drawn !== null) return drawn;
      }
      node = node.parentElement;
    }
    return null;
  }
  function firstDrawn(node: Element): Element | null {
    if (node.matches("[data-conversation-history-edge]")) return null;
    if (getComputedStyle(node).display === "contents") {
      for (const child of node.children) { const drawn = firstDrawn(child); if (drawn !== null) return drawn; }
      return null;
    }
    return node.getBoundingClientRect().height > 1 ? node : null;
  }
  /** Where an item starts to draw: a wrapper's own padding (the queued stack's 32 px top
   *  padding, for one) is gap, not content. */
  function paintedTop(node: Element, depth = 0): number {
    const box = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    const paints = style.backgroundColor !== "rgba(0, 0, 0, 0)" || style.borderTopWidth !== "0px" || style.boxShadow !== "none"
      || node.matches("img, svg, video, canvas, input, textarea, button, [data-conversation-system-line]");
    if (paints || depth > 6) return box.top;
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE && child.textContent?.trim()) {
        const range = document.createRange();
        range.selectNodeContents(child);
        return range.getBoundingClientRect().top;
      }
      if (child instanceof Element && getComputedStyle(child).display !== "none" && child.getBoundingClientRect().height > 1) return paintedTop(child, depth + 1);
    }
    return box.top;
  }
  /** An item that is one system line (and what it opens onto), nothing else. */
  function isSystemLine(item: Element): boolean {
    const line = item.matches("[data-conversation-system-line]") ? item : item.querySelector("[data-conversation-system-line]");
    if (line === null) return false;
    const box = item.getBoundingClientRect(), lineBox = line.getBoundingClientRect();
    return Math.abs(lineBox.top - box.top) < 1 && lineBox.bottom >= box.bottom - 1;
  }
  function place(): void {
    const message = actions?.parentElement;
    if (!message) return;
    const following: (FollowingItem & { element: Element })[] = [];
    for (let item = drawnAfter(message); item !== null && following.length < 3; item = drawnAfter(item)) {
      const box = item.getBoundingClientRect();
      following.push({ element: item, top: paintedTop(item), bottom: box.bottom, isSystemLine: isSystemLine(item) });
      if (!following.at(-1)!.isSystemLine) break;
    }
    const where = placeHoverRow(message.getBoundingClientRect().bottom, following);
    placed = where !== null;
    offset = where?.offset ?? 0;
    passed = following.slice(0, where?.passes ?? 0).map(item => item.element);
  }
  function inHoverArea(target: EventTarget | null): boolean {
    const message = actions?.parentElement;
    return target instanceof Node && (message?.contains(target) === true || passed.some(line => line.contains(target)));
  }
  function leave(event: PointerEvent): void {
    if (!inHoverArea(event.relatedTarget)) pointed = false;
  }
  $effect(() => {
    const message = actions?.parentElement;
    const row = actions;
    if (!message || !row) return;
    place();
    const enter = (): void => { place(); pointed = true; };
    message.addEventListener("pointerenter", enter);
    message.addEventListener("pointerleave", leave);
    row.addEventListener("focusin", place);
    return () => {
      message.removeEventListener("pointerenter", enter);
      message.removeEventListener("pointerleave", leave);
      row.removeEventListener("focusin", place);
    };
  });
  // The lines it stands under keep it shown while the pointer crosses them.
  $effect(() => {
    const lines = passed;
    for (const line of lines) line.addEventListener("pointerleave", leave as EventListener);
    return () => { for (const line of lines) line.removeEventListener("pointerleave", leave as EventListener); };
  });
  // While shown, the message's hover area reaches down over the gap to the row's foot, so the
  // pointer never leaves it on the way to the copy button.
  $effect(() => {
    const message = actions?.parentElement;
    if (!message) return;
    if (pointed && placed) {
      message.setAttribute("data-message-actions-reach", "");
      message.style.setProperty("--message-actions-reach", `${offset + 28}px`);
    } else {
      message.removeAttribute("data-message-actions-reach");
    }
  });

  // A touch screen has no hover: a tap on the message shows its actions, and a tap
  // anywhere else puts them away, so they never sit on every message at once.
  let tapped = $state(false);
  $effect(() => {
    const message = actions?.parentElement;
    if (!message || typeof window === "undefined" || !window.matchMedia?.("(hover: none)").matches) return;
    const onTap = (event: Event): void => {
      if (event.target instanceof Element && event.target.closest("a, button, input, textarea, [role='button']") && !actions?.contains(event.target)) return;
      place();
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
  <div class="message-actions" class:is-shown={pointed && placed} class:is-tapped={tapped && placed} class:is-unplaced={!placed} style:--message-actions-offset="{offset}px" data-message-actions bind:this={actions}>
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
  /* The 24 px slot, 4 px under its message (or under the system line it passes). */
  .message-actions {
    position: absolute;
    inset-block-start: calc(100% + var(--message-actions-offset, 0px));
    inset-inline-start: 0;
    /* Above the message's reach (its ::after), so the pointer lands on the row's buttons. */
    z-index: 1;
    box-sizing: border-box;
    block-size: calc(var(--cv-hover-row-offset) + var(--cv-hover-row-height));
    padding-block-start: var(--cv-hover-row-offset);
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
  /* No room under the message: the pointer never shows the row, so it covers nothing. Copy
     stays in the keyboard's order, and only while it has focus does the row stand on the
     canvas over what follows. */
  .message-actions.is-unplaced:focus-within { background: var(--cv-canvas); }
  /* Under the person's bubble the actions keep the bubble's right edge. */
  :global(.chat-u) > .message-actions {
    inset-inline-start: auto;
    inset-inline-end: 0;
  }
  .message-actions.is-shown,
  .message-actions:focus-within,
  .message-actions.is-tapped {
    opacity: 1;
    pointer-events: auto;
  }
  /* While shown, the message's hover area covers the gap down to the row's foot. System lines
     it passes are positioned later in the thread, so they stay on top and keep their actions. */
  :global([data-message-actions-reach])::after {
    content: "";
    position: absolute;
    inset-inline: 0;
    inset-block-start: 100%;
    block-size: var(--message-actions-reach);
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
