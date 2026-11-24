<script lang="ts">
  import type { Snippet } from "svelte";

  let { children }: { children: Snippet } = $props();
</script>

<section class="held-prompt-stack" aria-label="Queued messages" data-conversation-held-stack>
  {@render children()}
</section>

<style>
  .held-prompt-stack {
    display: flex;
    flex-direction: column;
    gap: var(--cv-gap-bubble-stack);
    padding: var(--cv-s3);
    margin-block-start: var(--cv-s3);
    border: 1px solid var(--cv-surface-edge);
    border-radius: var(--cv-surface-radius);
  }

  .held-prompt-stack :global(.c2-bubble) {
    background: transparent;
    box-shadow: inset 0 0 0 1px var(--cv-surface-edge);
  }
</style>
