<script lang="ts">
  import Disclosure from "./Disclosure.svelte";
  import StageMark from "./StageMark.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import type { TicketFieldApproval } from "../lib/types";
  import { type FieldStageVisualState } from "../lib/ui";
  import {
    fieldIsPassedFor,
    fieldLabelFor,
    gatingFieldFor,
    type Lifecycle
  } from "../lib/lifecycle";

  let {
    name,
    value = "",
    ticketStage,
    approval,
    lifecycle = null,
    stageState = "upcoming",
    variant = "ticket",
    emptyText = "Not written yet.",
    editableValue = true,
    editableCurrentValue = false,
    runLabel = null,
    runLabelAttention = false,
    readerLeftItOpen = null,
    onReaderToggle,
    onSaveValue,
    onCompleteGate
  }: {
    name: string;
    value?: string;
    ticketStage: string;
    approval?: TicketFieldApproval;
    lifecycle?: Lifecycle | null;
    stageState?: FieldStageVisualState;
    variant?: "ticket" | "review";
    emptyText?: string;
    editableValue?: boolean;
    editableCurrentValue?: boolean;
    runLabel?: string | null;
    runLabelAttention?: boolean;
    /** How the reader last left this stage, when they have touched it. A settling stage
     *  moves into the fold above and is rebuilt there, so the answer is kept by the
     *  screen rather than by this component. */
    readerLeftItOpen?: boolean | null;
    onReaderToggle?: (open: boolean) => void;
    onSaveValue?: (raw: string) => Promise<unknown>;
    onCompleteGate?: (raw: string) => Promise<unknown>;
  } = $props();

  let approvalLabel = $derived(approval?.method === "explicit_accept" && approval.principal?.kind === "sprint_item"
    ? "Approved by Orchestrator"
    : approval?.display_label);
  let reviewVariant = $derived(variant === "review");
  let fieldLabel = $derived(fieldLabelFor(lifecycle, name));
  let isGating = $derived(gatingFieldFor(lifecycle, ticketStage) === name);
  let passed = $derived(fieldIsPassedFor(lifecycle, name, ticketStage));
  let canEditValue = $derived(passed);
  let canCompleteGate = $derived(isGating && editableCurrentValue);
  let defaultOpen = $derived(readerLeftItOpen ?? isGating);

</script>

{#snippet stageBody()}
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
      {#if approvalLabel}
        <span class="ticket-field-approval">{approvalLabel}</span>
      {/if}
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

<style>
  .ticket-field-approval {
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    font-weight: 400;
    color: var(--text-faintest);
    margin-left: auto;
    text-align: right;
  }

  @media (max-width: 420px) {
    .ticket-field-approval {
      font-size: var(--type-xs);
      max-width: 52%;
      overflow-wrap: anywhere;
    }
  }
</style>
