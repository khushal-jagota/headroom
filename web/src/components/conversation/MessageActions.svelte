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
  /* The shared ghost button, at the thread's quiet size. */
  .message-action {
    height: 24px;
    padding: 0 var(--space-2);
    font-size: var(--type-small);
    font-weight: var(--weight-body);
  }
  @media (prefers-reduced-motion: reduce) { .message-actions { transition: none; } }
</style>
