<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import {
    feedbackRelativeTime,
    feedbackPageHref,
    feedbackTicketStageState,
    feedbackTicketStateLabel,
    type FeedbackNote
  } from "../lib/feedback";
  import Button from "../components/Button.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import ScreenHeader from "../components/ScreenHeader.svelte";
  import SegmentedControl from "../components/SegmentedControl.svelte";
  import StageMark from "../components/StageMark.svelte";

  let { onDismissed }: { onDismissed: (noteId: string) => void } = $props();
  const feedback = createQuery(() => queries.feedback());
  const tabs = [
    { value: "open", label: "Open" },
    { value: "handled", label: "Handled" }
  ];
  let tab = $state<string | null>("open");
  let actionError = $state<unknown>(null);
  let actionNoteId = $state<string | null>(null);
  let handledCount = $derived(
    (feedback.data?.handled_groups || []).reduce((count, group) => count + group.notes.length, 0)
  );
  let handledGroups = $derived([
    ...(feedback.data?.handled_groups || []).filter((group) => group.ticket !== null),
    ...(feedback.data?.handled_groups || []).filter((group) => group.ticket === null)
  ]);

  async function dismiss(noteId: string): Promise<void> {
    actionNoteId = noteId;
    actionError = null;
    try {
      await mutateJson(`/api/feedback/${encodeURIComponent(noteId)}/dismiss`, { method: "POST" });
      onDismissed(noteId);
    } catch (caught) {
      actionError = caught;
    } finally {
      actionNoteId = null;
    }
  }

  async function reopen(noteId: string): Promise<void> {
    actionNoteId = noteId;
    actionError = null;
    try {
      await mutateJson(`/api/feedback/${encodeURIComponent(noteId)}/reopen`, { method: "POST" });
    } catch (caught) {
      actionError = caught;
    } finally {
      actionNoteId = null;
    }
  }
</script>

{#snippet noteMeta(note: FeedbackNote)}
  {@const pageHref = feedbackPageHref(note.page_address)}
  <div class="fb-meta">
    {#if pageHref && note.page_label}
      <a href={pageHref} title={`Go to ${note.page_label}`}>{note.page_label}</a>
      <span aria-hidden="true">·</span>
    {/if}
    <span class="fb-when">{feedbackRelativeTime(note.created_at)}</span>
  </div>
{/snippet}

{#snippet noteRow(note: FeedbackNote, action: "dismiss" | "reopen")}
  <article class="fb-note" data-feedback-note={note.id} data-feedback-state={note.state}>
    <p class="fb-text">{note.text}</p>
    <div class="fb-row">
      {@render noteMeta(note)}
      <Button
        variant="pill"
        disabled={actionNoteId === note.id}
        data-feedback-action={action}
        onclick={() => action === "dismiss" ? void dismiss(note.id) : void reopen(note.id)}
      >{action === "dismiss" ? "Dismiss" : "Reopen"}</Button>
    </div>
  </article>
{/snippet}

<section class="ideas-screen feedback-screen" data-screen="feedback">
  <div class="doc">
    <ScreenHeader title="Feedback">
      {#snippet meta()}
        <SegmentedControl name="feedback-view" options={tabs} bind:value={tab}>
          {#snippet optionContent(option)}
            {option.label} {option.value === "open" ? feedback.data?.open_count ?? "" : feedback.data ? handledCount : ""}
          {/snippet}
        </SegmentedControl>
      {/snippet}
    </ScreenHeader>
    <div class="col">
      <ResourceState error={feedback.error} loading={feedback.isFetching} hasData={Boolean(feedback.data)} loadingText="Loading feedback...">
        {#if actionError}<ErrorLine error={actionError} />{/if}
        {#if tab === "open"}
          {#if feedback.data?.open.length}
            <p class="fb-lede">The feedback agent reads these, groups them into tickets and does the work.</p>
            <div class="fb-list" data-feedback-open>
              {#each feedback.data.open as note (note.id)}
                {@render noteRow(note, "dismiss")}
              {/each}
            </div>
          {:else}
            <div class="fb-empty" data-feedback-empty="open">
              Nothing waiting.<br />Tap
              <span class="fb-inline-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M20.5 12a8.5 8.5 0 0 1-12.3 7.6L3.5 20.5l1-4.4A8.5 8.5 0 1 1 20.5 12z"></path><path d="M12 8.5v7M8.5 12h7"></path></svg></span>
              on any page when something bugs you.
            </div>
          {/if}
        {:else if handledGroups.length}
          <div data-feedback-handled>
            {#each handledGroups as group, index (group.ticket?.id || `dismissed-${index}`)}
              <section class="fb-group" data-feedback-group={group.ticket?.id || "dismissed"}>
                <div class="fb-group-head">
                  {#if group.ticket}
                    <StageMark state={feedbackTicketStageState(group.ticket)} />
                    <a href={`#/workspace/${encodeURIComponent(group.ticket.id)}`}>{group.ticket.title}</a>
                    <span class="fb-group-state">{feedbackTicketStateLabel(group.ticket)} · {group.notes.length} {group.notes.length === 1 ? "note" : "notes"}</span>
                  {:else}
                    <span class="fb-group-label">Dismissed</span>
                  {/if}
                </div>
                {#each group.notes as note (note.id)}
                  {@render noteRow(note, "reopen")}
                {/each}
              </section>
            {/each}
          </div>
        {:else}
          <div class="fb-empty" data-feedback-empty="handled">Nothing handled yet.</div>
        {/if}
      </ResourceState>
    </div>
  </div>
</section>
