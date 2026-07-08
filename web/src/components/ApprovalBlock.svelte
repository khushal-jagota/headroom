<script lang="ts">
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
    onApprove,
    onNoteSave,
    onValueSave
  }: {
    mode: "gating-pending" | "needs_review";
    field?: string;
    whatLabel?: string;
    proposalBody?: string | null;
    proposedBy?: string;
    note?: string | null;
    newState?: string | null;
    onApprove: (payload: Record<string, unknown>) => Promise<unknown>;
    onNoteSave?: (raw: string) => Promise<unknown>;
    onValueSave?: (raw: string) => Promise<unknown>;
  } = $props();

  let draft = $state(proposalBody || "");
  let scope = $state<ScopePair | null>(null);
  let inFlight = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);

  async function approve(): Promise<void> {
    const payload: Record<string, unknown> = {};
    if (mode === "gating-pending") {
      if (!scope) return;
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
</script>

<div class="approval" data-approval-block data-mode={mode} data-field={field || undefined}>
  <div class="approval-what">{whatLabel || field.replace(/_/g, " ")}</div>

  {#if mode === "needs_review"}
    <div class="approval-result">
      {#if onValueSave}
        <InlineEdit value={proposalBody} markdown multiline placeholder="Result..." onSave={onValueSave} />
      {:else}
        <MarkdownBlock text={proposalBody} />
      {/if}
    </div>
    {#if onNoteSave}
      <div class="note">
        <div class="note-head">Review notes</div>
        <div class="note-body">
          <InlineEdit
            value={note}
            markdown
            multiline
            placeholder="Things to check before you approve the result..."
            onSave={onNoteSave}
          />
        </div>
      </div>
    {/if}
    <div class="approval-actions">
      {#if error}<ErrorLine {error} />{/if}
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
  {:else}
    {#if proposedBy}
      <div class="proposal-meta">proposed by {proposedBy}</div>
    {/if}
    <div class="ed approval-draft" data-edit contenteditable="true" bind:textContent={draft}></div>
    {#if onNoteSave}
      <div class="note">
        <div class="note-head">Note</div>
        <div class="note-body">
          <InlineEdit value={note} markdown multiline placeholder="Note..." onSave={onNoteSave} />
        </div>
      </div>
    {/if}
    <div class="approval-actions">
      {#if error}<ErrorLine {error} />{/if}
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
  {/if}
</div>
