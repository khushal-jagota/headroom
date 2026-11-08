<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import ComposerField from "../components/conversation/ComposerField.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import ListRow from "../components/ListRow.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import ManagerHeader from "../components/ManagerHeader.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";
  import SegmentedControl from "../components/SegmentedControl.svelte";

  const ideas = createQuery(() => queries.ideas());
  const projects = createQuery(() => queries.projects());

  let projectOptions = $derived([
    { value: null, label: "None" },
    ...(projects.data?.projects || []).map((entry) => ({ value: entry.id, label: entry.name }))
  ]);
  let title = $state("");
  let detail = $state("");
  let project = $state<string | null>(null);
  let createError = $state<unknown>(null);
  let creating = $state(false);

  async function capture(): Promise<void> {
    if (!title.trim()) return;
    creating = true;
    createError = null;
    const payload: Record<string, unknown> = { title: title.trim() };
    if (detail.trim()) payload.body = detail;
    if (project) payload.project_id = project;
    try {
      await mutateJson("/api/ideas", { method: "POST", body: payload });
      title = "";
      detail = "";
      project = null;
      creating = false;
    } catch (err) {
      createError = err;
      creating = false;
    }
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void capture();
    } else if (event.key === "Enter") {
      event.preventDefault();
    }
  }

  // The title was a one-line text input, which drops line breaks from whatever is pasted.
  $effect(() => {
    if (/[\r\n]/.test(title)) title = title.replace(/[\r\n]+/g, "");
  });
</script>

<section class="ideas-screen" data-screen="ideas">
  <div class="doc">
    <ManagerHeader>
      {#snippet title()}Ideas{/snippet}
      {#snippet actions()}<span class="idea-count">{ideas.data?.ideas?.length || 0} captured</span>{/snippet}
    </ManagerHeader>
    <div class="col">
      <section class="capture" data-create="idea">
        {#if createError}<ErrorLine error={createError} />{/if}
        <!-- The composer's field (ComposerField), twice: the idea, which stays one line of words
             as the text input it was (Enter captures; a line break never lands in it), and its
             detail. -->
        <ComposerField
          bind:value={title}
          placeholder="Capture an idea..."
          inputAttributes={{ "data-input": "title" }}
          onkeydown={keydown}
        />
        <ComposerField
          bind:value={detail}
          placeholder="Add detail — optional"
          inputAttributes={{ "data-input": "body" }}
        />
        <div class="foot">
          <SegmentedControl name="project" options={projectOptions} bind:value={project} />
          <div class="spacer"></div>
          <span class="hint"><kbd>↩</kbd> to capture</span>
          <Button
            variant="primary"
            data-commit=""
            disabled={creating || !title.trim()}
            onclick={() => void capture()}
          >
            Capture
          </Button>
        </div>
      </section>
      <div class="list" data-ideas>
        <ResourceState shape="rows" error={ideas.error} loading={ideas.isFetching} hasData={Boolean(ideas.data)} loadingText="Loading ideas...">
        {#if !(ideas.data?.ideas || []).length}
          <div class="quiet-line">No ideas yet.</div>
        {:else}
          <SectionHeading label="Captured" />
          {#each ideas.data?.ideas || [] as idea}
            {@const hasBody = idea.body !== null && idea.body !== undefined && String(idea.body).trim() !== ""}
            {#if hasBody}
              <Disclosure variant="idea" chevron="leading" data-idea-id={idea.id}>
                {#snippet summary()}
                  <span class="it list-row-title">{idea.title}</span>
                  {#if idea.project}<Chip variant="project" value={idea.project} />{/if}
                {/snippet}
                <MarkdownBlock text={idea.body} />
              </Disclosure>
            {:else}
              <ListRow variant="idea" title={idea.title} data-idea-id={idea.id}>
                {#snippet leading()}<span class="chev"></span>{/snippet}
                {#snippet trailing()}
                  {#if idea.project}<Chip variant="project" value={idea.project} />{/if}
                {/snippet}
              </ListRow>
            {/if}
          {/each}
        {/if}
        </ResourceState>
      </div>
    </div>
  </div>
</section>

<style>
  .ideas-screen { padding-top:var(--page-top); width:100%; max-width:var(--page-measure); margin-inline:auto; }
  .idea-count { color:var(--tone-tertiary); font-size:var(--type-small); }
</style>
