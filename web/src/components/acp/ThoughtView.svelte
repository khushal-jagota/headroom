<script lang="ts">
  import type { MessagePart } from "../../vendor/acp-components-core/src/types/index";
  import type { DeepReadonly } from "../../lib/acp/conversationState";
  import MarkdownBlock from "../MarkdownBlock.svelte";

  type ThoughtPart = Extract<DeepReadonly<MessagePart>, { readonly type: "thought" }>;

  let {
    part,
    messageId,
    partIndex,
    onExpanded
  }: {
    part: ThoughtPart;
    messageId: string;
    partIndex: number;
    onExpanded: (expanded: boolean) => void;
  } = $props();

  let regionId = $derived(`acp-thought-${messageId}-${partIndex}`);
</script>

<section class="acp-disclosure" data-acp-thought>
  <button
    type="button"
    class="acp-disclosure-button"
    aria-expanded={Boolean(part.expanded)}
    aria-controls={regionId}
    onclick={() => onExpanded(!part.expanded)}
  >
    Thinking
  </button>
  {#if part.expanded}
    <div id={regionId} class="acp-disclosure-body">
      {#each part.thought as block}
        {#if block.type === "text"}
          <MarkdownBlock text={block.text} />
        {:else}
          <div class="acp-unsupported">Unsupported agent content</div>
        {/if}
      {/each}
    </div>
  {/if}
</section>

<style>
  .acp-disclosure {
    color: var(--text-muted);
    font-family: var(--font-ui);
    font-size: var(--type-sm);
  }
  .acp-disclosure-button {
    border: 0;
    background: transparent;
    color: var(--text-muted);
    font: inherit;
    padding: var(--space-1) 0;
    cursor: pointer;
  }
  .acp-disclosure-button:hover { color: var(--text-strong); }
  .acp-disclosure-body {
    border-left: var(--border-hairline) solid var(--border-color);
    color: var(--text-default);
    margin-top: var(--space-1);
    padding-left: var(--space-3);
  }
  .acp-unsupported { color: var(--text-faint); }
</style>
