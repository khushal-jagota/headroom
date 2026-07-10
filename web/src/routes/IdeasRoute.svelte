<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson, resource } from "../lib/resources";
  import type { IdeasResponse, ProjectsResponse } from "../lib/types";
  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import ListRow from "../components/ListRow.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import Pill from "../components/Pill.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import ScreenHeader from "../components/ScreenHeader.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";
  import SegmentedControl from "../components/SegmentedControl.svelte";

  const ideas = resource<IdeasResponse>("ideas", (signal) =>
    fetchJson("/api/ideas", { signal })
  );
  const projects = resource<ProjectsResponse>("projects", (signal) =>
    fetchJson("/api/projects", { signal })
  );

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
      await mutateJson("/api/ideas", { method: "POST", body: payload }, ["ideas"]);
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
    }
  }

  onDestroy(() => {
    ideas.dispose();
    projects.dispose();
  });
</script>

<section class="ideas-screen" data-screen="ideas">
  <div class="doc">
    <ScreenHeader title="Ideas">
      {#snippet meta()}
        <Pill>{ideas.data?.ideas?.length || 0}</Pill>
      {/snippet}
    </ScreenHeader>
    <div class="col">
      <section class="capture" data-create="idea">
        {#if createError}<ErrorLine error={createError} />{/if}
        <input
          class="in title-in"
          type="text"
          placeholder="Capture an idea..."
          data-input="title"
          bind:value={title}
          onkeydown={keydown}
        />
        <textarea
          class="in detail-in"
          rows="2"
          placeholder="Add detail — optional"
          data-input="body"
          bind:value={detail}
        ></textarea>
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
        <ResourceState error={ideas.error} loading={ideas.loading} hasData={Boolean(ideas.data)} loadingText="Loading ideas...">
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
