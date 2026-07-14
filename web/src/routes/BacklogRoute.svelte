<script lang="ts">
  import { onDestroy } from "svelte";
  import {
    mutateJsonWithResourceEffect,
    resourceCatalogue
  } from "../lib/resourceCatalogue";
  import { PRIORITY_ORDER } from "../lib/ui";
  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import ListRow from "../components/ListRow.svelte";
  import Pill from "../components/Pill.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import ScreenHeader from "../components/ScreenHeader.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";
  import SegmentedControl from "../components/SegmentedControl.svelte";

  const backlog = resourceCatalogue.backlogSprintItems();
  const projects = resourceCatalogue.projects();

  const priorityOptions = PRIORITY_ORDER.map((value) => ({ value, label: value }));

  let title = $state("");
  let project = $state<string | null>(null);
  let priority = $state<string | null>("P3");
  let deadline = $state("");
  let body = $state("");
  let createError = $state<unknown>(null);
  let creating = $state(false);

  let groups = $derived.by(() => {
    const byPriority: Record<string, any[]> = {};
    for (const p of PRIORITY_ORDER) byPriority[p] = [];
    for (const item of backlog.data?.items || []) {
      (byPriority[item.priority] || (byPriority[item.priority] = [])).push(item);
    }
    return byPriority;
  });
  let projectOptions = $derived(
    (projects.data?.projects || []).map((entry) => ({ value: entry.id, label: entry.name }))
  );

  $effect(() => {
    const values = projectOptions.map((option) => option.value);
    if ((!project || !values.includes(project)) && values.length) {
      project = values[0];
    }
  });

  async function createItem(): Promise<void> {
    if (!title.trim() || !project) return;
    creating = true;
    createError = null;
    const payload: Record<string, unknown> = {
      title: title.trim(),
      project_id: project,
      priority,
      body
    };
    if (deadline.trim()) payload.deadline = deadline.trim();
    try {
      await mutateJsonWithResourceEffect(
        "/api/items",
        { method: "POST", body: payload },
        { kind: "backlogSprintItemCreated" }
      );
      title = "";
      deadline = "";
      body = "";
      creating = false;
    } catch (err) {
      createError = err;
      creating = false;
    }
  }

  onDestroy(() => {
    backlog.dispose();
    projects.dispose();
  });
</script>

<section class="backlog-screen" data-screen="backlog">
  <div class="doc">
    <ScreenHeader title="Backlog">
      {#snippet meta()}
        <Pill keyLabel="unscheduled">{backlog.data?.items?.length || 0}</Pill>
      {/snippet}
    </ScreenHeader>
    <div class="col">
      <Disclosure variant="make" chevron="none" data-create="item">
        {#snippet summary()}<span class="plus">+</span> New backlog item{/snippet}
        <div class="form">
          {#if createError}<ErrorLine error={createError} />{/if}
          <input class="in title-in" type="text" placeholder="What needs doing?" data-input="title" bind:value={title} />
          <textarea class="in detail-in" rows="2" placeholder="Why it matters, any context — optional" data-input="body" bind:value={body}></textarea>
          <div class="foot">
            <SegmentedControl name="project" options={projectOptions} bind:value={project} />
            <SegmentedControl name="priority" options={priorityOptions} bind:value={priority} />
            <input class="in due-in" type="text" placeholder="due YYYY-MM-DD — optional" data-input="deadline" bind:value={deadline} />
            <div class="spacer"></div>
            <Button variant="primary" data-commit="" disabled={creating || !title.trim() || !project} onclick={() => void createItem()}>Add to backlog</Button>
          </div>
        </div>
      </Disclosure>

      <div class="groups" data-backlog-items>
        <ResourceState error={backlog.error} loading={backlog.loading} hasData={Boolean(backlog.data)} loadingText="Loading backlog...">
        {#if !(backlog.data?.items || []).length}
          <div class="quiet-line">No unscheduled items.</div>
        {:else}
          {#each PRIORITY_ORDER as p}
            {#if groups[p]?.length}
              <div class="grp" data-priority-group={p}>
                <SectionHeading label={p} count={groups[p].length} />
                {#each groups[p] as item}
                  <ListRow variant="backlog" title={item.title} href="#/backlog" data-item-id={item.id}>
                    {#snippet trailing()}
                      <span class="chips">
                        <Chip variant="project" value={item.project} />
                        {#if item.deadline}<Chip variant="deadline" value={item.deadline} />{/if}
                      </span>
                    {/snippet}
                  </ListRow>
                {/each}
              </div>
            {/if}
          {/each}
        {/if}
        </ResourceState>
      </div>
    </div>
  </div>
</section>
