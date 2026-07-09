<script lang="ts">
  import ApprovalBlock from "./ApprovalBlock.svelte";
  import CollapsibleField from "./CollapsibleField.svelte";
  import ContentDisclosure from "./ContentDisclosure.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import ProposalCard from "./ProposalCard.svelte";
  import {
    advanceTarget,
    fieldIsPassed,
    gatingField,
    type FieldStageVisualState
  } from "../lib/ui";
  import type { TicketField } from "../lib/types";

  let {
    name,
    slot,
    ticketState,
    ceiling,
    stageState = "upcoming",
    variant = "ticket",
    recap = null,
    showRecap = false,
    emptyText = "Not written yet.",
    editableValue = true,
    onAccept,
    onApproveResult,
    onSaveNote,
    onSaveValue
  }: {
    name: string;
    slot: TicketField;
    ticketState: string;
    ceiling: string;
    stageState?: FieldStageVisualState;
    variant?: "ticket" | "review";
    recap?: string | null;
    showRecap?: boolean;
    emptyText?: string;
    editableValue?: boolean;
    onAccept: (payload: Record<string, unknown>) => Promise<unknown>;
    onApproveResult?: () => Promise<unknown>;
    onSaveNote?: (raw: string) => Promise<unknown>;
    onSaveValue?: (raw: string) => Promise<unknown>;
  } = $props();

  let reviewVariant = $derived(variant === "review");
  let fieldLabel = $derived(labelFor(name));
  let isDropped = $derived(ticketState === "dropped");
  let isGating = $derived(gatingField(ticketState) === name);
  let passed = $derived(fieldIsPassed(name, ticketState));
  let hasValue = $derived(hasText(slot.value));
  let hasNotes = $derived(hasText(slot.notes));
  let hasProposal = $derived(Boolean(slot.proposal));
  let isResultApproval = $derived(ticketState === "needs_review" && name === "result");
  let nextState = $derived(advanceTarget(ticketState, ceiling));
  let defaultOpen = $derived(isGating || isResultApproval);

  function hasText(value: unknown): boolean {
    return value !== null && value !== undefined && String(value).trim() !== "";
  }

  function labelFor(value: string): string {
    const text = value.replace(/_/g, " ").trim();
    if (!text) return "";
    return text[0].toUpperCase() + text.slice(1);
  }
</script>

{#snippet stageBody()}
  {#if showRecap && recap}
    <ContentDisclosure title="Recap" tone="support" section="recap">
      <MarkdownBlock text={recap} />
    </ContentDisclosure>
  {/if}

  {#if reviewVariant && hasNotes}
    <ContentDisclosure title="Notes" defaultOpen={false} tone="support" section="notes">
      <MarkdownBlock text={slot.notes} />
    </ContentDisclosure>
  {/if}

  {#if isDropped}
    <MarkdownBlock text={slot.value} quiet={emptyText} />
    {#if slot.proposal}
      <div class="ticket-field-proposal">
        <div class="proposal-meta">proposed by {slot.proposal.proposed_by}</div>
        <MarkdownBlock text={slot.proposal.body} />
      </div>
    {/if}
  {:else if isGating && hasProposal}
    <ApprovalBlock
      mode="gating-pending"
      layout="review"
      field={name}
      whatLabel={fieldLabel}
      proposalBody={slot.proposal?.body || ""}
      proposedBy={slot.proposal?.proposed_by || ""}
      newState={nextState}
      onApprove={onAccept}
    />
  {:else if isResultApproval}
    <ApprovalBlock
      mode="needs_review"
      layout="review"
      field={name}
      whatLabel={fieldLabel}
      proposalBody={slot.value || ""}
      onApprove={() => onApproveResult ? onApproveResult() : onAccept({})}
      onValueSave={reviewVariant ? undefined : onSaveValue}
    />
  {:else}
    {#if hasProposal && slot.proposal}
      <ProposalCard
        proposal={slot.proposal}
        newState={nextState}
        onAccept={onAccept}
      />
      {#if hasValue}<MarkdownBlock text={slot.value} />{/if}
    {:else if passed && hasValue && editableValue && onSaveValue}
      <div class="ticket-field-value">
        <InlineEdit value={slot.value} markdown multiline placeholder="Value..." onSave={onSaveValue} />
      </div>
    {:else}
      <MarkdownBlock text={slot.value} quiet={emptyText} />
    {/if}
  {/if}

  {#if !reviewVariant && onSaveNote}
    <ContentDisclosure title="Notes" defaultOpen={hasNotes} tone="support" section="note">
      <InlineEdit value={slot.notes} markdown multiline placeholder="Note..." onSave={onSaveNote} />
    </ContentDisclosure>
  {/if}
{/snippet}

{#if reviewVariant}
  <div class="ticket-stage-section ticket-stage-section--review" data-field={name}>
    {@render stageBody()}
  </div>
{:else}
  <CollapsibleField {stageState} {name} {defaultOpen} dataField={name}>
    {@render stageBody()}
  </CollapsibleField>
{/if}
