<script lang="ts">
  import type { Snippet } from "svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import ClampedText from "./ClampedText.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import RequestRecapAction from "./RequestRecapAction.svelte";
  import RecapSlot from "./RecapSlot.svelte";

  // ``recapTarget`` is the agent that can be asked for a fresh recap. Every agent with a
  // recap passes one; a header shown without a live agent behind it passes nothing.
  let { title, eyebrow, recap, onSaveRecap, signal, conversationId = null, recapTarget = null }: {
    title: Snippet;
    eyebrow?: Snippet;
    recap?: string;
    onSaveRecap?: (value: string) => Promise<unknown>;
    signal?: Partial<ConversationSignal>;
    conversationId?: string | null;
    recapTarget?: { kind: "manager" | "chief"; id: string } | null;
  } = $props();
</script>

<header class="manager-workspace-head">
  {@render eyebrow?.()}
  <h1 class="manager-workspace-title" data-feedback-page-title>
    {@render title()}
    <ConversationSignalMark {signal} {conversationId} class="manager-workspace-title-mark" />
  </h1>
  {#if recap !== undefined && onSaveRecap}
    <RecapSlot>
    {#snippet children()}
    <ClampedText
      class="manager-workspace-recap"
      contentKey={recap}
      moreControlAttributes={{ "data-manager-recap-toggle": "" }}
      data-manager-recap
    >
      <InlineEdit
        value={recap}
        markdown
        multiline
        ariaLabel="Manager recap"
        placeholder="Write a concise recap for this manager…"
        onSave={onSaveRecap}
      />
    </ClampedText>
    {/snippet}
    </RecapSlot>
  {/if}
  {#if recapTarget !== null}
    <div class="manager-workspace-recap-ask">
      <RequestRecapAction kind={recapTarget.kind} id={recapTarget.id} />
    </div>
  {/if}
</header>

<style>
  .manager-workspace-head { padding-top:var(--space-7); }
  .manager-workspace-title { font-family:var(--font-ui); font-size:28px; font-weight:600; line-height:1.24; letter-spacing:-0.014em; margin-top:var(--space-3); overflow-wrap:anywhere; }
  /* No padding on the clamped block itself: ClampedText measures its scrollHeight
     against a line count, so padding there reports an empty recap as overflowing. */
  /* The gap sits on the animating slot, not inside it: a margin on the clamped block
     would stay behind the height the slot is measuring. */
  .manager-workspace-head :global([data-recap-slot]) { margin-top:var(--space-3); }
  .manager-workspace-head :global(.manager-workspace-recap) { font-family:var(--font-ui); font-size:17px; line-height:1.6; max-width:66ch; color:var(--text-faint); }
  .manager-workspace-recap-ask { margin-top:var(--space-3); font-size:13px; color:var(--text-muted); }
</style>
