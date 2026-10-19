<script lang="ts">
  import type { Snippet } from "svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import ClampedText from "./ClampedText.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import RequestRecapAction from "./RequestRecapAction.svelte";
  import RecapSlot from "./RecapSlot.svelte";

  // The goal pair is optional because Chief has a recap and no goal: it is permanent,
  // so what it is for is not a thing anyone edits.
  // ``recapTarget`` is the agent that can be asked for a fresh recap. Every agent with a
  // recap passes one; a header shown without a live agent behind it passes nothing.
  let { title, eyebrow, goal, onSaveGoal, recap, onSaveRecap, signal, conversationId = null, recapTarget = null }: {
    title: Snippet;
    eyebrow?: Snippet;
    goal?: string;
    onSaveGoal?: (value: string) => Promise<unknown>;
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
  {#if goal !== undefined && onSaveGoal}
    <ClampedText
      class="sprint-workspace-goal"
      contentKey={goal}
      moreControlAttributes={{ "data-sprint-item-goal-toggle": "" }}
      data-sprint-item-goal
    >
      <InlineEdit
        value={goal}
        markdown
        multiline
        ariaLabel="Orchestrator goal"
        placeholder="Write what this orchestrator is for…"
        onSave={onSaveGoal}
      />
    </ClampedText>
  {/if}
  {#if recap !== undefined && onSaveRecap}
    {#if goal !== undefined}<p class="sprint-workspace-recap-label">Recap</p>{/if}
    <RecapSlot>
    {#snippet children()}
    <ClampedText
      class="sprint-workspace-recap"
      contentKey={recap}
      moreControlAttributes={{ "data-sprint-item-recap-toggle": "" }}
      data-sprint-item-recap
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
    {/snippet}
    </RecapSlot>
  {/if}
  {#if recapTarget !== null}
    <div class="sprint-workspace-recap-ask">
      <RequestRecapAction kind={recapTarget.kind} id={recapTarget.id} />
    </div>
  {/if}
</header>

<style>
  .sprint-workspace-head { padding-top:var(--space-7); }
  .sprint-workspace-title { font-family:var(--font-ui); font-size:28px; font-weight:600; line-height:1.24; letter-spacing:-0.014em; margin-top:var(--space-3); overflow-wrap:anywhere; }
  .sprint-workspace-head :global(.sprint-workspace-goal) { font-family:var(--font-ui); font-size:17px; line-height:1.6; margin-top:var(--space-3); max-width:66ch; }
  /* No padding on the clamped block itself: ClampedText measures its scrollHeight
     against a line count, so padding there reports an empty recap as overflowing. */
  .sprint-workspace-recap-label { font-family:var(--font-ui); font-size:11px; letter-spacing:0.06em; text-transform:uppercase; color:var(--text-faintest); margin-top:var(--space-4); }
  /* The gap sits on the animating slot, not inside it: a margin on the clamped block
     would stay behind the height the slot is measuring. */
  .sprint-workspace-head :global([data-recap-slot]) { margin-top:var(--space-1); }
  .sprint-workspace-head :global(.sprint-workspace-recap) { font-family:var(--font-ui); font-size:15px; line-height:1.6; max-width:66ch; }
  .sprint-workspace-recap-ask { margin-top:var(--space-3); font-size:13px; color:var(--text-muted); }
</style>
