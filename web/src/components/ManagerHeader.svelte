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
  let { title, heading, eyebrow, recap, onSaveRecap, signal, conversationId = null, recapTarget = null }: {
    title: Snippet;
    heading?: Snippet;
    eyebrow?: Snippet;
    recap?: string;
    onSaveRecap?: (value: string) => Promise<unknown>;
    signal?: Partial<ConversationSignal>;
    conversationId?: string | null;
    recapTarget?: { kind: "manager" | "chief"; id: string } | null;
  } = $props();
</script>

<header class="manager-workspace-head">
  {#if heading}{@render heading()}{:else}
  <!-- The line above the title is the same height on every page, so titles share one line.
       The Chief belongs to no project, so its line stays empty. -->
  {#if eyebrow}{@render eyebrow()}{:else}<div class="manager-workspace-eyebrow-slot" aria-hidden="true"></div>{/if}
  <h1 class="manager-workspace-title">
    {@render title()}
    <ConversationSignalMark {signal} {conversationId} class="manager-workspace-title-mark" />
  </h1>
  {/if}
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
        placeholder="Write a concise recap…"
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
  .manager-workspace-head { padding-top:var(--space-4); }
  .manager-workspace-eyebrow-slot { display:flex; align-items:center; min-height:24px; font-size:var(--type-sm); line-height:20px; color:var(--text-faint); }
  .manager-workspace-title { font-family:var(--font-ui); font-size:var(--type-display); font-weight:700; line-height:1.25; letter-spacing:-0.02em; margin-top:var(--space-2); overflow-wrap:anywhere; }
  /* No padding on the clamped block itself: ClampedText measures its scrollHeight
     against a line count, so padding there reports an empty recap as overflowing. */
  /* The gap sits on the animating slot, not inside it: a margin on the clamped block
     would stay behind the height the slot is measuring. */
  .manager-workspace-head :global([data-recap-slot]) { margin-top:var(--space-2); }
  .manager-workspace-head :global(.manager-workspace-recap) { font-family:var(--font-ui); font-size:var(--type-ui); font-weight:500; line-height:22px; max-width:none; color:var(--text-muted); }
  .manager-workspace-recap-ask { margin-top:var(--space-4); font-size:var(--type-sm); color:var(--text-muted); }
</style>
