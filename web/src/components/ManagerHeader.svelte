<script lang="ts">
  import type { Snippet } from "svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import ClampedText from "./ClampedText.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import RequestRecapAction from "./RequestRecapAction.svelte";
  import RecapSlot from "./RecapSlot.svelte";

  // ``recapTarget`` is the agent that can be asked for a fresh recap. Every agent with a
  // recap passes one; a header shown without a live agent behind it passes nothing.
  let { title, heading, eyebrow, actions, recap, signal, conversationId = null, recapTarget = null, recapNote = $bindable(null) }: {
    title: Snippet;
    heading?: Snippet;
    eyebrow?: Snippet;
    /** The page's own utilities, before the recap ask at the right end of the top band. */
    actions?: Snippet;
    recap?: string;
    signal?: Partial<ConversationSignal>;
    conversationId?: string | null;
    recapTarget?: { kind: "manager" | "chief"; id: string } | null;
    /** What became of the last ask for a recap: shown under the recap, as staging. A heading
     *  that carries its own ask binds it here. */
    recapNote?: string | null;
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
      {#if recapTarget !== null}<RequestRecapAction kind={recapTarget.kind} id={recapTarget.id} bind:note={recapNote} noteInline={false} />{/if}
    </div>{/if}
  </div>
  <h1 class="manager-workspace-title">
    {@render title()}
    <!-- The title shows the unread dot, never the working spinner: the status line says Working. -->
    <ConversationSignalMark signal={signal && {...signal, working:false}} {conversationId} class="manager-workspace-title-mark" />
  </h1>
  {/if}
  {#if recap !== undefined}
    <RecapSlot>
    {#snippet children()}
    <ClampedText
      class="manager-workspace-recap"
      contentKey={recap}
      moreControlAttributes={{ "data-manager-recap-toggle": "" }}
      data-manager-recap
    >
      <MarkdownBlock text={recap} quiet="" />
    </ClampedText>
    {/snippet}
    </RecapSlot>
  {/if}
  {#if recapNote}<p class="manager-workspace-recap-note" role="status" data-request-recap-note>{recapNote}</p>{/if}
</header>

<style>
  .manager-workspace-head { padding-top:0; }
  .manager-workspace-recap-note { margin:var(--space-2) 0 0; color:var(--tone-tertiary); font-size:var(--type-small); line-height:18px; }
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
