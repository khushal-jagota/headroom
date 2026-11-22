<script lang="ts">
  /** What a message offers when the reader points at it: when it was said, and a copy of
   *  its words. It sits in the space above the message, so showing it moves nothing. Its
   *  words are the conversation's quiet tone, which holds 4.5:1 on the page. */
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

  // A touch screen has no hover: a tap on the message shows its actions, and a tap
  // anywhere else puts them away, so they never sit on every message at once.
  let actions = $state<HTMLDivElement | null>(null);
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
  <div class="message-actions" class:is-tapped={tapped} data-message-actions bind:this={actions}>
    {#if time}<time class="message-time" datetime={time.iso} title={time.full}>{time.short}</time>{/if}
    {#if copyText}
      <button type="button" class="button button--ghost message-action" data-message-copy title="Copy this message" onclick={() => void copy()}>
        <svg class="message-action-glyph" viewBox="0 0 16 16" aria-hidden="true">
          {#if copied === "copied"}<path d="m3.5 8.5 3 3 6-7" />{:else}<rect x="5.25" y="5.25" width="8" height="8" rx="1.5" /><path d="M10.75 5.25V4a1.25 1.25 0 0 0-1.25-1.25H4A1.25 1.25 0 0 0 2.75 4v5.5A1.25 1.25 0 0 0 4 10.75h1.25" />{/if}
        </svg>
        {copied === "copied" ? "Copied" : copied === "failed" ? "Copy failed" : "Copy"}
      </button>
    {/if}
  </div>
{/if}

<style>
  .message-actions {
    position: absolute;
    /* It reaches a step into the message's own top edge (a bubble's padding, the first
       line's leading), so it keeps air under whatever is above the message, the head's
       rule included, without moving anything. */
    inset-block-end: calc(100% - var(--space-1));
    inset-inline-end: 0;
    block-size: var(--space-5);
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-faintest);
    font-family: var(--font-ui);
    font-size: var(--type-small);
    line-height: 1;
    white-space: nowrap;
    opacity: 0;
    pointer-events: none;
    transition: opacity var(--motion-fast) var(--motion-ease);
  }
  :global(:is(.chat-a, .chat-u, .chat-system):hover) > .message-actions,
  .message-actions:focus-within,
  .message-actions.is-tapped {
    opacity: 1;
    pointer-events: auto;
  }
  /* The shared ghost button, at the thread's quiet size. It is the one thing in the row
     that acts, so it carries a mark and the body tone; the time beside it is a fact and
     stays quiet. */
  .message-action {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    height: 24px;
    padding: 0 var(--space-2);
    color: var(--text-default);
    font-size: var(--type-small);
    font-weight: var(--weight-body);
  }
  .message-action-glyph {
    flex: none;
    width: 14px;
    height: 14px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.3;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  @media (prefers-reduced-motion: reduce) { .message-actions { transition: none; } }
</style>
