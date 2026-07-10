<script lang="ts">
  import { type Snippet } from "svelte";
  import Button from "./Button.svelte";
  import Disclosure from "./Disclosure.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import ScopePairPicker from "./ScopePairPicker.svelte";
  import { labelize } from "../lib/ui";

  type ScopePair = { next_ceiling: string; at_cap: string };

  let {
    mode,
    field = "",
    whatLabel = "",
    proposalBody = "",
    proposedBy = "",
    note = "",
    newState = null,
    layout = "default",
    requireScope = false,
    onApprove,
    onNoteSave,
    onValueSave,
    actions
  }: {
    mode: "gating-pending" | "needs_review" | "proposal" | "readonly";
    field?: string;
    whatLabel?: string;
    proposalBody?: string | null;
    proposedBy?: string;
    note?: string | null;
    newState?: string | null;
    layout?: "default" | "review";
    requireScope?: boolean;
    onApprove?: (payload: Record<string, unknown>) => Promise<unknown>;
    onNoteSave?: (raw: string) => Promise<unknown>;
    onValueSave?: (raw: string) => Promise<unknown>;
    actions?: Snippet;
  } = $props();

  let draft = $state("");
  let lastProposalBody = $state<string | null>(null);
  let scope = $state<ScopePair | null>(null);
  let inFlight = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);
  let reviewLayout = $derived(layout === "review");
  let hasNote = $derived(Boolean(onNoteSave) || Boolean((note || "").trim()));
  let contentTitle = $derived(labelize(whatLabel || field.replace(/_/g, " ")));

  // gating-pending always requires a scope; proposal requires one only when asked to.
  let scopeRequired = $derived(mode === "gating-pending" || (mode === "proposal" && requireScope));
  let showScope = $derived(mode === "gating-pending" || (mode === "proposal" && requireScope));
  let acceptAttr = $derived(mode === "needs_review" ? "approve" : "accept");
  let actionLabel = $derived(mode === "proposal" ? "Accept" : "Approve");
  let actionDisabled = $derived(inFlight || resolved || (scopeRequired && scope === null));

  async function saveDraft(raw: string): Promise<void> {
    draft = raw;
  }

  function resetDraft(): string {
    draft = proposalBody || "";
    return draft;
  }

  async function approve(): Promise<void> {
    const payload: Record<string, unknown> = {};
    if (mode === "gating-pending" || mode === "proposal") {
      if (draft !== (proposalBody || "")) payload.edited_body = draft;
    }
    if (scopeRequired) {
      if (!scope) return;
      payload.next_ceiling = scope.next_ceiling;
      payload.at_cap = scope.at_cap;
    }
    inFlight = true;
    error = null;
    try {
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
        {...{ [`data-${acceptAttr}`]: "" }}
        disabled={actionDisabled}
        onclick={() => void approve()}
      >
        {actionLabel}
      </Button>
      {#if showScope}<ScopePairPicker {newState} bind:scope />{/if}
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

    {#if mode !== "needs_review" && proposedBy && !reviewLayout}
      <div class="proposal-meta">proposed by {proposedBy}</div>
    {/if}

    <div class="approval-proposal-shell">
      <Disclosure title={contentTitle} variant="content" defaultOpen={true} data-content-section="proposal">
        {#if mode === "needs_review"}
          <div class="approval-result">
            {#if onValueSave}
              <InlineEdit value={proposalBody} markdown multiline placeholder="Result..." onSave={onValueSave} />
            {:else}
              <MarkdownBlock text={proposalBody} />
            {/if}
          </div>
        {:else}
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
        {/if}
      </Disclosure>
      {#if reviewLayout}
        {@render actionGroup(false)}
      {/if}
    </div>

    {#if hasNote && !reviewLayout && onNoteSave}
      <Disclosure title="Notes" variant="support" defaultOpen={Boolean((note || "").trim())} data-content-section="note">
        <InlineEdit
          value={note}
          markdown
          multiline
          placeholder={mode === "needs_review" ? "Things to check before you approve the result..." : "Note..."}
          onSave={onNoteSave}
        />
      </Disclosure>
    {/if}

    {#if !reviewLayout}
      {@render actionGroup(true)}
    {/if}
  {/if}
</div>
