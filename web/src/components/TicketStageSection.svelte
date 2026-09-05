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
    suggestedNextCeiling = null,
    lifecycle = null,
    stageState = "upcoming",
    variant = "ticket",
    emptyText = "Not written yet.",
    editableValue = true,
    approvalDisabled = false,
    runLabel = null,
    runLabelAttention = false,
    onRelease,
    contextRow,
    onAccept,
    onSaveValue
  }: {
    name: string;
    slot: TicketField;
    ticketStage: string;
    ceiling: string;
    suggestedNextCeiling?: string | null;
    lifecycle?: Lifecycle | null;
    stageState?: FieldStageVisualState;
    variant?: "ticket" | "review";
    emptyText?: string;
    editableValue?: boolean;
    approvalDisabled?: boolean;
    runLabel?: string | null;
    runLabelAttention?: boolean;
    onRelease?: () => void;
    contextRow?: Snippet;
    onAccept: (payload: Record<string, unknown>) => Promise<unknown>;
    onSaveValue?: (raw: string) => Promise<unknown>;
  } = $props();

  let reviewVariant = $derived(variant === "review");
  let fieldLabel = $derived(labelize(name));
  let isDropped = $derived(ticketStage === "dropped");
  let isGating = $derived(gatingFieldFor(lifecycle, ticketStage) === name);
  let passed = $derived(fieldIsPassedFor(lifecycle, name, ticketStage));
  let hasValue = $derived(hasText(slot.value));
  let hasProposal = $derived(Boolean(slot.proposal));
  let nextStage = $derived(advanceTargetFor(lifecycle, ticketStage, ceiling));
  let defaultOpen = $derived(isGating);

  function hasText(value: unknown): boolean {
    return value !== null && value !== undefined && String(value).trim() !== "";
  }
</script>

{#snippet stageBody()}
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
      suggestedNextCeiling={name === "kickoff" ? suggestedNextCeiling : null}
      {lifecycle}
      {contextRow}
      disabled={approvalDisabled}
      onApprove={onAccept}
      onProposalSave={onSaveValue}
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
        onProposalSave={onSaveValue}
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

{/snippet}

{#if reviewVariant}
  <div class="ticket-stage-section ticket-stage-section--review" data-field={name}>
    {@render stageBody()}
  </div>
{:else}
  <Disclosure
    variant="stage"
    {defaultOpen}
    chevron="none"
    data-field={name}
    data-stage-state={stageState}
  >
    {#snippet summary()}
      <StageMark state={stageState} />
      <span class="disclosure-stage-name">{name}</span>
      {#if runLabel}
        <span
          class="ticket-stage-run"
          class:ticket-stage-run--attention={runLabelAttention}
          data-stage-run-label={runLabel}
        >
          {runLabel}
          {#if onRelease}
            <button
              type="button"
              class="ticket-stage-run-action"
              data-stage-release
              onclick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onRelease?.();
              }}
            >Release</button>
          {/if}
        </span>
      {/if}
    {/snippet}
    {@render stageBody()}
  </Disclosure>
{/if}
