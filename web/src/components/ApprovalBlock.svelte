<script lang="ts">
  import { type Snippet } from "svelte";
  import Button from "./Button.svelte";
  import Disclosure from "./Disclosure.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import CeilingPicker from "./CeilingPicker.svelte";
  import { OWNER_HOLDER } from "../lib/ceilingHolder";
  import { fieldLabelFor, preferredScopeCeilingFor, type Lifecycle } from "../lib/lifecycle";
  import type { Principal } from "../lib/types";

  let {
    field = "",
    whatLabel = "",
    proposalBody = "",
    proposedBy = "",
    proposalCreatedAt = null,
    proposalRevision = null,
    note = "",
    newStage = null,
    lifecycle = null,
    sprintItem = null,
    layout = "default",
    disabled = false,
    onApprove,
    onNoteSave,
    actions,
    contextRow
  }: {
    field?: string;
    whatLabel?: string;
    proposalBody?: string | null;
    proposedBy?: string;
    proposalCreatedAt?: number | null;
    proposalRevision?: number | null;
    note?: string | null;
    newStage?: string | null;
    lifecycle?: Lifecycle | null;
    sprintItem?: { id: string; title: string } | null;
    layout?: "default" | "review";
    disabled?: boolean;
    onApprove?: (payload: Record<string, unknown>) => Promise<unknown>;
    onNoteSave?: (raw: string) => Promise<unknown>;
    actions?: Snippet;
    contextRow?: Snippet;
  } = $props();

  let draft = $state("");
  let lastApprovalKey = $state<string | null>(null);
  let ceiling = $state<string | null>(null);
  // Approving is a ceiling-setting moment, so it offers the same choices as any other.
  // This used to send `owner` no matter what the screen showed, which meant a proposal
  // approved in the browser could only ever stay with Khushal.
  let holder = $state<Principal>(OWNER_HOLDER);
  let inFlight = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);
  let reviewLayout = $derived(layout === "review");
  let hasNote = $derived(Boolean(onNoteSave) || Boolean((note || "").trim()));
  let contentTitle = $derived(whatLabel || fieldLabelFor(lifecycle, field));
  let preferredCeiling = $derived(preferredScopeCeilingFor(lifecycle, newStage));
  // Query refetches rebuild the lifecycle object. The reset boundary uses only the
  // proposal's stored identity and the primitive values that change valid approval
  // scope, so an unrelated refresh cannot erase an edit or an explicit picker choice.
  let approvalContextKey = $derived(
    lifecycle && newStage && preferredCeiling
      ? [
          lifecycle.workerType,
          newStage,
          lifecycle.stageOrder.join(","),
          lifecycle.ceilingRange.join(","),
          sprintItem?.id || ""
        ].join("|")
      : null
  );

  let actionDisabled = $derived(
    disabled || inFlight || resolved || approvalContextKey === null || ceiling === null
  );

  // The edit stays here until it is approved. A proposal has two outcomes, approve or
  // reject, so an edited proposal is approved as the edit — it is not saved back over the
  // author's text and left pending.
  async function saveDraft(raw: string): Promise<void> {
    draft = raw;
  }

  function resetDraft(): string {
    draft = proposalBody || "";
    return draft;
  }

  async function approve(): Promise<void> {
    if (actionDisabled) return;
    const ceilingForApproval = ceiling;
    const holderForApproval = holder;
    if (!approvalContextKey || !ceilingForApproval) return;
    inFlight = true;
    error = null;
    try {
      const payload: Record<string, unknown> = {};
      if (draft !== (proposalBody || "")) {
        payload.edited_body = draft;
      }
      payload.next_ceiling = ceilingForApproval;
      payload.next_holder = holderForApproval;
      await onApprove?.(payload);
      resolved = true;
    } catch (err) {
      error = err;
    } finally {
      inFlight = false;
    }
  }

  $effect(() => {
    if (proposalCreatedAt === null || !approvalContextKey || !preferredCeiling) return;
    const nextApprovalKey = `${field}:${proposalCreatedAt}:${proposalRevision ?? ""}:${approvalContextKey}`;
    if (nextApprovalKey !== lastApprovalKey) {
      lastApprovalKey = nextApprovalKey;
      draft = proposalBody || "";
      ceiling = preferredCeiling;
      holder = OWNER_HOLDER;
      resolved = false;
      error = null;
    }
  });
</script>

{#snippet actionGroup(defaultLayout: boolean)}
  <div class="approval-actions" class:approval-actions--split={defaultLayout && Boolean(actions)}>
    {#if error}<ErrorLine {error} />{/if}
    {#if defaultLayout && actions}
      <div class="approval-actions-left">{@render actions()}</div>
    {/if}
    <div class="approval-control-group">
      <Button
        variant="primary"
        data-accept=""
        disabled={actionDisabled}
        onclick={() => void approve()}
      >
        Approve
      </Button>
      <CeilingPicker {newStage} {lifecycle} {sprintItem} bind:ceiling bind:holder />
    </div>
  </div>
{/snippet}

<div class="approval {reviewLayout ? 'approval--review' : ''}" data-approval-block data-mode="pending" data-field={field || undefined}>
    {#if !reviewLayout}
      <div class="approval-what">{contentTitle}</div>
    {/if}

    {#if proposedBy && !reviewLayout}
      <div class="proposal-meta">proposed by {proposedBy}</div>
    {/if}

    <div class="approval-proposal-shell">
      {#if reviewLayout}
        <!-- The ask header is a static row — an uppercase field label and, when
             known, the quiet byline — with the proposal always visible below it.
             No collapse; the ask is the one thing on screen. -->
        <div class="approval-what" data-content-section="proposal">
          <span class="approval-what-label">{contentTitle}</span>
          {#if proposedBy}<span class="approval-what-by">proposed by {proposedBy}</span>{/if}
        </div>
        <div class="approval-draft">
          {#key lastApprovalKey}
            <InlineEdit
              value={draft}
              markdown
              multiline
              placeholder={`${contentTitle || "Proposal"}...`}
              dataEdit
              onCancel={resetDraft}
              onSave={saveDraft}
            />
          {/key}
        </div>
        {#if contextRow}
          <div class="approval-context-row" data-approval-context-row>{@render contextRow()}</div>
        {/if}
        {@render actionGroup(false)}
      {:else}
        <Disclosure title={contentTitle} variant="content" defaultOpen={true} data-content-section="proposal">
          <div class="approval-draft">
            {#key lastApprovalKey}
              <InlineEdit
                value={draft}
                markdown
                multiline
                placeholder={`${contentTitle || "Proposal"}...`}
                dataEdit
                onCancel={resetDraft}
                onSave={saveDraft}
              />
            {/key}
          </div>
        </Disclosure>
        {#if contextRow}
          <div class="approval-context-row" data-approval-context-row>{@render contextRow()}</div>
        {/if}
      {/if}
    </div>

    {#if hasNote && !reviewLayout && onNoteSave}
      <Disclosure title="Notes" variant="support" defaultOpen={Boolean((note || "").trim())} data-content-section="note">
        <InlineEdit
          value={note}
          markdown
          multiline
          placeholder="Note..."
          onSave={onNoteSave}
        />
      </Disclosure>
    {/if}

    {#if !reviewLayout}
      {@render actionGroup(true)}
    {/if}
</div>
