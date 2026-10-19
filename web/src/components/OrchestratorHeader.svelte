<script lang="ts">
  import type { Snippet } from "svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import ClampedText from "./ClampedText.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import RequestRecapAction from "./RequestRecapAction.svelte";

  /* ``recapTarget`` is the agent that can be asked for a fresh recap. Every agent with a
   * recap passes one; a header shown without a live agent behind it passes nothing. */
  let { title, eyebrow, recap, onSaveRecap, signal, conversationId = null, recapTarget = null }: {
    title: Snippet;
    eyebrow?: Snippet;
    recap?: string;
    onSaveRecap?: (value: string) => Promise<unknown>;
    signal?: Partial<ConversationSignal>;
    conversationId?: string | null;
    recapTarget?: { kind: "sprint_item" | "chief"; id: string } | null;
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
  {#if recapTarget !== null}
    <div class="sprint-workspace-recap-ask">
      <RequestRecapAction kind={recapTarget.kind} id={recapTarget.id} recap={recap ?? ""} />
    </div>
  {/if}
</header>

<style>
  .sprint-workspace-head { padding-top:var(--space-7); }
  .sprint-workspace-title { font-family:var(--font-ui); font-size:28px; font-weight:600; line-height:1.24; letter-spacing:-0.014em; margin-top:var(--space-3); overflow-wrap:anywhere; }
  .sprint-workspace-head :global(.sprint-workspace-brief) { font-family:var(--font-ui); font-size:17px; line-height:1.6; margin-top:var(--space-3); max-width:66ch; }
  .sprint-workspace-recap-ask { margin-top:var(--space-3); font-size:13px; color:var(--text-muted); }
</style>
