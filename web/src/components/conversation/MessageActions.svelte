<script module lang="ts">
  /** One watcher per thread. What follows a message can change without the message changing
   *  size (a tool row arriving, the lens showing or hiding work), so when the thread changes
   *  size, every row whose message is on screen is placed again once the thread is still. */
  const rowsByThread = new Map<Element, Set<() => void>>();
  const stillTimers = new Map<Element, ReturnType<typeof setTimeout>>();
  const THREAD_STILL_MILLISECONDS = 150;
  let threadResized: ResizeObserver | null = null;
  function watchThread(thread: Element, replace: () => void): () => void {
    threadResized ??= new ResizeObserver(entries => {
      for (const { target } of entries) {
        clearTimeout(stillTimers.get(target));
        stillTimers.set(target, setTimeout(() => { stillTimers.delete(target); for (const row of rowsByThread.get(target) ?? []) row(); }, THREAD_STILL_MILLISECONDS));
      }
    });
    let rows = rowsByThread.get(thread);
    if (rows === undefined) { rows = new Set(); rowsByThread.set(thread, rows); threadResized.observe(thread); }
    rows.add(replace);
    return () => {
      rows.delete(replace);
      if (rows.size === 0) { rowsByThread.delete(thread); threadResized?.unobserve(thread); clearTimeout(stillTimers.get(thread)); stillTimers.delete(thread); }
    };
  }
</script>

