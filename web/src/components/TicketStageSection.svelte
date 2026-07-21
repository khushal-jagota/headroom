<script lang="ts">
  import ApprovalBlock from "./ApprovalBlock.svelte";
  import Disclosure from "./Disclosure.svelte";
  import StageMark from "./StageMark.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import { labelize, type FieldStageVisualState } from "../lib/ui";
  import {
    advanceTargetFor,
    fieldIsPassedFor,
    gatingFieldFor,
    type Lifecycle
  } from "../lib/lifecycle";
  import type { Snippet } from "svelte";
  import type { TicketField } from "../lib/types";

  let {
    name,
    slot,
    ticketStage,
    ceiling,
    lifecycle = null,
    stageState = "upcoming",
    variant = "ticket",
    recap = null,
    showRecap = false,
    emptyText = "Not written yet.",
    editableValue = true,
    beforeApproval,
    onAccept,
    onSaveNote,
    onSaveValue
  }: {
    name: string;
    slot: TicketField;
    ticketStage: string;
    ceiling: string;
    lifecycle?: Lifecycle | null;
    stageState?: FieldStageVisualState;
    variant?: "ticket" | "review";
    recap?: string | null;
    showRecap?: boolean;
    emptyText?: string;
    editableValue?: boolean;
    beforeApproval?: Snippet;
    onAccept: (payload: Record<string, unknown>) => Promise<unknown>;
    onSaveNote?: (raw: string) => Promise<unknown>;
    onSaveValue?: (raw: string) => Promise<unknown>;
  } = $props();

  let reviewVariant = $derived(variant === "review");
  let fieldLabel = $derived(labelize(name));
  let isDropped = $derived(ticketStage === "dropped");
  let isGating = $derived(gatingFieldFor(lifecycle, ticketStage) === name);
  let passed = $derived(fieldIsPassedFor(lifecycle, name, ticketStage));
  let hasValue = $derived(hasText(slot.value));
  let hasNotes = $derived(hasText(slot.user_note));
  let hasProposal = $derived(Boolean(slot.proposal));
  let nextStage = $derived(advanceTargetFor(lifecycle, ticketStage, ceiling));
  let defaultOpen = $derived(isGating);

  function hasText(value: unknown): boolean {
    return value !== null && value !== undefined && String(value).trim() !== "";
  }
</script>

{#snippet stageBody()}
  {#if showRecap && recap}
    <div class="review-context" data-content-section="recap">
      <div class="review-context-label">Recap</div>
      <div class="review-context-recap"><MarkdownBlock text={recap} /></div>
    </div>
  {/if}

  {#if reviewVariant && hasNotes}
    <Disclosure title="Notes" variant="support" defaultOpen={false} data-content-section="notes">
      <MarkdownBlock text={slot.user_note} />
    </Disclosure>
  {/if}

  {#if beforeApproval}
    {@render beforeApproval()}
  {/if}

  {#if isDropped}
    <MarkdownBlock text={slot.value} quiet={emptyText} />
    {#if slot.proposal}
      <ApprovalBlock
        mode="readonly"
        field={name}
        whatLabel={fieldLabel}
        proposalBody={slot.proposal?.body || ""}
        proposedBy={slot.proposal?.proposed_by || ""}
      />
    {/if}
  {:else if isGating && hasProposal}
    <ApprovalBlock
      mode="gating-pending"
      layout="review"
      field={name}
      whatLabel={fieldLabel}
      proposalBody={slot.proposal?.body || ""}
      proposedBy={slot.proposal?.proposed_by || ""}
      newStage={nextStage}
      {lifecycle}
      onApprove={onAccept}
    />
  {:else}
    {#if hasProposal && slot.proposal}
      <ApprovalBlock
        mode="proposal"
        field={name}
        whatLabel={fieldLabel}
        proposalBody={slot.proposal?.body || ""}
        proposedBy={slot.proposal?.proposed_by || ""}
        newStage={nextStage}
        {lifecycle}
        onApprove={onAccept}
      />
      {#if hasValue}<MarkdownBlock text={slot.value} />{/if}
    {:else if passed && editableValue && onSaveValue}
      <div class="ticket-field-value">
        <InlineEdit value={slot.value} markdown multiline placeholder="Value..." onSave={onSaveValue} />
      </div>
    {:else}
      <MarkdownBlock text={slot.value} quiet={emptyText} />
    {/if}
  {/if}

  {#if !reviewVariant && onSaveNote}
    <Disclosure title="Notes" variant="support" defaultOpen={hasNotes} data-content-section="note">
      <InlineEdit value={slot.user_note} markdown multiline placeholder="Note..." onSave={onSaveNote} />
    </Disclosure>
  {/if}
{/snippet}

{#if reviewVariant}
  <div class="ticket-stage-section ticket-stage-section--review" data-field={name}>
    {@render stageBody()}
  </div>
{:else}
  <Disclosure
    variant="stage"
    {defaultOpen}
    data-field={name}
    data-stage-state={stageState}
  >
    {#snippet summary()}
      <StageMark state={stageState} />
      <span class="disclosure-stage-name">{name}</span>
    {/snippet}
    {@render stageBody()}
  </Disclosure>
{/if}
