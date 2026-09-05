<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import {
    blankScheduledTaskForm,
    scheduledTaskFormError,
    scheduledTaskFormFromSchedule,
    scheduledTaskPayload,
    type ScheduledTaskForm
  } from "../lib/scheduledTasks";
  import type {
    Priority,
    ScheduleCadence,
    SchedulePlacementMode,
    ScheduledTask
  } from "../lib/types";
  import Button from "../components/Button.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import ScreenHeader from "../components/ScreenHeader.svelte";

  const schedules = createQuery(() => queries.schedules());
  const projects = createQuery(() => queries.projects());
  const sprintItems = createQuery(() => queries.sprintItems());
  const sprintSummaries = createQuery(() => queries.sprintSummaries());
  const workerTypes = createQuery(() => queries.workerTypeManifests());

  const cadenceOptions: Array<{ value: ScheduleCadence; label: string }> = [
    { value: "every_planning_day", label: "Every planning day" },
    { value: "current_sprint_day_four", label: "Current sprint day four" },
    { value: "current_sprint_final_day", label: "Current sprint final day" }
  ];
  const placementOptions: Array<{ value: SchedulePlacementMode; label: string }> = [
    { value: "current_sprint", label: "Current sprint" },
    { value: "backlog", label: "Backlog" }
  ];
  const priorities: Priority[] = ["P0", "P1", "P2", "P3"];

  let formOpen = $state(false);
  let editingId = $state<string | null>(null);
  let draft = $state<ScheduledTaskForm>(blankScheduledTaskForm());
  let saveError = $state<unknown>(null);
  let toggleError = $state<unknown>(null);
  let formError = $state<string | null>(null);
  let saving = $state(false);
  let togglingId = $state<string | null>(null);

  let canSubmit = $derived(!saving);
  let catalogueError = $derived(
    [projects.error, sprintItems.error, workerTypes.error].find((error) => error != null)
  );

  function openCreate(): void {
    editingId = null;
    draft = blankScheduledTaskForm(workerTypes.data?.worker_types[0]?.worker_type || "coding");
    saveError = null;
    toggleError = null;
    formError = null;
    formOpen = true;
  }

  function openEdit(schedule: ScheduledTask): void {
    editingId = schedule.id;
    draft = scheduledTaskFormFromSchedule(schedule);
    saveError = null;
    toggleError = null;
    formError = null;
    formOpen = true;
  }

  function closeForm(): void {
    if (saving) return;
    formOpen = false;
    editingId = null;
    saveError = null;
    formError = null;
  }

  function selectValue(event: Event): string {
    return (event.currentTarget as HTMLSelectElement).value;
  }

  function setCadence(event: Event): void {
    draft.cadence = selectValue(event) as ScheduleCadence;
  }

  function setWorkerType(event: Event): void {
    draft.worker_type = selectValue(event);
  }

  function setPriority(event: Event): void {
    draft.priority = selectValue(event) as Priority;
  }

  function setProject(event: Event): void {
    const projectId = selectValue(event) || null;
    if (projectId !== draft.project_id) draft.sprint_item_id = null;
    draft.project_id = projectId;
  }

  function setPlacement(event: Event): void {
    draft.placement_mode = selectValue(event) as SchedulePlacementMode;
    if (draft.placement_mode === "backlog") draft.sprint_id = null;
  }

  function setSprintItem(event: Event): void {
    draft.sprint_item_id = selectValue(event) || null;
    const outcome = (sprintItems.data?.items || []).find((item) => item.id === draft.sprint_item_id);
    if (outcome) draft.project_id = outcome.project_id;
  }
  function setSprint(event: Event): void {
    draft.sprint_id = selectValue(event) || null;
  }

  function saveDraftField(field: "title" | "kickoff_note", raw: string): Promise<void> {
    draft[field] = raw;
    return Promise.resolve();
  }

  function toggleDraftEnabled(): void {
    draft.enabled = !draft.enabled;
  }

  async function toggleEnabled(schedule: ScheduledTask, event: MouseEvent): Promise<void> {
    event.stopPropagation();
    if (togglingId) return;
    togglingId = schedule.id;
    toggleError = null;
    try {
      await mutateJson(`/api/schedules/${encodeURIComponent(schedule.id)}`, {
        method: "PATCH",
        body: { enabled: !schedule.enabled }
      });
    } catch (error) {
      toggleError = error;
    } finally {
      togglingId = null;
    }
  }

  async function saveSchedule(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    formError = scheduledTaskFormError(draft);
    if (formError) return;

    saving = true;
    saveError = null;
    try {
      if (editingId) {
        await mutateJson(`/api/schedules/${encodeURIComponent(editingId)}`, {
          method: "PATCH",
          body: scheduledTaskPayload(draft)
        });
      } else {
        await mutateJson("/api/schedules", {
          method: "POST",
          body: scheduledTaskPayload(draft)
        });
      }
      formOpen = false;
      editingId = null;
      formError = null;
    } catch (error) {
      saveError = error;
    } finally {
      saving = false;
    }
  }
