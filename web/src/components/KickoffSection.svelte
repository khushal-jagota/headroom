<script lang="ts">
  import Button from "./Button.svelte";
  import Disclosure from "./Disclosure.svelte";
  import InlineEdit from "./InlineEdit.svelte";

  let {
    title,
    kickoffNote,
    proposal = null,
    variant = "ticket",
    onAccept,
    onSaveNote
  }: {
    title: string;
    kickoffNote: string;
    proposal?: {
      title: string;
      kickoff_note: string;
      proposed_by: string;
      created_at?: number;
    } | null;
    variant?: "ticket" | "review";
    onAccept?: (payload: Record<string, unknown>) => Promise<unknown>;
    onSaveNote?: (raw: string) => Promise<unknown>;
  } = $props();

  let titleDraft = $state("");
  let noteDraft = $state("");
  let busy = $state(false);

  $effect(() => {
    titleDraft = proposal?.title ?? title;
    noteDraft = proposal?.kickoff_note ?? kickoffNote;
  });

  function keepTitle(raw: string): Promise<unknown> {
    titleDraft = raw;
    return Promise.resolve();
  }

  function keepNote(raw: string): Promise<unknown> {
    noteDraft = raw;
    return Promise.resolve();
  }

  async function approve(): Promise<void> {
    if (!onAccept || busy) return;
    busy = true;
    try {
      await onAccept({ edited_title: titleDraft, edited_kickoff_note: noteDraft });
    } finally {
      busy = false;
    }
  }
</script>

{#snippet pendingBody()}
  <div class="kickoff-proposal" data-kickoff-proposal>
    <div class="review-context-label">Proposed title</div>
    <InlineEdit value={titleDraft} placeholder="Untitled" onSave={keepTitle} />
    <div class="review-context-label">Kickoff note</div>
    <InlineEdit value={noteDraft} markdown multiline placeholder="Ticket premise and boundaries..." onSave={keepNote} />
    <div class="review-card-actions">
      <Button data-accept="" disabled={busy} onclick={() => void approve()}>
        {busy ? "Approving…" : "Approve Kickoff"}
      </Button>
    </div>
  </div>
{/snippet}

{#if variant === "review"}
  <div class="ticket-stage-section ticket-stage-section--review" data-kickoff>
    {@render pendingBody()}
  </div>
{:else}
  <Disclosure variant="stage" defaultOpen={Boolean(proposal)} data-kickoff data-stage-state={proposal ? "current-awaiting-approval" : "completed"}>
    {#snippet summary()}
      <span class="stage-mark" class:stage-mark--blank={!proposal}></span>
      <span class="disclosure-stage-name">Kickoff</span>
    {/snippet}
    {#if proposal}
      {@render pendingBody()}
    {:else}
      <InlineEdit
        value={kickoffNote}
        markdown
        multiline
        placeholder="Ticket premise and boundaries..."
        onSave={(raw) => onSaveNote ? onSaveNote(raw) : Promise.resolve()}
      />
    {/if}
  </Disclosure>
{/if}
