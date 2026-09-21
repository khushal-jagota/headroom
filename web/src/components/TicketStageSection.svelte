<script lang="ts">
  import ApprovalBlock from "./ApprovalBlock.svelte";
  import Disclosure from "./Disclosure.svelte";
  import StageMark from "./StageMark.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import { type FieldStageVisualState } from "../lib/ui";
  import {
    advanceTargetFor,
    fieldIsPassedFor,
    fieldLabelFor,
    gatingFieldFor,
    type Lifecycle
  } from "../lib/lifecycle";
  import type { Snippet } from "svelte";
  import type { PendingTicketProposal } from "../lib/types";

  let {
    name,
    value = "",
    pendingProposal = null,
    ticketStage,
    ceiling,
    lifecycle = null,
    sprintItem = null,
    stageState = "upcoming",
    variant = "ticket",
    emptyText = "Not written yet.",
    editableValue = true,
    editableCurrentValue = false,
    approvalDisabled = false,
    runLabel = null,
    runLabelAttention = false,
    readerLeftItOpen = null,
    onReaderToggle,
    contextRow,
    onAccept,
    onSaveValue,
    onCompleteGate
  }: {
    name: string;
    value?: string;
    pendingProposal?: PendingTicketProposal | null;
    ticketStage: string;
    ceiling: string;
    lifecycle?: Lifecycle | null;
    sprintItem?: { id: string; title: string } | null;
    stageState?: FieldStageVisualState;
    variant?: "ticket" | "review";
    emptyText?: string;
    editableValue?: boolean;
    editableCurrentValue?: boolean;
    approvalDisabled?: boolean;
    runLabel?: string | null;
    runLabelAttention?: boolean;
    /** How the reader last left this stage, when they have touched it. A settling stage
     *  moves into the fold above and is rebuilt there, so the answer is kept by the
     *  screen rather than by this component. */
    readerLeftItOpen?: boolean | null;
    onReaderToggle?: (open: boolean) => void;
    contextRow?: Snippet;
    onAccept: (payload: Record<string, unknown>) => Promise<unknown>;
    onSaveValue?: (raw: string) => Promise<unknown>;
    onCompleteGate?: (raw: string) => Promise<unknown>;
  } = $props();

  let reviewVariant = $derived(variant === "review");
  let fieldLabel = $derived(fieldLabelFor(lifecycle, name));
  let isGating = $derived(gatingFieldFor(lifecycle, ticketStage) === name);
  let passed = $derived(fieldIsPassedFor(lifecycle, name, ticketStage));
  let hasProposal = $derived(pendingProposal?.field === name);
  let nextStage = $derived(advanceTargetFor(lifecycle, ticketStage, ceiling));
  let canEditValue = $derived(passed);
  let canCompleteGate = $derived(isGating && editableCurrentValue && !hasProposal);
  let defaultOpen = $derived(readerLeftItOpen ?? isGating);

</script>

{#snippet stageBody()}
  {#if isGating && hasProposal}
    <ApprovalBlock
      layout="review"
      field={name}
      whatLabel={fieldLabel}
      proposalBody={pendingProposal?.body || ""}
      proposedBy={pendingProposal?.proposed_by || ""}
      newStage={nextStage}
      {lifecycle}
      {sprintItem}
      {contextRow}
      disabled={approvalDisabled}
      onApprove={onAccept}
    />
  {:else}
    {#if canCompleteGate && editableValue && onCompleteGate}
      <div class="ticket-field-value">
        <InlineEdit {value} markdown multiline placeholder="Value..." onSave={onCompleteGate} />
      </div>
    {:else if canEditValue && editableValue && onSaveValue}
      <div class="ticket-field-value">
        <InlineEdit {value} markdown multiline placeholder="Value..." onSave={onSaveValue} />
      </div>
    {:else}
      <MarkdownBlock text={value} quiet={emptyText} />
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
    {onReaderToggle}
    chevron="none"
    data-field={name}
    data-stage-state={stageState}
  >
    {#snippet summary()}
      <StageMark state={stageState} />
      <span class="disclosure-stage-name">{fieldLabel}</span>
      {#if runLabel}
        <span
          class="ticket-stage-run"
          class:ticket-stage-run--attention={runLabelAttention}
          data-stage-run-label={runLabel}
        >
          {runLabel}
        </span>
      {/if}
    {/snippet}
    {@render stageBody()}
  </Disclosure>
{/if}
