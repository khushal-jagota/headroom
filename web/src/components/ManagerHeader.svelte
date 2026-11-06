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
  let { title, heading, eyebrow, actions, recap, onSaveRecap, signal, conversationId = null, recapTarget = null }: {
    title: Snippet;
    heading?: Snippet;
    eyebrow?: Snippet;
    /** The page's own utilities, before the recap ask at the right end of the top band. */
    actions?: Snippet;
    recap?: string;
    onSaveRecap?: (value: string) => Promise<unknown>;
    signal?: Partial<ConversationSignal>;
    conversationId?: string | null;
    recapTarget?: { kind: "manager" | "chief"; id: string } | null;
  } = $props();
</script>

<header class="manager-workspace-head">
  {#if heading}{@render heading()}{:else}
  <!-- The top band is the same height on every page, so titles share one line: where the
       page lives on the left (nothing is invented where there is no such place), and the
       page's own utilities on the right, quiet and always visible. -->
  <div class="page-band">
    <div class="page-band-place">{#if eyebrow}{@render eyebrow()}{/if}</div>
    {#if recapTarget !== null || actions}<div class="page-band-actions">
      {@render actions?.()}
      {#if recapTarget !== null}<RequestRecapAction kind={recapTarget.kind} id={recapTarget.id} />{/if}
    </div>{/if}
  </div>
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
</header>

<style>
  .manager-workspace-head { padding-top:0; }
  /* A sole goal's header (its recap last) keeps the block gap before the goal's card. */
  .manager-workspace-head:has(:global(.goal-heading)) { margin-bottom:var(--page-block-gap); }
  .manager-workspace-title { font-family:var(--font-ui); font-size:var(--type-page-title); font-weight:var(--weight-page-title); line-height:1.25; letter-spacing:-0.02em; margin-top:var(--space-1); overflow-wrap:anywhere; }
  /* No padding on the clamped block itself: ClampedText measures its scrollHeight
     against a line count, so padding there reports an empty recap as overflowing. */
  /* The gap sits on the animating slot, not inside it: a margin on the clamped block
     would stay behind the height the slot is measuring. */
  .manager-workspace-head :global([data-recap-slot]) { margin-top:var(--space-2); }
  .manager-workspace-head :global(.manager-workspace-recap) { font-family:var(--font-ui); font-size:var(--type-md); font-weight:500; line-height:24px; max-width:var(--page-prose-measure); color:var(--text-muted); }
</style>