<script lang="ts">
  /** What a message offers when the reader points at it: when it was said, and a copy of
   *  its words. Its 24 px row stands 4 px under the message, inside the gap that already
   *  follows, so showing it moves nothing and adds no space (B9). Where that gap is too
   *  small, it stands level with the message's last line at the column's right end, or just
   *  left of the person's bubble (hoverRowPlacement.ts). The pointer shows it from the
   *  message or from the row's own area; keyboard focus on the copy button shows it too. */
  import { messageCopyText, messageTimeLabel } from "../../lib/conversation/transcript";
  import { tick, untrack } from "svelte";
  import { placeHoverRow, type Box, type HoverRowPlace } from "../../lib/conversation/hoverRowPlacement";
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
  let rowPlace = $state<HoverRowPlace | null>({ kind: "below" });
  /** Beside: where the row's box stands inside the message, from its top and right edges. */
  let besideTop = $state(0);
  let besideEnd = $state(0);
  let pointed = $state(false);
  const placed = $derived(rowPlace !== null);
  const beside = $derived(rowPlace?.kind === "beside");
  const besideBubble = $derived(beside && actions?.parentElement?.matches(".chat-u") === true);

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
  function paints(node: Element): boolean {
    const style = getComputedStyle(node);
    return style.backgroundColor !== "rgba(0, 0, 0, 0)" || style.borderTopWidth !== "0px" || style.boxShadow !== "none"
      || node.matches("img, svg, video, canvas, input, textarea, button");
  }
  /** Where an item starts to draw: a wrapper's own padding (the queued stack's 32 px top
   *  padding, for one) is gap, not content. */
  function paintedTop(node: Element, depth = 0): number {
    const box = node.getBoundingClientRect();
    if (paints(node) || node.matches("[data-conversation-system-line]") || depth > 6) return box.top;
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
  /** What `within` draws, the row itself left out: each line of its words, and its painted
   *  boxes (a bubble, a code block, a picture). */
  function drawnIn(within: Element, row: Element): { lines: Box[]; boxes: Box[] } {
    const lines: Box[] = [], boxes: Box[] = [];
    const walker = document.createTreeWalker(within, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: node => row.contains(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    for (let node: Node | null = walker.nextNode(); node !== null; node = walker.nextNode()) {
      if (node.nodeType === Node.TEXT_NODE) {
        if (!node.textContent?.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const box of range.getClientRects()) if (box.width > 0) lines.push(box);
      } else if (node instanceof Element && paints(node)) {
        const box = node.getBoundingClientRect();
        if (box.width > 0 && box.height > 0) boxes.push(box);
      }
    }
    return { lines, boxes };
  }
  /** Room the row keeps between the end of the words and its time. */
  const WORDS_GAP = 12;
  /** Room between the row and the person's bubble. */
  const BUBBLE_GAP = 8;
  function place(): void {
    const message = actions?.parentElement;
    if (!message || !actions) return;
    const box = message.getBoundingClientRect();
    const column = message.closest(".chat-thread-content")?.getBoundingClientRect() ?? box;
    const width = actions.getBoundingClientRect().width;
    const next = drawnAfter(message);
    const drawn = drawnIn(message, actions);
    // The person's words: the row stands just left of their last bubble. Everyone else's:
    // at the column's right end.
    const bubbles = message.matches(".chat-u") ? message.querySelectorAll("[data-conversation-bubble]") : [];
    const bubble = bubbles.length > 0 ? bubbles[bubbles.length - 1] : null;
    let slot: { left: number; right: number } | null;
    let lines = drawn.lines;
    if (bubble !== null) {
      const right = bubble.getBoundingClientRect().left - BUBBLE_GAP;
      slot = right - width >= column.left ? { left: right - width, right } : null;
      lines = drawnIn(bubble, actions).lines;
    } else {
      slot = { left: column.right - width - WORDS_GAP, right: column.right };
    }
    const where = placeHoverRow({ messageBottom: box.bottom, nextTop: next === null ? null : paintedTop(next), lines, drawn: [...drawn.lines, ...drawn.boxes], slot });
    if (where?.kind === "beside" && slot !== null) {
      besideTop = where.top - box.top;
      besideEnd = box.right - slot.right;
    }
    rowPlace = where;
  }
  $effect(() => {
    const message = actions?.parentElement;
    const row = actions;
    if (!message || !row) return;
    // Placed when the message first draws and again whenever its size changes (the pane
    // opening, a window resize, a picture loading).
    const resized = new ResizeObserver(() => untrack(place));
    resized.observe(message);
    const thread = message.closest(".chat-thread-content");
    const unwatch = thread === null ? () => {} : watchThread(thread, () => {
      const box = message.getBoundingClientRect();
      if (box.bottom > 0 && box.top < window.innerHeight) untrack(place);
    });
    // A message scrolled onto the screen is placed again, as the thread may have changed
    // while it was away.
    const onScreen = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) untrack(place); });
    onScreen.observe(message);
    // The row's own area answers the pointer too (it is inside the message), so the pointer
    // finds it from below or the side. Its place is checked first: if what follows changed
    // since it was last placed, the pointer may now be over that, and then the row stays hidden.
    let crossings = 0;
    const enter = async (event: PointerEvent): Promise<void> => {
      const crossing = ++crossings;
      place();
      await tick();
      if (crossing !== crossings) return;
      const under = document.elementFromPoint(event.clientX, event.clientY);
      pointed = under !== null && message.contains(under);
    };
    const leave = (): void => { crossings += 1; pointed = false; };
    message.addEventListener("pointerenter", enter);
    message.addEventListener("pointerleave", leave);
    row.addEventListener("focusin", place);
    return () => {
      resized.disconnect();
      unwatch();
      onScreen.disconnect();
      message.removeEventListener("pointerenter", enter);
      message.removeEventListener("pointerleave", leave);
      row.removeEventListener("focusin", place);
    };
  });
  // While shown below, the message's hover area reaches down over the 4 px gap to the row, so
  // the pointer never leaves it on the way to the copy button.
  $effect(() => {
    const message = actions?.parentElement;
    if (!message) return;
    if (pointed && rowPlace?.kind === "below") message.setAttribute("data-message-actions-reach", "");
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
  <div class="message-actions" class:is-shown={pointed && placed} class:is-tapped={tapped && placed} class:is-unplaced={!placed} class:is-beside={beside} class:is-beside-bubble={besideBubble}
    style:--message-actions-beside-top={beside ? `${besideTop}px` : undefined} style:--message-actions-beside-end={beside ? `${besideEnd}px` : undefined}
    data-message-actions data-message-actions-place={rowPlace?.kind ?? "none"} bind:this={actions}>
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
  /* The 24 px row, 4 px under its message, inside the gap that follows it. */
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
  /* No room below: level with the message's last line, at the column's right end (or just
     left of the person's bubble). It takes no vertical space. */
  .message-actions.is-beside {
    inset-block-start: var(--message-actions-beside-top);
    inset-inline-start: auto;
    inset-inline-end: var(--message-actions-beside-end);
  }
  /* The 8 px between the row and the bubble belongs to the row's area, so the pointer
     crosses it without the row going away. */
  .message-actions.is-beside-bubble::after {
    content: "";
    position: absolute;
    inset-block: 0;
    inset-inline-start: 100%;
    inline-size: 8px;
  }
  /* The row's place is empty canvas (or the empty end of a line), so where a pointer exists
     the row's own area answers it even while hidden: the pointer finds the time and Copy from
     below or the side. A touch screen keeps it inert until a tap shows it. */
  @media (hover: hover) {
    .message-actions:not(.is-unplaced) { pointer-events: auto; }
  }
  /* No room anywhere: the pointer never shows the row, so it covers nothing. Copy stays in
     the keyboard's order, and only while it has focus does the row stand on the canvas over
     what follows. */
  .message-actions.is-unplaced:focus-within { background: var(--cv-canvas); }
  .message-actions.is-shown,
  .message-actions:focus-within,
  .message-actions.is-tapped {
    opacity: 1;
    pointer-events: auto;
  }
  /* While shown below, the message's hover area covers the gap down to the row's foot. */
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
