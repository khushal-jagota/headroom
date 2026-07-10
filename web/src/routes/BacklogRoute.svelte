<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson, resource } from "../lib/resources";
  import { PRIORITY_ORDER } from "../lib/ui";
  import type { BacklogResponse, ProjectsResponse } from "../lib/types";
  import Chip from "../components/Chip.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import SegmentedControl from "../components/SegmentedControl.svelte";

  const backlog = resource<BacklogResponse>("items:backlog", (signal) =>
    fetchJson("/api/items?sprint_id=null", { signal })
  );
  const projects = resource<ProjectsResponse>("projects", (signal) =>
    fetchJson("/api/projects", { signal })
  );

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
      await mutateJson("/api/items", { method: "POST", body: payload }, [
        "items:backlog",
        "sprint:current",
        "board",
        "queues"
      ]);
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
    <header class="head">
      <div class="row">
        <div class="title">Backlog</div>
        <div class="meta"><span class="pill"><span class="pill-key">unscheduled</span>{backlog.data?.items?.length || 0}</span></div>
      </div>
    </header>
    <div class="col">
      <Disclosure variant="make" chevron="none" data-create="item">
        {#snippet summary()}<span class="plus">+</span> New backlog item{/snippet}
        <div class="form">
          {#if createError}<ErrorLine error={createError} />{/if}
          <div>
            <div class="fl">Title</div>
            <input class="in title-in" type="text" placeholder="What needs doing?" data-input="title" bind:value={title} />
          </div>
          <div class="two">
            <div>
              <div class="fl">Project</div>
              <SegmentedControl name="project" options={projectOptions} bind:value={project} />
            </div>
            <div>
              <div class="fl">Priority</div>
              <SegmentedControl name="priority" options={priorityOptions} bind:value={priority} />
            </div>
          </div>
          <div>
            <div class="fl">Deadline <span class="fl-opt">— optional</span></div>
            <input class="in detail-in" type="text" placeholder="YYYY-MM-DD" data-input="deadline" bind:value={deadline} />
          </div>
          <div>
            <div class="fl">Description <span class="fl-opt">— optional</span></div>
            <textarea class="in detail-in" rows="2" placeholder="Why it matters, any context. Lives on the item page." data-input="body" bind:value={body}></textarea>
          </div>
          <button class="commit" type="button" data-commit disabled={creating || !title.trim() || !project} onclick={() => void createItem()}>Add to backlog</button>
        </div>
      </Disclosure>

      <div class="groups" data-backlog-items>
        {#if backlog.error}
          <ErrorLine error={backlog.error} />
        {:else if backlog.loading && !backlog.data}
          <div class="quiet-line">Loading backlog...</div>
        {:else if !(backlog.data?.items || []).length}
          <div class="quiet-line">No unscheduled items.</div>
        {:else}
          {#each PRIORITY_ORDER as p}
            {#if groups[p]?.length}
              <div class="grp" data-priority-group={p}>
                <div class="glabel">{p} <span class="n">· {groups[p].length}</span></div>
                {#each groups[p] as item}
                  <a class="brow" href="#/backlog" data-item-id={item.id}>
                    <span class="bt entity-row-title">{item.title}</span>
                    <span class="chips">
                      <Chip variant="project" value={item.project} />
                      {#if item.deadline}<Chip variant="deadline" value={item.deadline} />{/if}
                    </span>
                  </a>
                {/each}
              </div>
            {/if}
          {/each}
        {/if}
      </div>
    </div>
  </div>
</section>