</script>

<section class="scheduled-tasks-screen" data-screen="scheduled-tasks">
  <div class="scheduled-tasks-page">
    {#if formOpen}
      <button class="scheduled-task-back" type="button" onclick={() => closeForm()} data-back-to-schedules>
        ‹ Scheduled tasks
      </button>

      <section class="scheduled-task-editor" data-schedule-editor>
        <div class="scheduled-task-detail-head">
          <div class="scheduled-task-title-edit" data-schedule-title>
            <InlineEdit
              className="scheduled-task-title"
              value={draft.title}
              placeholder="Name this scheduled Ticket…"
              ariaLabel="Scheduled task title"
              onSave={(raw) => saveDraftField("title", raw)}
            />
          </div>
          <button
            class:scheduled-task-switch--on={draft.enabled}
            class="scheduled-task-switch"
            type="button"
            role="switch"
            aria-checked={draft.enabled}
            aria-label={draft.enabled ? "Disable schedule" : "Enable schedule"}
            data-input="enabled"
            onclick={() => toggleDraftEnabled()}
          ></button>
        </div>

        {#if saveError}<ErrorLine error={saveError} />{/if}
        {#if catalogueError}<ErrorLine error={catalogueError} />{/if}

        <form class="scheduled-task-form" onsubmit={saveSchedule}>
          <section class="scheduled-task-group" data-schedule-section="fires">
            <h2>Fires</h2>
            <div class="scheduled-task-values">
              <input
                class="scheduled-task-quiet-control"
                type="time"
                aria-label="Local time"
                bind:value={draft.local_time}
                data-input="local-time"
              />
              <span class="scheduled-task-separator" aria-hidden="true">·</span>
              <select
                class="scheduled-task-quiet-control"
                aria-label="Cadence"
                value={draft.cadence}
                onchange={setCadence}
                data-input="cadence"
              >
                {#each cadenceOptions as option}
                  <option value={option.value}>{option.label}</option>
                {/each}
              </select>
              <span class="scheduled-task-separator" aria-hidden="true">·</span>
              <input
                class="scheduled-task-quiet-control"
                type="date"
                aria-label="Due date"
                bind:value={draft.deadline}
                data-input="deadline"
              />
            </div>
          </section>

          <section class="scheduled-task-group" data-schedule-section="worker">
            <h2>Worker</h2>
            <div class="scheduled-task-values">
              <select
                class="scheduled-task-quiet-control"
                aria-label="Worker type"
                value={draft.worker_type}
                onchange={setWorkerType}
                data-input="worker-type"
              >
                {#each workerTypes.data?.worker_types || [] as workerType}
                  <option value={workerType.worker_type}>{workerType.label}</option>
                {/each}
              </select>
              <span class="scheduled-task-separator" aria-hidden="true">·</span>
              <select
                class="scheduled-task-quiet-control"
                aria-label="Priority"
                value={draft.priority}
                onchange={setPriority}
                data-input="priority"
              >
                {#each priorities as priority}
                  <option value={priority}>{priority}</option>
                {/each}
              </select>
            </div>
          </section>

          <section class="scheduled-task-group" data-schedule-section="placement">
            <h2>Placement</h2>
            <div class="scheduled-task-values">
              <select
                class="scheduled-task-quiet-control"
                aria-label="Placement"
                value={draft.placement_mode}
                onchange={setPlacement}
                data-input="placement"
              >
                {#each placementOptions as option}
                  <option value={option.value}>{option.label}</option>
                {/each}
              </select>
              <span class="scheduled-task-separator" aria-hidden="true">·</span>
              <select
                  class="scheduled-task-quiet-control"
                  aria-label="Project"
                  value={draft.project_id || ""}
                  onchange={setProject}
                  data-input="project"
                >
                  <option value="">No project</option>
                  {#each projects.data?.projects || [] as project}
                    <option value={project.id}>{project.name}</option>
                  {/each}
              </select>
              {#if draft.placement_mode === "current_sprint"}
                <span class="scheduled-task-separator" aria-hidden="true">·</span>
                <select class="scheduled-task-quiet-control" aria-label="Sprint" value={draft.sprint_id || ""} onchange={setSprint} data-input="sprint">
                  <option value="">Current Sprint</option>
                  {#each sprintSummaries.data?.sprints || [] as sprint}<option value={sprint.id}>{sprint.name}</option>{/each}
                </select>
              {/if}
              <span class="scheduled-task-separator" aria-hidden="true">·</span>
              <select class="scheduled-task-quiet-control" aria-label="Outcome" value={draft.sprint_item_id || ""} onchange={setSprintItem} data-input="sprint-item">
                <option value="">No outcome</option>
                {#each (sprintItems.data?.items || []).filter((item) => !draft.project_id || item.project_id === draft.project_id) as item}<option value={item.id}>{item.title}</option>{/each}
              </select>
            </div>
          </section>

          <section class="scheduled-task-group" data-schedule-section="kickoff">
            <h2>Kickoff</h2>
            <div class="ticket-recap-inner scheduled-task-kickoff" data-input="kickoff-note">
              <InlineEdit
                className="scheduled-task-kickoff-edit"
                value={draft.kickoff_note}
                markdown
                multiline
                placeholder="Context passed into each created Ticket…"
                ariaLabel="Kickoff"
                onSave={(raw) => saveDraftField("kickoff_note", raw)}
              />
            </div>
          </section>

          <Disclosure
            variant="scheduled-task-advanced"
            title="Advanced Ticket settings"
            chevron="none"
            data-schedule-advanced
          >
            <div class="scheduled-task-advanced-grid">
              <label>
                <span>Employee backend</span>
                <input
                  class="scheduled-task-advanced-control"
                  type="text"
                  placeholder="inherit"
                  bind:value={draft.employee_backend}
                  data-input="employee-backend"
                />
              </label>
              <label>
                <span>Launch model</span>
                <input
                  class="scheduled-task-advanced-control"
                  type="text"
                  placeholder="inherit"
                  bind:value={draft.employee_launch_model}
                  data-input="employee-launch-model"
                />
              </label>
              <label>
                <span>Blocking ticket IDs</span>
                <input
                  class="scheduled-task-advanced-control"
                  type="text"
                  placeholder="comma-separated, optional"
                  bind:value={draft.blocked_by_ticket_ids}
                  data-input="blocked-by"
                />
              </label>
            </div>
          </Disclosure>

          {#if formError}
            <p class="scheduled-form-error" role="alert" data-schedule-error>{formError}</p>
          {/if}
          <div class="scheduled-task-form-actions">
            {#if !editingId}
              <Button variant="quiet" disabled={saving} onclick={() => closeForm()} data-close-schedule>
                Cancel
              </Button>
            {/if}
            <Button
              variant="primary"
              type="submit"
              disabled={!canSubmit}
              data-commit-schedule
            >
              {saving ? "Saving…" : editingId ? "Save changes" : "Add schedule"}
            </Button>
          </div>
        </form>
      </section>
    {:else}
      <ScreenHeader title="Scheduled tasks">
        {#snippet meta()}
          <Button variant="primary" onclick={() => openCreate()} data-create-schedule>Add</Button>
        {/snippet}
      </ScreenHeader>

      {#if toggleError}<ErrorLine error={toggleError} />{/if}

      <div class="scheduled-task-list" data-scheduled-tasks>
        <ResourceState
          error={schedules.error}
          loading={schedules.isFetching}
          hasData={Boolean(schedules.data)}
          loadingText="Loading scheduled tasks..."
        >
          {#if !(schedules.data?.schedules || []).length}
            <div class="scheduled-task-empty" data-schedule-empty>
              No scheduled tasks yet.<br />Add one to create a durable schedule.
            </div>
          {:else}
            {#each schedules.data?.schedules || [] as schedule (schedule.id)}
              <article class:scheduled-task-row--off={!schedule.enabled} class="scheduled-task-row" data-schedule-id={schedule.id}>
                <button class="scheduled-task-row-title" type="button" onclick={() => openEdit(schedule)} data-schedule-title>
                  {schedule.title}
                </button>
                <button
                  class:scheduled-task-switch--on={schedule.enabled}
                  class="scheduled-task-switch"
                  type="button"
                  role="switch"
                  aria-checked={schedule.enabled}
                  aria-label={schedule.enabled ? "Disable schedule" : "Enable schedule"}
                  disabled={togglingId === schedule.id}
                  data-schedule-toggle={schedule.id}
                  onclick={(event) => void toggleEnabled(schedule, event)}
                ></button>
              </article>
            {/each}
          {/if}
        </ResourceState>
      </div>
    {/if}
  </div>
</section>
