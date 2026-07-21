<script lang="ts">
  import type { ConversationTerminalState } from "../../lib/acp/contracts";
  import type { DeepReadonly, TranscriptStanza } from "../../lib/acp/conversationState";
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import StepView from "./StepView.svelte";

  let {
    stanza,
    terminalStates,
    live,
    onThoughtExpanded,
    onToolExpanded
  }: {
    stanza: TranscriptStanza;
    terminalStates: Readonly<Record<string, DeepReadonly<ConversationTerminalState>>>;
    live: boolean;
    onThoughtExpanded: (messageId: string, partIndex: number, expanded: boolean) => void;
    onToolExpanded: (toolCallId: string, expanded: boolean) => void;
  } = $props();

  // Bare stanzas (tool calls with no owning thought part) have nowhere on the
  // wire to persist disclosure, so they keep it locally — the same UI-only
  // treatment as the synthetic live stanza. Thought-bearing stanzas persist it
  // through the thought part, exactly like today.
  let localOpen = $state(false);
  let persisted = $derived(stanza.thoughtPartIndex !== null);
  let open = $derived(persisted ? stanza.thoughtExpanded : localOpen);

  // A failing stanza arrives open — once, so the user can still collapse it.
  let failureOpened = $state(false);
  $effect(() => {
    if (!stanza.hasFailure || failureOpened) return;
    failureOpened = true;
    if (persisted && stanza.thoughtPartIndex !== null) {
      onThoughtExpanded(stanza.messageId, stanza.thoughtPartIndex, true);
    } else {
      localOpen = true;
    }
  });
  let regionId = $derived(`acp-stanza-${stanza.key}`);

  let preview = $derived.by(() => {
    for (const block of stanza.thought ?? []) {
      if (block.type === "text" && block.text.trim()) {
        return block.text.trim().split("\n")[0];
      }
    }
    return "";
  });

  function toggle(): void {
    const next = !open;
    if (persisted && stanza.thoughtPartIndex !== null) {
      onThoughtExpanded(stanza.messageId, stanza.thoughtPartIndex, next);
    } else {
      localOpen = next;
    }
  }
</script>

<section class="acp-stanza" class:is-open={open} data-acp-stanza>
  <button
    type="button"
    class="acp-think"
    class:is-live={live}
    aria-expanded={open}
    aria-controls={regionId}
    onclick={toggle}
  >
    <span class="acp-think-word">{live ? "Thinking" : "Thought"}</span>
    {#if preview}<span class="acp-think-preview">{preview}</span>{/if}
  </button>
  {#if open}
    <div id={regionId} class="acp-stanza-body">
      {#if stanza.thought}
        <div class="acp-think-body">
          {#each stanza.thought as block}
            {#if block.type === "text"}
              <MarkdownBlock text={block.text} />
            {:else}
              <div class="acp-unsupported">Unsupported agent content</div>
            {/if}
          {/each}
        </div>
      {/if}
      {#if stanza.steps.length > 0}
        <div class="acp-steps">
          {#each stanza.steps as tool (tool.toolCallId)}
            <StepView
              {tool}
              {terminalStates}
              onExpanded={(expanded) => onToolExpanded(tool.toolCallId, expanded)}
            />
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</section>
