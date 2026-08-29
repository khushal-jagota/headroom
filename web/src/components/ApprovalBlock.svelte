<script lang="ts">
  import { type Snippet } from "svelte";
  import Button from "./Button.svelte";
  import Disclosure from "./Disclosure.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import ScopePairPicker from "./ScopePairPicker.svelte";
  import { labelize } from "../lib/ui";
  import type { Lifecycle } from "../lib/lifecycle";
  import type { AtCap } from "../lib/types";

  type ScopePair = { next_ceiling: string; at_cap: AtCap };

  let {
    mode,
    field = "",
    whatLabel = "",
    proposalBody = "",
    proposedBy = "",
    note = "",
    newStage = null,
    suggestedNextCeiling = null,
    lifecycle = null,
    layout = "default",
    requireScope = false,
    disabled = false,
    onApprove,
    onProposalSave,
    onNoteSave,
    actions,
    contextRow
  }: {
    mode: "gating-pending" | "proposal" | "readonly";
    field?: string;
    whatLabel?: string;
    proposalBody?: string | null;
    proposedBy?: string;
    note?: string | null;
    newStage?: string | null;
    suggestedNextCeiling?: string | null;
    lifecycle?: Lifecycle | null;
    layout?: "default" | "review";
    requireScope?: boolean;
    disabled?: boolean;
    onApprove?: (payload: Record<string, unknown>) => Promise<unknown>;
    onProposalSave?: (raw: string) => Promise<unknown>;
    onNoteSave?: (raw: string) => Promise<unknown>;
    actions?: Snippet;
    contextRow?: Snippet;
  } = $props();

  let draft = $state("");
  let lastProposalBody = $state<string | null>(null);
  let scope = $state<ScopePair | null>(null);
  let inFlight = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);
  let pendingProposalSave = $state<Promise<void> | null>(null);
  let reviewLayout = $derived(layout === "review");
  let hasNote = $derived(Boolean(onNoteSave) || Boolean((note || "").trim()));
  let contentTitle = $derived(labelize(whatLabel || field.replace(/_/g, " ")));

  let scopeRequired = $derived(
    mode === "gating-pending" || (mode === "proposal" && requireScope)
  );
  let showScope = $derived(
    mode === "gating-pending" || (mode === "proposal" && requireScope)
  );
  let actionLabel = $derived(mode === "proposal" ? "Accept" : "Approve");
  let actionDisabled = $derived(
    disabled || inFlight || resolved || (scopeRequired && scope === null)
  );

  async function saveDraft(raw: string): Promise<void> {
    const save = (async () => {
      await onProposalSave?.(raw);
      draft = raw;
    })();
    pendingProposalSave = save;
    try {
      await save;
    } finally {
      if (pendingProposalSave === save) pendingProposalSave = null;
    }
  }

  function resetDraft(): string {
    draft = proposalBody || "";
    return draft;
  }

  async function approve(): Promise<void> {
    const proposalSave = pendingProposalSave;
    const scopeForApproval = scope;
    inFlight = true;
    error = null;
    try {
      await proposalSave;
      const payload: Record<string, unknown> = {};
      if (mode === "gating-pending" || mode === "proposal") {
        if (proposalSave === null && draft !== (proposalBody || "")) {
          payload.edited_body = draft;
        }
      }
      if (scopeRequired) {
        if (!scopeForApproval) return;
        payload.next_ceiling = scopeForApproval.next_ceiling;
        payload.at_cap = scopeForApproval.at_cap;
      }
      await onApprove?.(payload);
      resolved = true;
    } catch (err) {
      error = err;
    } finally {
      inFlight = false;
    }
  }

  $effect(() => {
    const incoming = proposalBody || "";
    if (incoming !== lastProposalBody) {
      lastProposalBody = incoming;
      draft = incoming;
      scope = null;
      resolved = false;
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
        {actionLabel}
      </Button>
      {#if showScope}<ScopePairPicker {newStage} {suggestedNextCeiling} {lifecycle} bind:scope />{/if}
    </div>
  </div>
{/snippet}

<div class="approval {reviewLayout ? 'approval--review' : ''}" data-approval-block data-mode={mode} data-field={field || undefined}>
  {#if mode === "readonly"}
    {#if proposedBy}<div class="proposal-meta">proposed by {proposedBy}</div>{/if}
    <MarkdownBlock text={proposalBody} />
  {:else}
    {#if !reviewLayout}
      <div class="approval-what">{whatLabel || field.replace(/_/g, " ")}</div>
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
          <InlineEdit
            value={draft}
            markdown
            multiline
            placeholder={`${contentTitle || "Proposal"}...`}
            dataEdit
            onCancel={mode === "gating-pending" ? resetDraft : undefined}
            onSave={saveDraft}
          />
        </div>
        {#if contextRow}
          <div class="approval-context-row" data-approval-context-row>{@render contextRow()}</div>
        {/if}
        {@render actionGroup(false)}
      {:else}
        <Disclosure title={contentTitle} variant="content" defaultOpen={true} data-content-section="proposal">
          <div class="approval-draft">
            <InlineEdit
              value={draft}
              markdown
              multiline
              placeholder={`${contentTitle || "Proposal"}...`}
              dataEdit
              onCancel={mode === "gating-pending" ? resetDraft : undefined}
              onSave={saveDraft}
            />
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
  {/if}
</div>
