<script lang="ts">
  import type { Snippet } from "svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import ClampedText from "./ClampedText.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import ConversationSignalMark from "./ConversationSignalMark.svelte";

  let { title, eyebrow, recap, onSaveRecap, signal, conversationId = null }: {
    title: Snippet;
    eyebrow?: Snippet;
    recap?: string;
    onSaveRecap?: (value: string) => Promise<unknown>;
    signal?: ConversationSignal;
    conversationId?: string | null;
  } = $props();
</script>

<header class="sprint-workspace-head">
  {@render eyebrow?.()}
  <h1 class="sprint-workspace-title" data-feedback-page-title>
    {@render title()}
    <ConversationSignalMark {signal} {conversationId} class="sprint-workspace-title-mark" />
  </h1>
  {#if recap !== undefined && onSaveRecap}
    <ClampedText
      class="sprint-workspace-brief"
      contentKey={recap}
      moreControlAttributes={{ "data-sprint-item-brief-toggle": "" }}
      data-sprint-item-brief
    >
      <InlineEdit
        value={recap}
        markdown
        multiline
        ariaLabel="Orchestrator recap"
        placeholder="Write a concise recap for this orchestrator…"
        onSave={onSaveRecap}
      />
    </ClampedText>
  {/if}
</header>

<style>
  .sprint-workspace-head { padding-top:var(--space-7); }
  .sprint-workspace-title { font-family:var(--font-ui); font-size:28px; font-weight:600; line-height:1.24; letter-spacing:-0.014em; margin-top:var(--space-3); overflow-wrap:anywhere; }
  .sprint-workspace-head :global(.sprint-workspace-brief) { font-family:var(--font-ui); font-size:17px; line-height:1.6; margin-top:var(--space-3); max-width:66ch; }
</style>
