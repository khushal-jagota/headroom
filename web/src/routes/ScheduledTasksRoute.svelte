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
  import { labelize } from "../lib/ui";
  import type {
    Priority,
    ScheduleCadence,
    SchedulePlacementMode,
    ScheduledTask
  } from "../lib/types";
  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import Pill from "../components/Pill.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import ScreenHeader from "../components/ScreenHeader.svelte";

  const schedules = createQuery(() => queries.schedules());
  const projects = createQuery(() => queries.projects());
  const sprintItems = createQuery(() => queries.sprintItems());
  const workerTypes = createQuery(() => queries.workerTypeManifests());

  const cadenceOptions: Array<{ value: ScheduleCadence; label: string }> = [
    { value: "every_planning_day", label: "Every planning day" },
    { value: "current_sprint_final_day", label: "Current sprint final day" }
  ];
  const placementOptions: Array<{ value: SchedulePlacementMode; label: string }> = [
    { value: "current_sprint", label: "Current sprint" },
    { value: "backlog", label: "Backlog" },
    { value: "sprint_item", label: "Specific sprint item" }
  ];
  const priorities: Priority[] = ["P0", "P1", "P2", "P3"];

  let formOpen = $state(false);
  let editingId = $state<string | null>(null);
  let draft = $state<ScheduledTaskForm>(blankScheduledTaskForm());
  let saveError = $state<unknown>(null);
  let formError = $state<string | null>(null);
  let saving = $state(false);

  let scheduleCount = $derived(schedules.data?.schedules.length || 0);
  let editorTitle = $derived(editingId ? "Edit scheduled task" : "New scheduled task");
  let canSubmit = $derived(!saving);
  let catalogueError = $derived(
    [projects.error, sprintItems.error, workerTypes.error].find((error) => error != null)
  );

  function workerLabel(workerType: string): string {
    return (
      workerTypes.data?.worker_types.find((entry) => entry.worker_type === workerType)?.label ||
      labelize(workerType)
    );
  }

  function projectLabel(projectId: string | null): string | null {
    if (!projectId) return null;
    return projects.data?.projects.find((project) => project.id === projectId)?.name || projectId;
  }

  function sprintItemLabel(itemId: string | null): string | null {
    if (!itemId) return null;
    return sprintItems.data?.items.find((item) => item.id === itemId)?.title || itemId;
  }

  function cadenceLabel(cadence: ScheduleCadence): string {
    return cadenceOptions.find((option) => option.value === cadence)?.label || labelize(cadence);
  }

  function placementLabel(schedule: ScheduledTask): string {
    if (schedule.placement_mode === "sprint_item") {
      return `Sprint item · ${sprintItemLabel(schedule.sprint_item_id) || "not selected"}`;
    }
    return placementOptions.find((option) => option.value === schedule.placement_mode)?.label ||
      labelize(schedule.placement_mode);
  }

  function openCreate(): void {
    editingId = null;
    draft = blankScheduledTaskForm(workerTypes.data?.worker_types[0]?.worker_type || "coding");
    saveError = null;
    formError = null;
    formOpen = true;
  }

  function openEdit(schedule: ScheduledTask): void {
    editingId = schedule.id;
    draft = scheduledTaskFormFromSchedule(schedule);
    saveError = null;
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
    draft.project_id = selectValue(event) || null;
  }

  function setPlacement(event: Event): void {
    draft.placement_mode = selectValue(event) as SchedulePlacementMode;
    if (draft.placement_mode !== "sprint_item") draft.sprint_item_id = null;
  }

  function setSprintItem(event: Event): void {
    draft.sprint_item_id = selectValue(event) || null;
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
    <ScreenHeader title="Scheduled tasks">
      {#snippet meta()}
        <Pill keyLabel="configured">{scheduleCount}</Pill>
        <Button variant="primary" onclick={() => openCreate()} data-create-schedule>
          Add schedule
        </Button>
      {/snippet}
    </ScreenHeader>

    <p class="scheduled-tasks-intro">
      Durable schedules create ordinary planning Tickets at their configured local time.
      Adjust the timing or Ticket template here; the scheduler keeps using the saved definition.
    </p>

    {#if formOpen}
      <section class="scheduled-task-editor" data-schedule-editor>
        <div class="scheduled-task-editor-head">
          <div>
            <div class="scheduled-task-eyebrow">Schedule configuration</div>
            <h2>{editorTitle}</h2>
          </div>
          <Button variant="quiet" disabled={saving} onclick={() => closeForm()} data-close-schedule>
            Cancel
          </Button>
        </div>

        {#if saveError}<ErrorLine error={saveError} />{/if}
        {#if catalogueError}<ErrorLine error={catalogueError} />{/if}

        <form class="scheduled-task-form" onsubmit={saveSchedule}>
          <div class="scheduled-task-form-grid">
            <label class="scheduled-field scheduled-field--wide">
              <span>Ticket title</span>
              <input
                class="scheduled-control"
                type="text"
                placeholder="What should the scheduled Ticket be called?"
                bind:value={draft.title}
                data-input="title"
              />
            </label>

            <label class="scheduled-field">
              <span>Local time</span>
              <input class="scheduled-control" type="time" bind:value={draft.local_time} data-input="local-time" />
            </label>

            <label class="scheduled-field">
              <span>Cadence</span>
              <select class="scheduled-control" value={draft.cadence} onchange={setCadence} data-input="cadence">
                {#each cadenceOptions as option}
                  <option value={option.value}>{option.label}</option>
                {/each}
              </select>
            </label>

            <label class="scheduled-field">
              <span>Worker type</span>
              <select class="scheduled-control" value={draft.worker_type} onchange={setWorkerType} data-input="worker-type">
                {#each workerTypes.data?.worker_types || [] as workerType}
                  <option value={workerType.worker_type}>{workerType.label}</option>
                {/each}
              </select>
            </label>

            <label class="scheduled-field">
              <span>Priority</span>
              <select class="scheduled-control" value={draft.priority} onchange={setPriority} data-input="priority">
                {#each priorities as priority}
                  <option value={priority}>{priority}</option>
                {/each}
              </select>
            </label>

            <label class="scheduled-field">
              <span>Project</span>
              <select class="scheduled-control" value={draft.project_id || ""} onchange={setProject} data-input="project">
                <option value="">No project</option>
                {#each projects.data?.projects || [] as project}
                  <option value={project.id}>{project.name}</option>
                {/each}
              </select>
            </label>

            <label class="scheduled-field">
              <span>Placement</span>
              <select class="scheduled-control" value={draft.placement_mode} onchange={setPlacement} data-input="placement">
                {#each placementOptions as option}
                  <option value={option.value}>{option.label}</option>
                {/each}
              </select>
            </label>

            {#if draft.placement_mode === "sprint_item"}
              <label class="scheduled-field scheduled-field--wide">
                <span>Sprint item</span>
                <select class="scheduled-control" value={draft.sprint_item_id || ""} onchange={setSprintItem} data-input="sprint-item">
                  <option value="">Choose a sprint item</option>
                  {#each sprintItems.data?.items || [] as item}
                    <option value={item.id}>{item.title} · {item.project}</option>
                  {/each}
                </select>
              </label>
            {/if}

            <label class="scheduled-field">
              <span>Deadline</span>
              <input class="scheduled-control" type="date" bind:value={draft.deadline} data-input="deadline" />
            </label>

            <label class="scheduled-field scheduled-field--toggle">
              <span>Schedule status</span>
              <span class="scheduled-toggle">
                <input type="checkbox" bind:checked={draft.enabled} data-input="enabled" />
                <span>{draft.enabled ? "Enabled" : "Disabled"}</span>
              </span>
            </label>

            <label class="scheduled-field scheduled-field--wide">
              <span>Kickoff note</span>
              <textarea
                class="scheduled-control scheduled-control--textarea"
                rows="3"
                placeholder="Context passed into each created Ticket — optional"
                bind:value={draft.kickoff_note}
                data-input="kickoff-note"
              ></textarea>
            </label>
          </div>

          <details class="scheduled-task-advanced">
            <summary>Advanced Ticket settings</summary>
            <div class="scheduled-task-form-grid">
              <label class="scheduled-field">
                <span>Employee backend override</span>
                <input class="scheduled-control" type="text" placeholder="optional" bind:value={draft.employee_backend} data-input="employee-backend" />
              </label>
              <label class="scheduled-field">
                <span>Launch model override</span>
                <input class="scheduled-control" type="text" placeholder="optional" bind:value={draft.employee_launch_model} data-input="employee-launch-model" />
              </label>
              <label class="scheduled-field scheduled-field--wide">
                <span>Blocking Ticket IDs</span>
                <input class="scheduled-control" type="text" placeholder="comma-separated, optional" bind:value={draft.blocked_by_ticket_ids} data-input="blocked-by" />
              </label>
            </div>
          </details>

          {#if formError}<p class="scheduled-form-error" role="alert">{formError}</p>{/if}
          <div class="scheduled-task-form-actions">
            <Button variant="quiet" disabled={saving} onclick={() => closeForm()}>Cancel</Button>
            <button class="button button--primary" type="submit" disabled={!canSubmit} data-commit-schedule>
              {saving ? "Saving…" : editingId ? "Save changes" : "Add schedule"}
            </button>
          </div>
        </form>
      </section>
    {/if}

    <div class="scheduled-task-list" data-scheduled-tasks>
      <ResourceState
        error={schedules.error}
        loading={schedules.isFetching}
        hasData={Boolean(schedules.data)}
        loadingText="Loading scheduled tasks..."
      >
        {#if !(schedules.data?.schedules || []).length}
          <div class="quiet-line">No scheduled tasks yet. Add one above to create a durable schedule.</div>
        {:else}
          {#each schedules.data?.schedules || [] as schedule}
            <article class="scheduled-task-card" data-schedule-id={schedule.id}>
              <header class="scheduled-task-card-head">
                <div class="scheduled-task-card-title">
                  <h2>{schedule.title}</h2>
                  <div class="scheduled-task-card-meta">
                    <span class="scheduled-task-status" data-enabled={schedule.enabled ? "true" : "false"}>
                      {schedule.enabled ? "Enabled" : "Disabled"}
                    </span>
                    <Chip variant="priority" value={schedule.priority} />
                    <span>{workerLabel(schedule.worker_type)}</span>
                  </div>
                </div>
                <Button
                  variant="quiet"
                  onclick={() => openEdit(schedule)}
                  data-edit-schedule={schedule.id}
                >
                  Edit
                </Button>
              </header>

              <div class="scheduled-task-timing">
                <span class="scheduled-task-time">{schedule.local_time}</span>
                <span>{cadenceLabel(schedule.cadence)}</span>
                <span>{placementLabel(schedule)}</span>
              </div>

              <div class="scheduled-task-template">
                <div class="scheduled-task-eyebrow">Ticket template</div>
                <div class="scheduled-task-template-facts">
                  {#if projectLabel(schedule.project_id)}
                    <Chip variant="project" value={projectLabel(schedule.project_id)} />
                  {/if}
                  {#if schedule.deadline}
                    <Chip variant="deadline" value={schedule.deadline} />
                  {/if}
                  {#if schedule.blocked_by_ticket_ids.length}
                    <Chip variant="blocked-by" value={`${schedule.blocked_by_ticket_ids.length} blocker(s)`} />
                  {/if}
                </div>
                {#if schedule.kickoff_note}
                  <p>{schedule.kickoff_note}</p>
                {/if}
              </div>
            </article>
          {/each}
        {/if}
      </ResourceState>
    </div>
  </div>
</section>
