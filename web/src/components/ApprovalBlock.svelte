<script lang="ts">
  import { type Snippet } from "svelte";
  import {
    handlePlainTextPaste,
    paintMarkdownEditable,
    readMarkdownEditable,
    refreshEditableEmptyState
  } from "../lib/markdownEdit";
  import ContentDisclosure from "./ContentDisclosure.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import ScopePairPicker from "./ScopePairPicker.svelte";

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
    onApprove,
    onNoteSave,
    onValueSave,
    actions
  }: {
    mode: "gating-pending" | "needs_review";
    field?: string;
    whatLabel?: string;
    proposalBody?: string | null;
    proposedBy?: string;
    note?: string | null;
    newState?: string | null;
    layout?: "default" | "review";
    onApprove: (payload: Record<string, unknown>) => Promise<unknown>;
    onNoteSave?: (raw: string) => Promise<unknown>;
    onValueSave?: (raw: string) => Promise<unknown>;
    actions?: Snippet;
  } = $props();

  let draft = $state("");
  let draftEl = $state<HTMLDivElement | null>(null);
  let draftEditing = $state(false);
  let lastProposalBody = $state<string | null>(null);
  let scope = $state<ScopePair | null>(null);
  let inFlight = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);
  let reviewLayout = $derived(layout === "review");
  let hasNote = $derived(Boolean(onNoteSave) || Boolean((note || "").trim()));
  let contentTitle = $derived(displayLabel(whatLabel || field.replace(/_/g, " ")));

  function displayLabel(value: string): string {
    const trimmed = value.trim();
    if (!trimmed) return "";
    return trimmed[0].toUpperCase() + trimmed.slice(1);
  }

  function paintDraft(): void {
    const el = draftEl;
    if (!el) return;
    paintMarkdownEditable(el, draft);
  }

  function enterDraftEdit(): void {
    const el = draftEl;
    if (!el || draftEditing || inFlight) return;
    draftEditing = true;
  }

  function commitDraft(): void {
    const el = draftEl;
    if (!el || !draftEditing) return;
    draft = readMarkdownEditable(el);
    draftEditing = false;
    paintDraft();
  }

  function draftKeydown(event: KeyboardEvent): void {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      draftEl?.blur();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      draft = proposalBody || "";
      draftEditing = false;
      paintDraft();
      draftEl?.blur();
    }
  }

  async function approve(): Promise<void> {
    const payload: Record<string, unknown> = {};
    if (mode === "gating-pending") {
      if (!scope) return;
      if (draftEditing) commitDraft();
      payload.next_ceiling = scope.next_ceiling;
      payload.at_cap = scope.at_cap;
      if (draft !== (proposalBody || "")) payload.edited_body = draft;
    }
    inFlight = true;
    error = null;
    try {
      await onApprove(payload);
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
    if (!draftEditing) paintDraft();
  });
</script>

<div class="approval {reviewLayout ? 'approval--review' : ''}" data-approval-block data-mode={mode} data-field={field || undefined}>
  {#if !reviewLayout}
    <div class="approval-what">{whatLabel || field.replace(/_/g, " ")}</div>
  {/if}

  {#if mode === "needs_review"}
    <div class="approval-proposal-shell">
      <ContentDisclosure title={contentTitle} section="proposal">
        <div class="approval-result">
          {#if onValueSave}
            <InlineEdit value={proposalBody} markdown multiline placeholder="Result..." onSave={onValueSave} />
          {:else}
            <MarkdownBlock text={proposalBody} />
          {/if}
        </div>
      </ContentDisclosure>
      {#if reviewLayout}
        <div class="approval-actions">
          {#if error}<ErrorLine {error} />{/if}
          <div class="approval-control-group">
            <button
              type="button"
              class="approval-approve"
              data-approve
              disabled={inFlight || resolved}
              onclick={() => void approve()}
            >
              Approve
            </button>
          </div>
        </div>
      {/if}
    </div>
    {#if hasNote}
      {#if !reviewLayout && onNoteSave}
        <ContentDisclosure title="Notes" defaultOpen={Boolean((note || "").trim())} tone="support" section="note">
          <InlineEdit
            value={note}
            markdown
            multiline
            placeholder="Things to check before you approve the result..."
            onSave={onNoteSave}
          />
        </ContentDisclosure>
      {/if}
    {/if}
    {#if !reviewLayout}
      <div class="approval-actions" class:approval-actions--split={Boolean(actions)}>
        {#if error}<ErrorLine {error} />{/if}
        {#if actions}
          <div class="approval-actions-left">{@render actions()}</div>
        {/if}
        <div class="approval-control-group">
          <button
            type="button"
            class="approval-approve"
            data-approve
            disabled={inFlight || resolved}
            onclick={() => void approve()}
          >
            Approve
          </button>
        </div>
      </div>
    {/if}
  {:else}
    {#if proposedBy && !reviewLayout}
      <div class="proposal-meta">proposed by {proposedBy}</div>
    {/if}
    <div class="approval-proposal-shell">
      <ContentDisclosure title={contentTitle} section="proposal">
        <div
          bind:this={draftEl}
          class="ed approval-draft"
          data-edit
          contenteditable="true"
          role="textbox"
          aria-multiline="true"
          tabindex="0"
          onfocus={enterDraftEdit}
          onblur={commitDraft}
          onkeydown={draftKeydown}
          oninput={() => {
            if (draftEl) refreshEditableEmptyState(draftEl, true);
          }}
          onpaste={handlePlainTextPaste}
        ></div>
      </ContentDisclosure>
      {#if reviewLayout}
        <div class="approval-actions">
          {#if error}<ErrorLine {error} />{/if}
          <div class="approval-control-group">
            <button
              type="button"
              class="approval-approve"
              data-accept
              disabled={inFlight || resolved || scope === null}
              onclick={() => void approve()}
            >
              Approve
            </button>
            <ScopePairPicker {newState} bind:scope />
          </div>
        </div>
      {/if}
    </div>
    {#if hasNote}
      {#if !reviewLayout && onNoteSave}
        <ContentDisclosure title="Notes" defaultOpen={Boolean((note || "").trim())} tone="support" section="note">
          <InlineEdit value={note} markdown multiline placeholder="Note..." onSave={onNoteSave} />
        </ContentDisclosure>
      {/if}
    {/if}
    {#if !reviewLayout}
      <div class="approval-actions" class:approval-actions--split={Boolean(actions)}>
        {#if error}<ErrorLine {error} />{/if}
        {#if actions}
          <div class="approval-actions-left">{@render actions()}</div>
        {/if}
        <div class="approval-control-group">
          <button
            type="button"
            class="approval-approve"
            data-accept
            disabled={inFlight || resolved || scope === null}
            onclick={() => void approve()}
          >
            Approve
          </button>
          <ScopePairPicker {newState} bind:scope />
        </div>
      </div>
    {/if}
  {/if}
</div>
